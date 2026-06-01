#!/usr/bin/env python
"""Select Vast.ai GPU offers by bandwidth-aware effective cost."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional


def ensure_local_vastai_import() -> None:
    """Allow this local skill to use the user's local Vast.ai SDK skill package."""
    local_skills = Path.home() / ".agents" / "skills"
    if local_skills.exists():
        sys.path.insert(0, str(local_skills))


ensure_local_vastai_import()

from vastai import VastAI  # noqa: E402
from vastai.pricing import estimate_offer_cost, rank_offers_by_effective_cost  # noqa: E402


COMMON_TARGETS = {
    "3090": "RTX_3090",
    "4090": "RTX_4090",
    "5090": "RTX_5090",
}


def normalize_target(target: str) -> str:
    cleaned = target.strip().replace(" ", "_")
    return COMMON_TARGETS.get(cleaned.lower(), cleaned)


def fmt_money(value: Any, places: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{places}f}"


def usable_offers(
    offers: Iterable[dict[str, Any]],
    *,
    download_gb: float,
    upload_gb: float,
) -> tuple[list[dict[str, Any]], int]:
    usable = []
    skipped = 0
    for offer in offers:
        missing_down = download_gb and offer.get("inet_down_cost") is None
        missing_up = upload_gb and offer.get("inet_up_cost") is None
        if missing_down or missing_up:
            skipped += 1
            continue
        usable.append(offer)
    return usable, skipped


def build_query(args: argparse.Namespace) -> str:
    parts = [
        f"gpu_name={normalize_target(args.target)}",
        f"num_gpus={args.num_gpus}",
        f"reliability>{args.reliability}",
        "rentable=true",
        "rented=false",
    ]
    if args.direct_port_count is not None:
        parts.append(f"direct_port_count>={args.direct_port_count}")
    return " ".join(parts)


def result_payload(args: argparse.Namespace) -> dict[str, Any]:
    vast = VastAI(quiet=True)
    query = build_query(args)
    offers = vast.search_offers(
        query=query,
        order="dph_total",
        limit=args.limit,
        storage=args.storage_gb,
    )
    usable, skipped = usable_offers(
        offers,
        download_gb=args.download_gb,
        upload_gb=args.upload_gb,
    )
    if not offers:
        return {
            "query": query,
            "assumptions": vars(args),
            "offers": 0,
            "skipped_missing_bandwidth": 0,
            "error": "No offers found",
        }
    if not usable:
        return {
            "query": query,
            "assumptions": vars(args),
            "offers": len(offers),
            "skipped_missing_bandwidth": skipped,
            "error": "No offers had the required bandwidth prices",
        }

    base = min(
        usable,
        key=lambda offer: (
            offer.get("dph_total") is None,
            offer.get("dph_total") or float("inf"),
        ),
    )
    base_estimate = estimate_offer_cost(
        base,
        runtime_hours=args.runtime_hours,
        download_gb=args.download_gb,
        upload_gb=args.upload_gb,
    )
    ranked = rank_offers_by_effective_cost(
        usable,
        runtime_hours=args.runtime_hours,
        download_gb=args.download_gb,
        upload_gb=args.upload_gb,
    )
    best = ranked[0]

    return {
        "query": query,
        "assumptions": vars(args),
        "offers": len(offers),
        "usable_offers": len(usable),
        "skipped_missing_bandwidth": skipped,
        "changed_winner": base.get("id") != best.get("id"),
        "raw_dph_winner": {**base, **base_estimate},
        "effective_cost_winner": best,
        "top_effective_offers": ranked[: args.top],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    assumptions = payload["assumptions"]
    lines = [
        (
            f"Assumptions: runtime={assumptions['runtime_hours']}h, "
            f"download={assumptions['download_gb']}GB, "
            f"upload={assumptions['upload_gb']}GB, "
            f"storage={assumptions['storage_gb']}GB"
        ),
        f"Query: `{payload['query']}`",
        "",
    ]

    if payload.get("error"):
        lines.append(payload["error"])
        return "\n".join(lines)

    lines.extend(
        [
            (
                f"Offers: {payload['offers']} "
                f"({payload['usable_offers']} usable, "
                f"{payload['skipped_missing_bandwidth']} skipped for missing bandwidth prices)"
            ),
            f"Bandwidth changed winner: {'yes' if payload['changed_winner'] else 'no'}",
            "",
            "| Pick | Offer | GPU | Location | Base $/hr | Down $/GB | Up $/GB | Bandwidth $ | Total $ | Effective $/hr |",
            "|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )

    rows = [
        ("Raw dph", payload["raw_dph_winner"]),
        ("Effective", payload["effective_cost_winner"]),
    ]
    for label, offer in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(offer.get("id", "-")),
                    str(offer.get("gpu_name", "-")),
                    str(offer.get("geolocation", "-")),
                    fmt_money(offer.get("dph_total")),
                    fmt_money(offer.get("inet_down_cost"), 6),
                    fmt_money(offer.get("inet_up_cost"), 6),
                    fmt_money(offer.get("bandwidth_cost"), 2),
                    fmt_money(offer.get("total_estimated_cost"), 2),
                    fmt_money(offer.get("effective_dph_total")),
                ]
            )
            + " |"
        )

    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="GPU model, e.g. 4090, RTX_4090, H100_SXM")
    parser.add_argument("--runtime-hours", type=float, default=8.0)
    parser.add_argument("--download-gb", type=float, default=500.0)
    parser.add_argument("--upload-gb", type=float, default=50.0)
    parser.add_argument("--storage-gb", type=float, default=100.0)
    parser.add_argument("--num-gpus", type=int, default=1)
    parser.add_argument("--reliability", type=float, default=0.95)
    parser.add_argument("--direct-port-count", type=int, default=1)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    args = parser.parse_args(argv)
    if args.runtime_hours <= 0:
        parser.error("--runtime-hours must be greater than 0")
    if args.download_gb < 0 or args.upload_gb < 0 or args.storage_gb < 0:
        parser.error("--download-gb, --upload-gb, and --storage-gb cannot be negative")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    payload = result_payload(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_markdown(payload))
    return 0 if not payload.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
