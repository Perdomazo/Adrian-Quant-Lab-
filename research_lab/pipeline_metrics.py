from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_lab.run_manager import atomic_write_json


def load_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def update_metrics(
    manifests: list[Path],
    stage: str | None,
    seconds: float | None,
    total_seconds: float | None,
    peak_memory_mb: float | None,
) -> None:
    for manifest in manifests:
        payload = load_payload(manifest)
        if stage and seconds is not None:
            payload[f"{stage}_seconds"] = round(float(seconds), 3)
        if total_seconds is not None:
            payload["total_seconds"] = round(float(total_seconds), 3)
        if peak_memory_mb is not None:
            current = float(payload.get("peak_memory_mb") or 0.0)
            payload["peak_memory_mb"] = round(max(current, float(peak_memory_mb)), 3)
        atomic_write_json(manifest, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update pipeline timing metrics in manifests")
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--stage", default=None)
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--total-seconds", type=float, default=None)
    parser.add_argument("--peak-memory-mb", type=float, default=None)
    args = parser.parse_args()

    update_metrics(
        args.manifest,
        args.stage,
        args.seconds,
        args.total_seconds,
        args.peak_memory_mb,
    )


if __name__ == "__main__":
    main()
