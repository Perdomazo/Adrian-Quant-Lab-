from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

from research_lab.run_manager import atomic_write_json, short_git_commit, utc_now


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_paths(storage: Path, run_id: str) -> tuple[Path, Path]:
    results = storage / "results"
    return (
        results / "ingest_manifest.json",
        results / "ingest_runs" / run_id / "ingest_manifest.json",
    )


def base_manifest(root: Path, storage: Path, run_id: str, status: str) -> dict[str, Any]:
    quality = load_json(storage / "results" / "data_quality_report.json")
    return {
        "run_id": run_id,
        "status": status,
        "started_at": utc_now(),
        "completed_at": None,
        "git_commit": short_git_commit(root),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "data_quality_status": quality.get("status"),
        "expected_last_complete_candle": quality.get("expected_last_complete_candle"),
        "actual_last_available_candle": quality.get("actual_last_available_candle"),
        "max_lag_bars": quality.get("max_lag_bars"),
        "allowed_lag_bars": quality.get("allowed_lag_bars"),
    }


def write_manifest(root: Path, storage: Path, run_id: str, status: str, error: str | None) -> None:
    latest_path, run_path = manifest_paths(storage, run_id)
    current = load_json(run_path) or base_manifest(root, storage, run_id, status)
    quality = load_json(storage / "results" / "data_quality_report.json")
    current["status"] = status
    if status in {"success", "failed"}:
        current["completed_at"] = utc_now()
    if error:
        current["error"] = error
    current["data_quality_status"] = quality.get("status")
    current["expected_last_complete_candle"] = quality.get("expected_last_complete_candle")
    current["actual_last_available_candle"] = quality.get("actual_last_available_candle")
    current["max_lag_bars"] = quality.get("max_lag_bars")
    current["allowed_lag_bars"] = quality.get("allowed_lag_bars")
    atomic_write_json(run_path, current)
    atomic_write_json(latest_path, current)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily ingest manifest manager")
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", required=True, choices=["running", "success", "failed"])
    parser.add_argument("--error", default=None)
    args = parser.parse_args()

    write_manifest(args.root.resolve(), args.storage, args.run_id, args.status, args.error)


if __name__ == "__main__":
    main()
