from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RUN_FILES = [
    "edge_summary.parquet",
    "edge_trades.parquet",
    "edge_signals.parquet",
    "walk_forward_summary.parquet",
    "season_summary.parquet",
    "run_history.parquet",
    "data_quality_report.json",
    "account_summary.parquet",
    "account_trades.parquet",
    "account_equity.parquet",
    "account_rejections.parquet",
    "account_validation.json",
    "account_oos_summary.parquet",
    "account_oos_trades.parquet",
    "account_oos_equity.parquet",
    "account_oos_rejections.parquet",
    "account_oos_aggregate.parquet",
    "account_oos_validation.json",
    "account_candidate_summary.parquet",
    "account_candidate_decision.json",
    "deep_validation_summary.parquet",
    "deep_validation_manifest.json",
]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short_git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def full_git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def text_file_value(path: Path) -> str | None:
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def command_version(root: Path, command: list[str]) -> str | None:
    try:
        return subprocess.check_output(
            command, cwd=root, stderr=subprocess.STDOUT, text=True, timeout=10
        ).strip()
    except Exception:
        return None


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_many(paths: list[Path]) -> str | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    h = hashlib.sha256()
    for path in sorted(existing):
        h.update(path.name.encode("utf-8"))
        file_hash = sha256_file(path)
        if file_hash:
            h.update(file_hash.encode("utf-8"))
    return h.hexdigest()


def json_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version")
    except Exception:
        return None


def file_exists_hash(path: Path) -> str | None:
    return sha256_file(path) if path.exists() else None


def source_tree_hash(root: Path) -> str | None:
    source_root = root / "research_lab"
    if not source_root.exists():
        return None
    suffixes = {".py", ".sh", ".json", ".service", ".timer", ".md"}
    paths = [
        path
        for path in source_root.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and "storage" not in path.parts
        and "audit_integrity" not in path.parts
        and "__pycache__" not in path.parts
    ]
    if not paths:
        return None
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(str(path.relative_to(root)).encode("utf-8"))
        file_hash = sha256_file(path)
        if file_hash:
            h.update(file_hash.encode("utf-8"))
    return h.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def new_run_id(root: Path) -> str:
    commit = short_git_commit(root) or "nogit"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{commit}-{uuid.uuid4().hex[:6]}"


def manifest_path(storage: Path, run_id: str) -> Path:
    return storage / "results" / "runs" / run_id / "run_manifest.json"


def load_manifest(storage: Path, run_id: str) -> dict[str, Any]:
    path = manifest_path(storage, run_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def base_manifest(root: Path, storage: Path, run_id: str, status: str) -> dict[str, Any]:
    req = root / "research_lab" / "requirements.txt"
    ohlcv_manifest = storage / "ohlcv_manifest.parquet"
    features_manifest = storage / "features_manifest.parquet"
    data_quality = storage / "results" / "data_quality_report.json"
    dq = json.loads(data_quality.read_text(encoding="utf-8")) if data_quality.exists() else {}
    decision_rules = root / "research_lab" / "config" / "decision_rules.json"
    promotion_rules = root / "research_lab" / "config" / "promotion_rules.json"
    account_rules = root / "research_lab" / "config" / "account_rules.json"
    account_decision_rules = root / "research_lab" / "config" / "account_decision_rules.json"
    pair_universe = root / "research_lab" / "config" / "pair_universe.json"
    deployed_commit = root / "research_lab" / "DEPLOYED_COMMIT"
    deployment_hash = root / "research_lab" / "DEPLOYMENT_PACKAGE_SHA256"
    return {
        "run_id": run_id,
        "status": status,
        "pipeline_mode": os.environ.get("PIPELINE_MODE", "research"),
        "started_at": utc_now(),
        "completed_at": None,
        "git_commit": short_git_commit(root),
        "source_commit": full_git_commit(root) or text_file_value(deployed_commit),
        "code_hash": source_tree_hash(root),
        "deployment_package_hash": text_file_value(deployment_hash),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "freqtrade_version": command_version(root, ["freqtrade", "--version"]),
        "requirements_hash": sha256_file(req),
        "data_hash": hash_many([ohlcv_manifest]),
        "features_hash": hash_many([features_manifest]),
        "features_version": "features-v2",
        "edge_registry_version": "edges-v2",
        "decision_rules_version": json_version(decision_rules),
        "promotion_rules_version": json_version(promotion_rules),
        "account_rules_version": json_version(account_rules),
        "account_rules_hash": file_exists_hash(account_rules),
        "pair_universe_version": json_version(pair_universe),
        "pair_universe_hash": file_exists_hash(pair_universe),
        "account_simulator_version": "account-sim-v2",
        "account_validation_version": "account-validation-v1",
        "account_oos_version": "account-oos-v1",
        "account_oos_validation_version": "account-oos-validation-v1",
        "account_candidate_version": "account-candidate-v1",
        "account_decision_rules_version": json_version(account_decision_rules),
        "account_decision_rules_hash": file_exists_hash(account_decision_rules),
        "train_months": int(os.environ.get("TRAIN_MONTHS", "18")),
        "test_months": int(os.environ.get("TEST_MONTHS", "6")),
        "step_months": int(os.environ.get("STEP_MONTHS", "6")),
        "random_seed": 17062026,
        "expected_last_complete_candle": dq.get("expected_last_complete_candle"),
        "actual_last_available_candle": dq.get("actual_last_available_candle"),
        "last_complete_candle": dq.get("actual_last_available_candle")
        or dq.get("last_complete_candle"),
        "max_lag_bars": dq.get("max_lag_bars"),
        "allowed_lag_bars": dq.get("allowed_lag_bars"),
    }


def start_run(root: Path, storage: Path, run_id: str) -> None:
    payload = base_manifest(root, storage, run_id, "running")
    atomic_write_json(manifest_path(storage, run_id), payload)
    latest_dir = storage / "results" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(latest_dir / "run_manifest.json", payload)


def update_run(
    root: Path, storage: Path, run_id: str, status: str, error: str | None = None
) -> None:
    payload = load_manifest(storage, run_id) or base_manifest(root, storage, run_id, status)
    payload["status"] = status
    payload["completed_at"] = (
        utc_now() if status in {"success", "failed"} else payload.get("completed_at")
    )
    if error:
        payload["error"] = error

    dq_path = storage / "results" / "data_quality_report.json"
    if dq_path.exists():
        dq = json.loads(dq_path.read_text(encoding="utf-8"))
        payload["data_quality_status"] = dq.get("status")
        payload["expected_last_complete_candle"] = dq.get("expected_last_complete_candle")
        payload["actual_last_available_candle"] = dq.get("actual_last_available_candle")
        payload["last_complete_candle"] = dq.get("actual_last_available_candle") or dq.get(
            "last_complete_candle"
        )
        payload["max_lag_bars"] = dq.get("max_lag_bars")
        payload["allowed_lag_bars"] = dq.get("allowed_lag_bars")
    payload["data_hash"] = hash_many([storage / "ohlcv_manifest.parquet"])
    payload["features_hash"] = hash_many([storage / "features_manifest.parquet"])
    account_rules = root / "research_lab" / "config" / "account_rules.json"
    account_decision_rules = root / "research_lab" / "config" / "account_decision_rules.json"
    pair_universe = root / "research_lab" / "config" / "pair_universe.json"
    deployed_commit = root / "research_lab" / "DEPLOYED_COMMIT"
    deployment_hash = root / "research_lab" / "DEPLOYMENT_PACKAGE_SHA256"
    payload["account_rules_version"] = json_version(account_rules)
    payload["account_rules_hash"] = file_exists_hash(account_rules)
    payload["pair_universe_version"] = json_version(pair_universe)
    payload["pair_universe_hash"] = file_exists_hash(pair_universe)
    payload["source_commit"] = full_git_commit(root) or text_file_value(deployed_commit)
    payload["code_hash"] = source_tree_hash(root)
    payload["deployment_package_hash"] = text_file_value(deployment_hash)
    payload["account_simulator_version"] = "account-sim-v2"
    payload["account_validation_version"] = "account-validation-v1"
    payload["account_oos_version"] = "account-oos-v1"
    payload["account_oos_validation_version"] = "account-oos-validation-v1"
    payload["account_candidate_version"] = "account-candidate-v1"
    payload["account_decision_rules_version"] = json_version(account_decision_rules)
    payload["account_decision_rules_hash"] = file_exists_hash(account_decision_rules)
    payload["train_months"] = int(os.environ.get("TRAIN_MONTHS", payload.get("train_months", 18)))
    payload["test_months"] = int(os.environ.get("TEST_MONTHS", payload.get("test_months", 6)))
    payload["step_months"] = int(os.environ.get("STEP_MONTHS", payload.get("step_months", 6)))
    account_validation = storage / "results" / "account_validation.json"
    if account_validation.exists():
        validation = json.loads(account_validation.read_text(encoding="utf-8"))
        payload["account_validation_status"] = validation.get("status")
    account_oos_validation = storage / "results" / "account_oos_validation.json"
    if account_oos_validation.exists():
        validation = json.loads(account_oos_validation.read_text(encoding="utf-8"))
        payload["account_oos_validation_status"] = validation.get("status")
    account_candidate = storage / "results" / "account_candidate_decision.json"
    if account_candidate.exists():
        decision = json.loads(account_candidate.read_text(encoding="utf-8"))
        payload["account_candidate_status"] = decision.get("status")

    atomic_write_json(manifest_path(storage, run_id), payload)
    latest_dir = storage / "results" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(latest_dir / "run_manifest.json", payload)


def snapshot_results(storage: Path, run_id: str) -> None:
    results = storage / "results"
    run_dir = results / "runs" / run_id
    latest = results / "latest"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest.mkdir(parents=True, exist_ok=True)

    for name in RUN_FILES:
        source = results / name
        if not source.exists():
            continue
        for target_dir in [run_dir, latest]:
            tmp = target_dir / f".{name}.tmp-{os.getpid()}"
            target = target_dir / name
            shutil.copy2(source, tmp)
            tmp.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run manifest manager")
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--run-id", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-id")
    sub.add_parser("latest-id")
    sub.add_parser("start")
    complete = sub.add_parser("complete")
    complete.add_argument("--status", default="success", choices=["success", "failed"])
    complete.add_argument("--error", default=None)
    sub.add_parser("snapshot")

    args = parser.parse_args()
    root = args.root.resolve()
    storage = args.storage
    if args.cmd == "new-id":
        print(new_run_id(root))
        return
    if args.cmd == "latest-id":
        latest = storage / "results" / "latest" / "run_manifest.json"
        if not latest.exists():
            raise SystemExit("No latest run manifest found.")
        print(json.loads(latest.read_text(encoding="utf-8"))["run_id"])
        return
    if not args.run_id:
        raise SystemExit("--run-id is required")
    if args.cmd == "start":
        start_run(root, storage, args.run_id)
    elif args.cmd == "complete":
        update_run(root, storage, args.run_id, args.status, args.error)
    elif args.cmd == "snapshot":
        snapshot_results(storage, args.run_id)


if __name__ == "__main__":
    main()
