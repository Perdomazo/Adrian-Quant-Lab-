from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.account_oos import ACCOUNT_OOS_VERSION, aggregate_oos
from research_lab.run_manager import atomic_write_json


ACCOUNT_OOS_VALIDATION_VERSION = "account-oos-validation-v1"
TOLERANCE = 1e-6


OOS_FILES = {
    "summary": "account_oos_summary.parquet",
    "trades": "account_oos_trades.parquet",
    "equity": "account_oos_equity.parquet",
    "rejections": "account_oos_rejections.parquet",
    "aggregate": "account_oos_aggregate.parquet",
}


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def filter_run_id(df: pd.DataFrame, run_id: str | None) -> pd.DataFrame:
    if df.empty or not run_id or "run_id" not in df.columns:
        return df
    return df[df["run_id"].astype(str) == str(run_id)].copy()


def mixed_run_id_rows(frames: list[pd.DataFrame], run_id: str | None) -> int:
    mismatches = 0
    for df in frames:
        if df.empty or "run_id" not in df.columns:
            continue
        values = df["run_id"].astype(str)
        if run_id:
            mismatches += int((values != str(run_id)).sum())
        elif values.nunique(dropna=True) > 1:
            mismatches += len(values)
    return mismatches


def nonfinite_rows(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return 0
    return int((~np.isfinite(numeric.to_numpy(dtype=float))).any(axis=1).sum())


def trades_outside_test(trades: pd.DataFrame) -> int:
    if trades.empty:
        return 0
    required = {"entry_date", "test_start", "test_end"}
    if not required.issubset(trades.columns):
        return len(trades)
    entry_date = pd.to_datetime(trades["entry_date"], utc=True)
    test_start = pd.to_datetime(trades["test_start"], utc=True)
    test_end = pd.to_datetime(trades["test_end"], utc=True)
    outside = (entry_date < test_start) | (entry_date >= test_end)
    return int(outside.sum())


def duplicate_folds(summary: pd.DataFrame) -> int:
    if summary.empty:
        return 0
    required = ["run_id", "edge", "timeframe", "fold"]
    if any(column not in summary.columns for column in required):
        return len(summary)
    return int(summary.duplicated(required).sum())


def duplicate_equity_rows(equity: pd.DataFrame) -> int:
    if equity.empty:
        return 0
    required = ["run_id", "edge", "timeframe", "fold", "date"]
    if any(column not in equity.columns for column in required):
        return len(equity)
    return int(equity.duplicated(required).sum())


def equity_start_failures(summary: pd.DataFrame, equity: pd.DataFrame) -> int:
    if summary.empty or equity.empty:
        return 0
    required = {"run_id", "edge", "timeframe", "fold", "date", "equity"}
    if not required.issubset(equity.columns) or "initial_cash" not in summary.columns:
        return len(summary)
    failures = 0
    for key, group in equity.groupby(["run_id", "edge", "timeframe", "fold"], dropna=False):
        first = group.sort_values("date").iloc[0]
        row = summary[
            (summary["run_id"] == key[0])
            & (summary["edge"] == key[1])
            & (summary["timeframe"] == key[2])
            & (summary["fold"] == key[3])
        ]
        if row.empty:
            failures += 1
            continue
        initial_cash = float(row.iloc[0]["initial_cash"])
        first_equity = float(first["equity"])
        if first_equity <= 0 or first_equity > initial_cash * 1.05:
            failures += 1
    return failures


def pair_coverage_violations(summary: pd.DataFrame) -> int:
    if summary.empty:
        return 0
    required = {"available_pair_count", "universe_pair_count", "pair_coverage_rate"}
    if not required.issubset(summary.columns):
        return len(summary)
    available = summary["available_pair_count"].astype(float)
    universe = summary["universe_pair_count"].astype(float)
    rate = summary["pair_coverage_rate"].astype(float)
    expected_rate = available / universe.replace(0, np.nan)
    invalid = (
        (universe <= 0)
        | (available < 0)
        | (available > universe)
        | (rate < 0)
        | (rate > 1)
        | (~np.isclose(rate, expected_rate, atol=TOLERANCE, equal_nan=False))
    )
    return int(invalid.sum())


def aggregate_mismatches(summary: pd.DataFrame, aggregate: pd.DataFrame) -> int:
    if summary.empty and aggregate.empty:
        return 0
    if summary.empty or aggregate.empty:
        return max(len(summary), len(aggregate), 1)

    expected = aggregate_oos(summary)
    sort_cols = ["run_id", "edge", "edge_version", "timeframe"]
    expected = expected.sort_values(sort_cols).reset_index(drop=True)
    actual = aggregate.sort_values(sort_cols).reset_index(drop=True)
    if len(expected) != len(actual):
        return abs(len(expected) - len(actual)) or 1

    mismatches = 0
    for column in expected.columns:
        if column not in actual.columns:
            mismatches += len(expected)
            continue
        if pd.api.types.is_numeric_dtype(expected[column]):
            left = expected[column].astype(float).to_numpy()
            right = actual[column].astype(float).to_numpy()
            mismatches += int((~np.isclose(left, right, atol=TOLERANCE, equal_nan=True)).sum())
        else:
            mismatches += int((expected[column].astype(str) != actual[column].astype(str)).sum())
    return mismatches


def validate_account_oos_outputs(storage: Path, run_id: str | None = None) -> dict[str, Any]:
    results = storage / "results"
    paths = {key: results / name for key, name in OOS_FILES.items()}
    missing_artifacts = [path.name for path in paths.values() if not path.exists()]

    raw_frames = {key: read_parquet(path) for key, path in paths.items()}
    mixed_run_ids = mixed_run_id_rows(list(raw_frames.values()), run_id)
    frames = {key: filter_run_id(df, run_id) for key, df in raw_frames.items()}

    summary = frames["summary"]
    trades = frames["trades"]
    equity = frames["equity"]
    aggregate = frames["aggregate"]
    rejections = frames["rejections"]

    report = {
        "status": "passed",
        "run_id": run_id,
        "account_oos_version": ACCOUNT_OOS_VERSION,
        "account_oos_validation_version": ACCOUNT_OOS_VALIDATION_VERSION,
        "folds_checked": len(summary),
        "trades_outside_test": trades_outside_test(trades),
        "duplicate_folds": duplicate_folds(summary),
        "duplicate_equity_rows": duplicate_equity_rows(equity),
        "equity_start_failures": equity_start_failures(summary, equity),
        "pair_coverage_violations": pair_coverage_violations(summary),
        "mixed_run_ids": mixed_run_ids,
        "nonfinite_rows": int(
            nonfinite_rows(summary)
            + nonfinite_rows(trades)
            + nonfinite_rows(equity)
            + nonfinite_rows(rejections)
            + nonfinite_rows(aggregate)
        ),
        "aggregate_mismatches": aggregate_mismatches(summary, aggregate),
        "missing_artifacts": missing_artifacts,
        "summary_rows": len(summary),
        "trade_rows": len(trades),
        "equity_rows": len(equity),
        "rejection_rows": len(rejections),
        "aggregate_rows": len(aggregate),
    }

    failure_keys = [
        "trades_outside_test",
        "duplicate_folds",
        "duplicate_equity_rows",
        "equity_start_failures",
        "pair_coverage_violations",
        "mixed_run_ids",
        "nonfinite_rows",
        "aggregate_mismatches",
    ]
    if missing_artifacts or any(report[key] for key in failure_keys) or len(summary) == 0:
        report["status"] = "failed"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate account OOS artifacts")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = validate_account_oos_outputs(args.storage, args.run_id)
    out_path = args.storage / "results" / "account_oos_validation.json"
    atomic_write_json(out_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_error and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
