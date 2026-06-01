---
name: vast-codex-remote
description: Create and prepare Vast.ai GPU instances as Codex remote SSH connections. Use when the user asks to create or attach to a Vast.ai instance for Codex remote work, choose a GPU offer with bandwidth-aware pricing, install Codex CLI plus companion CLIs, register an SSH alias, and clone a Git or GitHub project onto the instance.
---

# Vast Codex Remote

## Overview

Provision a Vast.ai instance or attach to an existing one, make it usable from Codex Desktop as a remote SSH environment, install development CLIs inside the instance, and clone the requested project into `/workspace`.

Use `scripts/create_vast_codex_remote.py` for the main flow whenever possible. It keeps offer selection, instance creation, SSH config registration, Codex feature-flag setup, remote bootstrap, project clone, and best-effort app state seeding consistent.

## Inputs

Collect or infer these values before running:

- Target GPU name, such as `RTX_4090`, `4090`, `A100_SXM4`, or `H100_SXM`, unless the user gives `--offer-id`.
- Project repository URL or `owner/repo` GitHub shorthand.
- Instance/SSH alias name. Default to a slug based on the repo and GPU.
- Disk size. Default to `80` GB for Codex work.
- Docker image. Default to `vastai/pytorch:@vastai-automatic-tag`.
- Bandwidth assumptions for offer selection. Defaults match `$vast-gpu-selection`: 8 hours, 500 GB download, 50 GB upload, 100 GB storage.
- SSH key path. Default private key is `~/.ssh/id_ed25519`; default public key is `~/.ssh/id_ed25519.pub`.

Before creating a billable instance, make sure the user has clearly asked to create one. If the requested GPU is ambiguous, search offers first and show the candidate.

## Workflow

Create a fresh instance, choosing the offer by bandwidth-aware cost:

```powershell
python "<skill_dir>\scripts\create_vast_codex_remote.py" `
  --target RTX_4090 `
  --repo https://github.com/owner/project.git `
  --name project-4090 `
  --disk-gb 80
```

Use an explicit offer ID:

```powershell
python "<skill_dir>\scripts\create_vast_codex_remote.py" `
  --offer-id 12345678 `
  --repo owner/project `
  --name project-vast `
  --disk-gb 80
```

Attach to an existing Vast instance without recreating or bootstrapping:

```powershell
python "<skill_dir>\scripts\create_vast_codex_remote.py" `
  --instance-id 123456 `
  --repo owner/project `
  --name project-vast `
  --skip-bootstrap
```

Pre-authenticate Codex and GitHub CLI only when explicitly requested:

```powershell
python "<skill_dir>\scripts\create_vast_codex_remote.py" `
  --target RTX_4090 `
  --repo owner/project `
  --copy-codex-auth `
  --copy-gh-auth `
  --copy-git-identity
```

Auth flags copy usable credentials to the instance. Use them only for trusted instances. Never paste auth files or tokens into chat or command output.

## Offer Selection

Rank offers by bandwidth-aware effective cost, not `dph_total` alone:

```text
total_estimated_cost =
  dph_total * runtime_hours
  + download_gb * inet_down_cost
  + upload_gb * inet_up_cost

effective_dph_total = total_estimated_cost / runtime_hours
```

Exclude offers missing required bandwidth prices when transfer amounts are nonzero.

## Remote Bootstrap

Install these inside the instance:

- Node.js 20+ and npm.
- `codex` via `npm install -g @openai/codex`.
- `vastai` via `pipx install vastai` or `pip install --user vastai`.
- `hf` via the Hugging Face CLI installer.
- `gh` from the GitHub CLI apt repository on Debian/Ubuntu.
- `aws` from AWS CLI v2 installer.

Clone into `/workspace/<repo-name>` by default. Prefer HTTPS for public GitHub repos because it works before GitHub SSH keys are configured on the instance.

## Codex SSH Config

Codex discovers concrete SSH aliases from `~/.ssh/config`; pattern-only hosts are ignored. The helper writes aliases like:

```text
Host project-4090
  HostName ssh.vast.ai
  User root
  Port 40036
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
```

Ensure `~/.codex/config.toml` contains:

```toml
[features]
remote_connections = true
```

The current app uses discovered SSH host IDs shaped like:

```text
remote-ssh-discovered:<ssh-alias>
```

Best-effort state seeding may need an app refresh or restart because Codex Desktop can cache SSH config and state while running.

## Verification

After a run, verify the same SSH alias Codex Desktop will use:

```powershell
ssh project-4090 "bash -lc 'codex --version && test -d /workspace/project/.git'"
ssh project-4090 "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader"
```

## Cleanup

Stopping an instance preserves disk but ends GPU compute billing:

```powershell
vastai stop instance INSTANCE_ID
```

Destroy only when the user is sure:

```powershell
vastai destroy instance INSTANCE_ID -y
```

