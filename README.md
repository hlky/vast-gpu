# Vast GPU Codex Skills

Publishable Codex skills for Vast.ai GPU workflows.

## Skills

- `vast-gpu-selection`: find the best Vast.ai GPU offer by bandwidth-aware effective cost instead of raw `dph_total`.
- `vast-codex-remote`: create or attach to a Vast.ai instance, bootstrap it for Codex remote SSH work, clone a repository into `/workspace`, and register the SSH alias/project locally.

## Install

Clone the repository, then copy either skill folder into your Codex skills directory:

PowerShell:

```powershell
git clone https://github.com/hlky/vast-gpu.git
Copy-Item -Recurse ./vast-gpu/vast-gpu-selection $HOME/.codex/skills/
Copy-Item -Recurse ./vast-gpu/vast-codex-remote $HOME/.codex/skills/
```

Linux/bash:

```bash
git clone https://github.com/hlky/vast-gpu.git
mkdir -p "$HOME/.codex/skills"
cp -R ./vast-gpu/vast-gpu-selection "$HOME/.codex/skills/"
cp -R ./vast-gpu/vast-codex-remote "$HOME/.codex/skills/"
```

The helper scripts expect the `vastai` Python package:

```sh
python -m pip install vastai
```

Configure your Vast.ai API key locally. Do not paste API keys into chat:

```powershell
$dir = "$HOME/.config/vastai"
New-Item -ItemType Directory -Force $dir | Out-Null
Set-Content -NoNewline -Path "$dir/vast_api_key" -Value "YOUR_API_KEY_HERE"
```

Linux/bash:

```bash
mkdir -p "$HOME/.config/vastai"
echo -n "YOUR_API_KEY_HERE" > "$HOME/.config/vastai/vast_api_key"
chmod 600 "$HOME/.config/vastai/vast_api_key"
```

## How The Skills Work Together

`vast-codex-remote` uses the same bandwidth-aware offer ranking as `vast-gpu-selection` when you pass `--target`. To pin a result from `vast-gpu-selection`, pass that offer to `vast-codex-remote` with `--offer-id`.

## Examples

```text
Use $vast-gpu-selection to find the best RTX_4090 Vast.ai offer with runtime_hours=8, download_gb=500, upload_gb=50, storage_gb=100.
```

```text
Use $vast-codex-remote to choose the best RTX_4090 offer with the same bandwidth-aware pricing defaults, create a Codex remote named project-4090, clone https://github.com/owner/project.git, and use 80GB disk.
```

```text
Use $vast-codex-remote with offer_id=12345678 to create a Codex remote from the offer selected by $vast-gpu-selection.
```
