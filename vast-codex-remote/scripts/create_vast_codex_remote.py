#!/usr/bin/env python3
"""Create a Vast.ai instance, add an SSH alias for Codex, bootstrap tools, and clone a repo."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


DEFAULT_IMAGE = "vastai/pytorch:@vastai-automatic-tag"
DEFAULT_IDENTITY = "~/.ssh/id_ed25519"
DEFAULT_PUBLIC_KEY = "~/.ssh/id_ed25519.pub"
DEFAULT_DISK_GB = 80


def ensure_local_vastai_import() -> None:
    local_skills = Path.home() / ".agents" / "skills"
    if local_skills.exists():
        sys.path.insert(0, str(local_skills))


ensure_local_vastai_import()

from vastai import VastAI  # noqa: E402


COMMON_TARGETS = {
    "3090": "RTX_3090",
    "4090": "RTX_4090",
    "5090": "RTX_5090",
}


def run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, text=True, capture_output=capture, check=check)


def normalize_target(target: str) -> str:
    cleaned = target.strip().replace(" ", "_")
    return COMMON_TARGETS.get(cleaned.lower(), cleaned)


def normalize_repo(repo: str) -> tuple[str, str]:
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        repo = f"https://github.com/{repo}.git"
    name = repo.rstrip("/").removesuffix(".git").split("/")[-1]
    if not name:
        raise SystemExit(f"Could not derive repository name from {repo!r}")
    return repo, re.sub(r"[^A-Za-z0-9._-]+", "-", name)


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return value or "vast-codex"


def ensure_vast_api_key(args: argparse.Namespace) -> None:
    if args.api_key:
        return
    try:
        VastAI(quiet=True).show_user()
        return
    except Exception:
        pass

    if not args.prompt_api_key:
        raise SystemExit(
            "No Vast.ai API key found. Save one locally or rerun with --prompt-api-key. "
            "Do not paste API keys into chat."
        )

    api_key = getpass.getpass("Vast.ai API key: ").strip()
    if not api_key:
        raise SystemExit("No Vast.ai API key provided.")
    target = Path.home() / ".config" / "vastai" / "vast_api_key"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(api_key, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def ensure_vast_ssh_key(vast: VastAI, public_key_path: str, *, skip: bool) -> None:
    if skip:
        return
    key_path = Path(os.path.expanduser(public_key_path))
    if not key_path.exists():
        print(
            f"Warning: SSH public key not found at {key_path}. "
            "Create/register a Vast SSH key before creating an SSH instance.",
            file=sys.stderr,
        )
        return

    public_key = key_path.read_text(encoding="utf-8").strip()
    try:
        keys = vast.show_ssh_keys()
        if any(public_key in json.dumps(key) for key in keys):
            return
        vast.create_ssh_key(public_key)
        print(f"Registered Vast.ai SSH public key from {key_path}")
    except Exception as exc:
        print(f"Warning: could not verify/register Vast SSH key: {exc}", file=sys.stderr)


def choose_offer(vast: VastAI, args: argparse.Namespace) -> dict[str, Any]:
    if args.offer_id:
        return {"id": int(args.offer_id), "gpu_name": args.target or "offer", "effective_dph_total": None}
    if not args.target:
        raise SystemExit("Pass --target or --offer-id.")

    target = normalize_target(args.target)
    query = (
        f"gpu_name={target} num_gpus={args.num_gpus} reliability>{args.reliability} "
        "rentable=true rented=false"
    )
    if args.direct_port_count is not None:
        query += f" direct_port_count>={args.direct_port_count}"

    ranked = vast.search_offers_effective_cost(
        query=query,
        runtime_hours=args.runtime_hours,
        download_gb=args.download_gb,
        upload_gb=args.upload_gb,
        limit=args.limit,
        storage=args.storage_gb,
        order="dph_total",
    )
    if not ranked:
        raise SystemExit(f"No Vast offers found for {target}.")

    best = ranked[0]
    print(
        "Selected offer "
        f"{best['id']} {best.get('gpu_name')} base=${best.get('dph_total'):.4f}/hr "
        f"effective=${best.get('effective_dph_total'):.4f}/hr total=${best.get('total_estimated_cost'):.2f}",
        flush=True,
    )
    return best


def create_instance(vast: VastAI, offer: dict[str, Any], args: argparse.Namespace) -> int:
    if args.instance_id:
        return int(args.instance_id)

    runtype = "ssh_direc ssh_proxy" if args.direct else "ssh_proxy"
    result = vast.create_instance(
        id=int(offer["id"]),
        image=args.image,
        disk=args.disk_gb,
        label=args.name,
        env={},
        runtype=runtype,
        cancel_unavail=args.cancel_unavail,
    )
    instance_id = result.get("new_contract") or result.get("id") or result.get("instance_id")
    if not instance_id:
        raise SystemExit(f"Created instance but could not parse instance id from response: {result}")
    print(f"Instance ID: {instance_id}")
    return int(instance_id)


def wait_for_instance(vast: VastAI, instance_id: int, *, timeout_seconds: int, interval_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last = {}
    bad_states = {"exited", "unknown", "offline"}
    while time.time() < deadline:
        inst = vast.show_instance(instance_id)
        last = inst
        status = inst.get("actual_status") or inst.get("status") or inst.get("intended_status")
        print(f"Instance {instance_id} status: {status}", flush=True)
        if status == "running":
            return inst
        if status in bad_states:
            raise SystemExit(f"Instance reached non-runnable status {status!r}: {inst.get('status_msg')}")
        time.sleep(interval_seconds)
    raise SystemExit(f"Timed out waiting for instance {instance_id}; last status: {last}")


def parse_vast_ssh_url(value: str) -> tuple[str, int]:
    parsed = urlparse(value)
    if parsed.scheme == "ssh" and parsed.hostname and parsed.port:
        user = parsed.username or "root"
        return f"{user}@{parsed.hostname}", int(parsed.port)
    match = re.search(r"ssh://(?P<userhost>[^:\s]+@[^:\s]+):(?P<port>\d+)", value)
    if match:
        return match.group("userhost"), int(match.group("port"))
    match = re.search(r"ssh\s+(?:-p\s+(?P<port1>\d+)\s+)?(?P<userhost>[\w.-]+@[\w.-]+)(?:\s+-p\s+(?P<port2>\d+))?", value)
    if match and (match.group("port1") or match.group("port2")):
        return match.group("userhost"), int(match.group("port1") or match.group("port2"))
    raise SystemExit(f"Could not parse Vast SSH URL: {value!r}")


def ssh_endpoint(vast: VastAI, instance_id: int, identity: str) -> tuple[str, int]:
    for _ in range(24):
        value = vast.ssh_url(instance_id)
        if value:
            return parse_vast_ssh_url(value)
        time.sleep(5)
    inst = vast.show_instance(instance_id)
    host = inst.get("ssh_host") or inst.get("public_ipaddr")
    port = inst.get("ssh_port")
    if host and port:
        return f"root@{host}", int(port)
    raise SystemExit(f"No SSH endpoint found for instance {instance_id}. Identity was {identity}.")


def remote_shell(userhost: str, port: int, identity: str, command: str, *, check: bool = True) -> None:
    ssh_cmd = [
        "ssh",
        "-i",
        os.path.expanduser(identity),
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        userhost,
        "bash -s",
    ]
    print("+ " + " ".join(ssh_cmd), flush=True)
    subprocess.run(ssh_cmd, input=command.replace("\r\n", "\n").encode("utf-8"), check=check)


def wait_for_ssh(userhost: str, port: int, identity: str, *, attempts: int = 36, wait_seconds: int = 10) -> None:
    test_cmd = [
        "ssh",
        "-i",
        os.path.expanduser(identity),
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        userhost,
        "true",
    ]
    for attempt in range(1, attempts + 1):
        result = subprocess.run(test_cmd, capture_output=True)
        if result.returncode == 0:
            return
        print(f"SSH not ready; retrying in {wait_seconds}s ({attempt}/{attempts}).", flush=True)
        time.sleep(wait_seconds)
    raise SystemExit(f"SSH did not become reachable for {userhost}:{port}.")


def copy_codex_auth(userhost: str, port: int, identity: str) -> None:
    auth_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
    if not auth_path.exists():
        raise SystemExit(f"Local Codex auth file not found: {auth_path}")
    remote_shell(userhost, port, identity, "mkdir -p /root/.codex && chmod 700 /root/.codex")
    run([
        "scp", "-i", os.path.expanduser(identity), "-P", str(port),
        "-o", "StrictHostKeyChecking=accept-new", str(auth_path),
        f"{userhost}:/root/.codex/auth.json",
    ], check=True, capture=False)
    remote_shell(userhost, port, identity, "chmod 600 /root/.codex/auth.json && codex login status")


def copy_github_auth(userhost: str, port: int, identity: str) -> None:
    token = (run(["gh", "auth", "token"], check=True).stdout or "").strip()
    if not token:
        raise SystemExit("Local GitHub CLI did not return a token.")
    ssh_cmd = [
        "ssh", "-i", os.path.expanduser(identity), "-p", str(port),
        "-o", "StrictHostKeyChecking=accept-new", userhost,
        "bash -lc 'mkdir -p /root/.config/gh && gh auth login --hostname github.com --with-token && gh auth status'",
    ]
    print("+ " + " ".join(ssh_cmd), flush=True)
    subprocess.run(ssh_cmd, text=True, input=token + "\n", check=True)


def copy_git_identity(userhost: str, port: int, identity: str, *, clone_path: str, scope: str) -> None:
    name = (run(["git", "config", "--global", "user.name"], check=False).stdout or "").strip()
    email = (run(["git", "config", "--global", "user.email"], check=False).stdout or "").strip()
    if not name or not email:
        raise SystemExit("Local git identity is incomplete.")
    git_config = "git config --global" if scope == "global" else f"git -C {shlex.quote(clone_path)} config"
    remote_shell(
        userhost,
        port,
        identity,
        f"{git_config} user.name {shlex.quote(name)}\n{git_config} user.email {shlex.quote(email)}\n{git_config} --get user.name\n{git_config} --get user.email\n",
    )


def bootstrap_command(repo_url: str, clone_path: str) -> str:
    return f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git gnupg lsb-release unzip wget openssh-client build-essential python3 python3-pip python3-venv pipx
if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
npm install -g @openai/codex
python3 -m pipx ensurepath || true
python3 -m pipx install vastai || python3 -m pip install --user vastai || true
curl -LsSf https://hf.co/cli/install.sh | bash || true
if ! command -v gh >/dev/null 2>&1; then
  mkdir -p -m 755 /etc/apt/keyrings
  wget -nv -O /tmp/githubcli-archive-keyring.gpg https://cli.github.com/packages/githubcli-archive-keyring.gpg
  cat /tmp/githubcli-archive-keyring.gpg > /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
  apt-get update && apt-get install -y gh
fi
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install --update
fi
mkdir -p /workspace
if [ ! -d {json.dumps(clone_path)}/.git ]; then
  rm -rf {json.dumps(clone_path)}
  git clone {json.dumps(repo_url)} {json.dumps(clone_path)}
fi
codex --version
git -C {json.dumps(clone_path)} status --short
""".strip()


def codex_base() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def ensure_codex_remote_connections_feature() -> None:
    path = codex_base() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"(?ms)^\[features\]\s*.*?^\s*remote_connections\s*=\s*true\s*$", text):
        return
    if re.search(r"(?m)^\[features\]\s*$", text):
        text = re.sub(r"(?m)^\[features\]\s*$", "[features]\nremote_connections = true", text, count=1)
    else:
        text = text.rstrip() + "\n\n[features]\nremote_connections = true\n"
    path.write_text(text, encoding="utf-8")
    print(f"Enabled Codex remote_connections feature in {path}")


def split_userhost(userhost: str) -> tuple[str, str]:
    if "@" not in userhost:
        return "root", userhost
    user, host = userhost.split("@", 1)
    return user or "root", host


def register_ssh_config_alias(alias: str, userhost: str, port: int, identity: str) -> None:
    user, host = split_userhost(userhost)
    path = Path.home() / ".ssh" / "config"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = (
        f"Host {alias}\n"
        f"  HostName {host}\n"
        f"  User {user}\n"
        f"  Port {port}\n"
        f"  IdentityFile {identity}\n"
        f"  StrictHostKeyChecking accept-new\n"
    )
    pattern = re.compile(rf"(?ms)^Host\s+{re.escape(alias)}\s*$.*?(?=^Host\s+|\Z)")
    text = pattern.sub(lambda _match: block, text).rstrip() + "\n" if pattern.search(text) else text.rstrip() + ("\n\n" if text.strip() else "") + block
    path.write_text(text, encoding="utf-8")
    print(f"Registered SSH config alias {alias!r} in {path}")


def codex_desktop_process_running() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Codex.exe", "/NH"], text=True, capture_output=True, check=False)
    return "Codex.exe" in (result.stdout or "")


def write_codex_state(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def upsert_remote_project(data: dict[str, Any], host_id: str, remote_path: str, label: str) -> str:
    normalized_path = posixpath.normpath(remote_path)
    projects = data.setdefault("remote-projects", [])
    project = None
    for existing in projects:
        if existing.get("hostId") == host_id and posixpath.normpath(str(existing.get("remotePath", ""))) == normalized_path:
            project = existing
            break
    if project is None:
        project = {"id": str(uuid.uuid4()), "hostId": host_id, "remotePath": normalized_path, "label": label}
        projects.insert(0, project)
    else:
        project["label"] = label or project.get("label") or posixpath.basename(normalized_path)
    order = data.setdefault("project-order", [])
    project_id = project["id"]
    data["project-order"] = [project_id, *[item for item in order if item != project_id]]
    data["active-remote-project-id"] = project_id
    data["selected-remote-host-id"] = host_id
    return str(project_id)


def seed_discovered_remote_alias(alias: str, remote_path: str, label: str, *, auto_connect: bool) -> None:
    path = codex_base() / ".codex-global-state.json"
    if not path.exists():
        raise SystemExit(f"Codex global state file not found: {path}")
    host_id = f"remote-ssh-discovered:{alias}"
    desktop_running = codex_desktop_process_running()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("remote-connection-auto-connect-by-host-id", {})[host_id] = bool(auto_connect)
    atoms = data.setdefault("electron-persisted-atom-state", {})
    atoms.setdefault("agent-mode-by-host-id", {})[host_id] = "full-access"
    upsert_remote_project(data, host_id, remote_path, label)
    write_codex_state(path, data)
    print(f"Seeded Codex discovered SSH host {alias!r} and project {label!r}")
    if desktop_running:
        print("Note: Codex Desktop is running; restart or refresh if the project does not appear.", file=sys.stderr)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", help="Vast GPU name, e.g. 4090, RTX_4090, H100_SXM.")
    parser.add_argument("--offer-id", type=int, help="Use a specific Vast offer ID instead of searching.")
    parser.add_argument("--instance-id", type=int, help="Use an existing Vast instance instead of creating one.")
    parser.add_argument("--repo", required=True, help="Git URL or GitHub owner/repo shorthand to clone.")
    parser.add_argument("--name", help="Instance label and SSH alias.")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--disk-gb", type=float, default=DEFAULT_DISK_GB)
    parser.add_argument("--storage-gb", type=float, default=100.0)
    parser.add_argument("--num-gpus", type=int, default=1)
    parser.add_argument("--runtime-hours", type=float, default=8.0)
    parser.add_argument("--download-gb", type=float, default=500.0)
    parser.add_argument("--upload-gb", type=float, default=50.0)
    parser.add_argument("--reliability", type=float, default=0.95)
    parser.add_argument("--direct-port-count", type=int, default=1)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_KEY)
    parser.add_argument("--api-key", help="Vast API key. Prefer local config or --prompt-api-key.")
    parser.add_argument("--prompt-api-key", action="store_true")
    parser.add_argument("--skip-ssh-key-register", action="store_true")
    parser.add_argument("--clone-path", help="Destination path on instance. Default: /workspace/<repo-name>.")
    parser.add_argument("--project-label", help="Codex remote project label. Default: repository directory name.")
    parser.add_argument("--direct", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cancel-unavail", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait-timeout", type=int, default=900)
    parser.add_argument("--wait-interval", type=int, default=15)
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--skip-ssh-config", action="store_true")
    parser.add_argument("--skip-feature-flag", action="store_true")
    parser.add_argument("--skip-remote-project", action="store_true")
    parser.add_argument("--auto-connect", action="store_true")
    parser.add_argument("--copy-codex-auth", action="store_true")
    parser.add_argument("--copy-gh-auth", action="store_true")
    parser.add_argument("--copy-git-identity", action="store_true")
    parser.add_argument("--git-identity-scope", choices=["global", "repo"], default="global")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    repo_url, repo_dir = normalize_repo(args.repo)
    target_label = normalize_target(args.target) if args.target else (f"offer-{args.offer_id}" if args.offer_id else "vast")
    args.name = slug(args.name or f"{repo_dir}-{target_label}")
    clone_path = args.clone_path or f"/workspace/{repo_dir}"

    ensure_vast_api_key(args)
    vast = VastAI(api_key=args.api_key, quiet=True)
    ensure_vast_ssh_key(vast, args.public_key, skip=args.skip_ssh_key_register)

    offer = {"id": None, "gpu_name": "existing"} if args.instance_id else choose_offer(vast, args)
    instance_id = create_instance(vast, offer, args)
    wait_for_instance(vast, instance_id, timeout_seconds=args.wait_timeout, interval_seconds=args.wait_interval)

    userhost, port = ssh_endpoint(vast, instance_id, args.identity)
    print(f"SSH: {userhost} port {port} identity {args.identity}")
    wait_for_ssh(userhost, port, args.identity)

    if not args.skip_feature_flag:
        ensure_codex_remote_connections_feature()
    if not args.skip_ssh_config:
        register_ssh_config_alias(args.name, userhost, port, args.identity)
    if not args.skip_bootstrap:
        remote_shell(userhost, port, args.identity, bootstrap_command(repo_url, clone_path))
    if args.copy_codex_auth:
        copy_codex_auth(userhost, port, args.identity)
    if args.copy_gh_auth:
        copy_github_auth(userhost, port, args.identity)
    if args.copy_git_identity:
        copy_git_identity(userhost, port, args.identity, clone_path=clone_path, scope=args.git_identity_scope)
    if not args.skip_remote_project:
        seed_discovered_remote_alias(args.name, clone_path, args.project_label or repo_dir, auto_connect=args.auto_connect)

    print(f"Ready: instance {instance_id}, ssh alias {args.name}, project {clone_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
