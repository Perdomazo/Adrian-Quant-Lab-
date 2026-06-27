from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.run_manager import atomic_write_json, file_exists_hash, json_version
from research_lab.warehouse import refresh_duckdb, write_parquet_atomic


ACCOUNT_CANDIDATE_VERSION = "account-candidate-v1"


SUMMARY_COLUMNS = [
    "run_id",
    "candidate_id",
    "edge",
    "edge_version",
    "timeframe",
    "pair_universe_version",
    "pair_universe_hash",
    "account_rules_version",
    "account_rules_hash",
    "account_decision_rules_version",
    "account_decision_rules_hash",
    "account_simulator_version",
    "features_version",
    "eligibility_status",
    "account_verdict",
    "failed_rules",
    "passed_rules",
    "full_trades",
    "full_total_return",
    "full_profit_factor",
    "full_max_drawdown",
    "full_open_positions_end",
    "full_unrealized_pnl",
    "full_open_pnl_share",
    "oos_folds",
    "oos_positive_fold_rate",
    "oos_median_return",
    "oos_median_profit_factor",
    "oos_worst_drawdown",
    "oos_total_trades",
    "oos_min_pair_coverage_rate",
    "consecutive_candidate_runs",
    "first_candidate_date",
    "observation_calendar_days",
    "account_candidate_version",
]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_artifact(results: Path, run_id: str, name: str) -> dict[str, Any]:
    for path in [results / "runs" / run_id / name, results / name]:
        payload = read_json(path)
        if payload and str(payload.get("run_id")) == str(run_id):
            return payload
    return {}


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def read_parquet_artifact(results: Path, run_id: str, name: str) -> pd.DataFrame:
    run_path = results / "runs" / run_id / name
    if run_path.exists():
        return read_parquet(run_path)
    return read_parquet(results / name)


def load_rules(path: Path) -> tuple[dict[str, Any], str, str | None]:
    payload = read_json(path)
    version = str(payload.get("version", "unknown"))
    return payload, version, file_exists_hash(path)


def make_candidate_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out[columns + [column for column in out.columns if column not in columns]]


def passed_or_failed(
    passed: list[str],
    failed: list[str],
    name: str,
    actual: float,
    threshold: float,
    op: str,
) -> bool:
    if op == ">=":
        ok = actual >= threshold
        token = f"{name}: {actual:g} >= {threshold:g}"
        fail_token = f"{name}: {actual:g} < {threshold:g}"
    elif op == ">":
        ok = actual > threshold
        token = f"{name}: {actual:g} > {threshold:g}"
        fail_token = f"{name}: {actual:g} <= {threshold:g}"
    else:
        raise ValueError(f"Unsupported op={op}")
    if ok:
        passed.append(token)
    else:
        failed.append(fail_token)
    return ok


def evaluate_metric_rules(
    row: dict[str, Any],
    rules: dict[str, Any],
    prefix: str,
) -> tuple[bool, list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []
    checks = [
        ("full_trades", "min_full_trades", ">="),
        ("full_profit_factor", "min_full_profit_factor", ">="),
        ("full_total_return", "min_full_total_return", ">"),
        ("full_max_drawdown", "min_full_max_drawdown", ">="),
        ("oos_folds", "min_oos_folds", ">="),
        ("oos_positive_fold_rate", "min_oos_positive_fold_rate", ">="),
        ("oos_median_return", "min_oos_median_return", ">"),
        ("oos_median_profit_factor", "min_oos_median_profit_factor", ">="),
        ("oos_worst_drawdown", "min_oos_worst_drawdown", ">="),
        ("oos_total_trades", "min_oos_total_trades", ">="),
    ]
    for metric, rule_key, op in checks:
        if rule_key not in rules:
            continue
        actual = float(row.get(metric, 0.0) or 0.0)
        threshold = float(rules[rule_key])
        passed_or_failed(passed, failed, f"{prefix}.{metric}", actual, threshold, op)
    if "max_open_pnl_share" in rules:
        actual = float(row.get("full_open_pnl_share", 0.0) or 0.0)
        threshold = float(rules["max_open_pnl_share"])
        ok = actual <= threshold
        if ok:
            passed.append(f"{prefix}.full_open_pnl_share: {actual:g} <= {threshold:g}")
        else:
            failed.append(f"{prefix}.full_open_pnl_share: {actual:g} > {threshold:g}")
    return not failed, passed, failed


def validation_status(
    manifest: dict[str, Any],
    data_quality: dict[str, Any],
    account_validation: dict[str, Any],
    account_oos_validation: dict[str, Any],
    eligibility_rules: dict[str, Any],
    allow_running_run: bool = False,
) -> tuple[str, list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []
    manifest_status = manifest.get("status")
    if manifest_status == "success":
        passed.append("run_manifest.status: success")
    elif allow_running_run and manifest_status == "running":
        passed.append("run_manifest.status: running allowed")
    else:
        failed.append(f"run_manifest.status: {manifest_status} != success")
        return "invalid_run", passed, failed

    validations = [
        (
            "data_quality",
            eligibility_rules.get("require_data_quality_passed", True),
            data_quality.get("status") or manifest.get("data_quality_status"),
        ),
        (
            "account_validation",
            eligibility_rules.get("require_account_validation_passed", True),
            account_validation.get("status") or manifest.get("account_validation_status"),
        ),
        (
            "account_oos_validation",
            eligibility_rules.get("require_account_oos_validation_passed", True),
            account_oos_validation.get("status") or manifest.get("account_oos_validation_status"),
        ),
    ]
    for name, required, status in validations:
        if not required:
            continue
        if status == "passed":
            passed.append(f"{name}.status: passed")
        else:
            failed.append(f"{name}.status: {status} != passed")
    if failed:
        return "failed_validation", passed, failed
    return "eligible", passed, failed


def observation_months(account_oos_summary: pd.DataFrame, run_id: str) -> float:
    if account_oos_summary.empty:
        return 0.0
    df = account_oos_summary
    if "run_id" in df.columns:
        df = df[df["run_id"].astype(str) == str(run_id)].copy()
    if df.empty or "train_start" not in df.columns or "test_end" not in df.columns:
        return 0.0
    start = pd.Timestamp(pd.to_datetime(df["train_start"], utc=True).min())
    end = pd.Timestamp(pd.to_datetime(df["test_end"], utc=True).max())
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")
    if end <= start:
        return 0.0
    return float((end - start).days / 30.4375)


def open_pnl_share(account_equity: pd.DataFrame, edge: str, timeframe: str, run_id: str) -> float:
    if account_equity.empty:
        return 0.0
    required = {"run_id", "edge", "timeframe", "date", "equity", "unrealized_pnl"}
    if not required.issubset(account_equity.columns):
        return 0.0
    df = account_equity[
        (account_equity["run_id"].astype(str) == str(run_id))
        & (account_equity["edge"] == edge)
        & (account_equity["timeframe"] == timeframe)
    ].copy()
    if df.empty:
        return 0.0
    last = df.sort_values("date").iloc[-1]
    equity = float(last.get("equity", 0.0) or 0.0)
    unrealized = abs(float(last.get("unrealized_pnl", 0.0) or 0.0))
    return unrealized / equity if equity > 0 else 0.0


def build_candidate_rows(  # noqa: C901
    root: Path,
    storage: Path,
    rules: dict[str, Any],
    rules_version: str,
    rules_hash: str | None,
    pair_universe: Path,
    run_id: str,
    allow_running_run: bool = False,
) -> pd.DataFrame:
    results = storage / "results"
    manifest = read_json(results / "latest" / "run_manifest.json")
    if manifest.get("run_id") != run_id:
        run_manifest = read_json(results / "runs" / run_id / "run_manifest.json")
        if run_manifest:
            manifest = run_manifest
    data_quality = read_json_artifact(results, run_id, "data_quality_report.json")
    account_validation = read_json_artifact(results, run_id, "account_validation.json")
    account_oos_validation = read_json_artifact(results, run_id, "account_oos_validation.json")
    account_summary = read_parquet_artifact(results, run_id, "account_summary.parquet")
    account_equity = read_parquet_artifact(results, run_id, "account_equity.parquet")
    account_oos = read_parquet_artifact(results, run_id, "account_oos_aggregate.parquet")
    account_oos_summary = read_parquet_artifact(results, run_id, "account_oos_summary.parquet")
    if run_id:
        for frame_name, frame in [("summary", account_summary), ("oos", account_oos)]:
            if not frame.empty and "run_id" in frame.columns:
                filtered = frame[frame["run_id"].astype(str) == str(run_id)].copy()
                if frame_name == "summary":
                    account_summary = filtered
                else:
                    account_oos = filtered
    merge_keys = {"edge", "edge_version", "timeframe"}
    if not merge_keys.issubset(account_summary.columns) or not merge_keys.issubset(
        account_oos.columns
    ):
        return ensure_columns(pd.DataFrame(), SUMMARY_COLUMNS)

    eligibility_rules = rules.get("eligibility", {})
    base_status, base_passed, base_failed = validation_status(
        manifest,
        data_quality,
        account_validation,
        account_oos_validation,
        eligibility_rules,
        allow_running_run,
    )
    pair_universe_version = json_version(pair_universe) or manifest.get("pair_universe_version")
    pair_universe_hash = file_exists_hash(pair_universe) or manifest.get("pair_universe_hash")
    source_commit = manifest.get("source_commit")
    data_hash = manifest.get("data_hash")
    features_version = str(manifest.get("features_version", "unknown"))
    account_rules_version = str(manifest.get("account_rules_version", "unknown"))
    account_rules_hash = manifest.get("account_rules_hash")
    account_simulator_version = str(manifest.get("account_simulator_version", "unknown"))

    rows: list[dict[str, Any]] = []
    merged = account_summary.merge(
        account_oos,
        on=["edge", "edge_version", "timeframe"],
        how="inner",
        suffixes=("_full", "_oos"),
    )
    for item in merged.to_dict("records"):
        edge = str(item["edge"])
        timeframe = str(item["timeframe"])
        edge_version = str(item.get("edge_version", "unknown"))
        oos_folds = int(item.get("folds", 0) or 0)
        oos_min_pair_coverage_rate = float(item.get("min_pair_coverage_rate", 0.0) or 0.0)
        payload = {
            "edge": edge,
            "edge_version": edge_version,
            "timeframe": timeframe,
            "pair_universe_version": pair_universe_version,
            "pair_universe_hash": pair_universe_hash,
            "account_rules_version": account_rules_version,
            "account_rules_hash": account_rules_hash,
            "account_simulator_version": account_simulator_version,
            "features_version": features_version,
        }
        candidate_id = make_candidate_id(payload)
        row = {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "edge": edge,
            "edge_version": edge_version,
            "timeframe": timeframe,
            "pair_universe_version": pair_universe_version,
            "pair_universe_hash": pair_universe_hash,
            "account_rules_version": account_rules_version,
            "account_rules_hash": account_rules_hash,
            "account_decision_rules_version": rules_version,
            "account_decision_rules_hash": rules_hash,
            "account_simulator_version": account_simulator_version,
            "features_version": features_version,
            "full_trades": int(item.get("trades", 0) or 0),
            "full_total_return": float(item.get("total_return", 0.0) or 0.0),
            "full_profit_factor": float(item.get("profit_factor", 0.0) or 0.0),
            "full_max_drawdown": float(item.get("max_drawdown", 0.0) or 0.0),
            "full_open_positions_end": int(item.get("open_positions_end", 0) or 0),
            "full_unrealized_pnl": 0.0,
            "full_open_pnl_share": open_pnl_share(account_equity, edge, timeframe, run_id),
            "oos_folds": oos_folds,
            "oos_positive_fold_rate": float(item.get("positive_fold_rate", 0.0) or 0.0),
            "oos_median_return": float(item.get("median_oos_return", 0.0) or 0.0),
            "oos_median_profit_factor": float(item.get("median_oos_profit_factor", 0.0) or 0.0),
            "oos_worst_drawdown": float(item.get("worst_oos_drawdown", 0.0) or 0.0),
            "oos_total_trades": int(item.get("total_oos_trades", 0) or 0),
            "oos_min_pair_coverage_rate": oos_min_pair_coverage_rate,
            "source_commit": source_commit,
            "data_hash": data_hash,
            "account_candidate_version": ACCOUNT_CANDIDATE_VERSION,
        }
        passed = list(base_passed)
        failed = list(base_failed)
        eligibility_status = base_status
        min_folds = int(eligibility_rules.get("min_oos_folds", 0) or 0)
        if eligibility_status == "eligible" and oos_folds < min_folds:
            eligibility_status = "insufficient_history"
            failed.append(f"oos_folds: {oos_folds} < {min_folds}")
        elif min_folds:
            passed.append(f"oos_folds: {oos_folds} >= {min_folds}")

        min_months = float(eligibility_rules.get("min_observation_months", 0.0) or 0.0)
        months = observation_months(account_oos_summary, run_id)
        if eligibility_status == "eligible" and months < min_months:
            eligibility_status = "insufficient_history"
            failed.append(f"observation_months: {months:g} < {min_months:g}")
        elif min_months:
            passed.append(f"observation_months: {months:g} >= {min_months:g}")

        min_coverage = float(eligibility_rules.get("min_pair_coverage_rate", 0.0) or 0.0)
        coverage = oos_min_pair_coverage_rate
        if eligibility_status == "eligible" and coverage < min_coverage:
            eligibility_status = "insufficient_history"
            failed.append(f"pair_coverage_rate: {coverage:g} < {min_coverage:g}")
        elif min_coverage:
            passed.append(f"pair_coverage_rate: {coverage:g} >= {min_coverage:g}")

        candidate_ok, candidate_passed, candidate_failed = evaluate_metric_rules(
            row, rules.get("candidate", {}), "candidate"
        )
        watch_ok, watch_passed, watch_failed = evaluate_metric_rules(
            row, rules.get("watch", {}), "watch"
        )
        if eligibility_status == "eligible" and candidate_ok:
            verdict = "candidate"
            passed.extend(candidate_passed)
        elif eligibility_status in {"eligible", "insufficient_history"} and watch_ok:
            verdict = "watch"
            passed.extend(watch_passed)
            failed.extend(candidate_failed)
        else:
            verdict = "reject"
            failed.extend(candidate_failed if eligibility_status == "eligible" else watch_failed)

        row["eligibility_status"] = eligibility_status
        row["account_verdict"] = verdict
        row["failed_rules"] = json.dumps(failed, sort_keys=True)
        row["passed_rules"] = json.dumps(passed, sort_keys=True)
        rows.append(row)

    return ensure_columns(pd.DataFrame(rows), SUMMARY_COLUMNS)


def add_history_metrics(summary: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    out = summary.copy()
    out["consecutive_candidate_runs"] = 0
    out["first_candidate_date"] = pd.NA
    out["observation_calendar_days"] = 0
    if history.empty:
        return out
    history = history.sort_values("evaluation_date")
    for idx, row in out.iterrows():
        candidate_history = history[history["candidate_id"] == row["candidate_id"]]
        streak = 0
        first_date = None
        for hist in reversed(list(candidate_history.to_dict("records"))):
            if hist.get("account_verdict") == "candidate":
                streak += 1
                first_date = hist.get("evaluation_date")
            else:
                break
        out.at[idx, "consecutive_candidate_runs"] = streak
        out.at[idx, "first_candidate_date"] = first_date
        if first_date:
            elapsed = pd.Timestamp(utc_now()) - pd.Timestamp(first_date)
            out.at[idx, "observation_calendar_days"] = int(elapsed.days)
    return out


def update_history(storage: Path, summary: pd.DataFrame, manifest: dict[str, Any]) -> pd.DataFrame:
    path = storage / "results" / "account_candidate_history.parquet"
    existing = read_parquet(path)
    history_cols = [
        "candidate_id",
        "run_id",
        "evaluation_date",
        "eligibility_status",
        "account_verdict",
        "source_commit",
        "data_hash",
    ]
    if summary.empty:
        history = ensure_columns(existing, history_cols)[history_cols]
    else:
        rows = summary.copy()
        rows["evaluation_date"] = utc_now()
        rows["source_commit"] = manifest.get("source_commit")
        rows["data_hash"] = manifest.get("data_hash")
        rows = ensure_columns(rows, history_cols)[history_cols]
        existing = ensure_columns(existing, history_cols)[history_cols]
        history = pd.concat([existing, rows], ignore_index=True) if not existing.empty else rows
        history = history.drop_duplicates(["candidate_id", "run_id"], keep="last")
    write_parquet_atomic(history, path)
    return history


def run_account_candidate(
    root: Path,
    storage: Path,
    rules_path: Path,
    pair_universe: Path,
    run_id: str,
    allow_running_run: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    rules, rules_version, rules_hash = load_rules(rules_path)
    results = storage / "results"
    manifest = read_json(results / "latest" / "run_manifest.json")
    if manifest.get("run_id") != run_id:
        run_manifest = read_json(results / "runs" / run_id / "run_manifest.json")
        if run_manifest:
            manifest = run_manifest

    summary = build_candidate_rows(
        root,
        storage,
        rules,
        rules_version,
        rules_hash,
        pair_universe,
        run_id,
        allow_running_run,
    )
    history = update_history(storage, summary, manifest)
    summary = add_history_metrics(summary, history)

    write_parquet_atomic(summary, results / "account_candidate_summary.parquet")
    decision = {
        "status": "success",
        "run_id": run_id,
        "account_candidate_version": ACCOUNT_CANDIDATE_VERSION,
        "account_decision_rules_version": rules_version,
        "account_decision_rules_hash": rules_hash,
        "eligible_candidates": int((summary["eligibility_status"] == "eligible").sum())
        if not summary.empty
        else 0,
        "candidate_count": int((summary["account_verdict"] == "candidate").sum())
        if not summary.empty
        else 0,
        "watch_count": int((summary["account_verdict"] == "watch").sum())
        if not summary.empty
        else 0,
        "reject_count": int((summary["account_verdict"] == "reject").sum())
        if not summary.empty
        else 0,
        "insufficient_history_count": int(
            (summary["eligibility_status"] == "insufficient_history").sum()
        )
        if not summary.empty
        else 0,
        "rows": len(summary),
        "generated_at": utc_now(),
    }
    atomic_write_json(results / "account_candidate_decision.json", decision)
    refresh_duckdb(storage)
    return summary, decision, history


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate account-level candidates")
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument(
        "--rules",
        default="research_lab/config/account_decision_rules.json",
        type=Path,
    )
    parser.add_argument(
        "--pair-universe",
        default="research_lab/config/pair_universe.json",
        type=Path,
    )
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID"))
    parser.add_argument(
        "--allow-running-run",
        action="store_true",
        help="Allow the active pipeline run to be evaluated before run_manager marks success.",
    )
    args = parser.parse_args()

    run_id = args.run_id
    if not run_id:
        latest = read_json(args.storage / "results" / "latest" / "run_manifest.json")
        run_id = latest.get("run_id")
    if not run_id:
        raise SystemExit("--run-id is required")

    summary, decision, _ = run_account_candidate(
        args.root.resolve(),
        args.storage,
        args.rules,
        args.pair_universe,
        str(run_id),
        args.allow_running_run,
    )
    print(json.dumps(decision, indent=2, sort_keys=True))
    if not summary.empty:
        cols = [
            "edge",
            "timeframe",
            "eligibility_status",
            "account_verdict",
            "full_profit_factor",
            "oos_folds",
            "oos_positive_fold_rate",
            "oos_median_profit_factor",
            "oos_min_pair_coverage_rate",
            "oos_total_trades",
        ]
        print(summary[cols].to_string(index=False))


if __name__ == "__main__":
    main()
