---
name: vast-gpu-selection
description: Find and compare Vast.ai GPU offers by bandwidth-aware effective cost. Use when the user asks for the best or cheapest Vast.ai offer for a target GPU, especially when runtime, storage, download, upload, bandwidth pricing, or total estimated cost should affect ranking rather than raw dph_total alone.
---

# Vast GPU Selection

## Workflow

Prefer the Python SDK path. Use the bundled script when the user asks to find the best offer, compare candidates, or demonstrate bandwidth-aware pricing:

```powershell
python "<skill_dir>\\scripts\\select_vast_gpu.py" --target RTX_4090
```

Pass user-provided assumptions when available:

```powershell
python "<skill_dir>\\scripts\\select_vast_gpu.py" `
  --target H100_SXM `
  --runtime-hours 24 `
  --download-gb 200 `
  --upload-gb 500 `
  --storage-gb 100
```

Use these defaults when the user omits values, and state them in the response:

- `runtime_hours`: `8`
- `download_gb`: `500`
- `upload_gb`: `50`
- `storage_gb`: `100`
- `num_gpus`: `1`
- `reliability`: `0.95`
- `limit`: `50`

Normalize common target names before searching:

- `4090` -> `RTX_4090`
- `3090` -> `RTX_3090`
- `5090` -> `RTX_5090`
- Replace spaces with underscores for other model names, for example `H100 SXM` -> `H100_SXM`.

## Ranking

Rank by bandwidth-aware effective cost, not `dph_total` alone:

```text
total_estimated_cost =
  dph_total * runtime_hours
  + download_gb * inet_down_cost
  + upload_gb * inet_up_cost

effective_dph_total = total_estimated_cost / runtime_hours
```

Interpret transfer directions as:

- `download_gb`: data transferred from the internet into the instance, using `inet_down_cost`.
- `upload_gb`: data transferred out of the instance, using `inet_up_cost`.

Exclude offers missing required bandwidth prices whenever the corresponding transfer amount is nonzero. Do not silently treat missing bandwidth prices as free.

## Output

Show a compact result with:

- offer id
- GPU name
- location
- base $/hr
- download $/GB
- upload $/GB
- bandwidth cost
- total estimated cost
- effective $/hr
- whether bandwidth changed the winner compared with raw `dph_total`

If no offers are found, relax filters in this order: remove `direct_port_count`, lower reliability, increase `limit`, then broaden the GPU target only if the user asked for alternatives.

Do not create or launch an instance unless the user explicitly asks.

## Fallback

If the script cannot import `vastai`, install the package before retrying:

```powershell
python -m pip install vastai
```

If the API key is missing, ask the user to configure it locally. Do not ask them to paste the key in chat.
