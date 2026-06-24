from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.account_simulator import load_account_config
from research_lab.run_manager import atomic_write_json


ACCOUNT_VALIDATION_VERSION = "account-validation-v1"
TOLERANCE = 1e-6


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def nonfinite_rows(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return 0
    return int((~np.isfinite(numeric.to_numpy(dtype=float))).any(axis=1).sum())


def run_id_mismatches(frames: list[pd.DataFrame], run_id: str | None) -> int:
    if not run_id:
        return 0
    mismatches = 0
    for df in frames:
        if df.empty or "run_id" not in df.columns:
            continue
        mismatches += int((df["run_id"].astype(str) != str(run_id)).sum())
    return mismatches


def missing_columns(df: pd.DataFrame, required: list[str]) -> list[str]:
    if df.empty:
        return []
    return [column for column in required if column not in df.columns]


def invalid_trade_dates(trades: pd.DataFrame) -> int:
    if trades.empty:
        return 0
    required = ["signal_date", "entry_date", "exit_date"]
    if missing_columns(trades, required):
        return len(trades)
    signal_date = pd.to_datetime(trades["signal_date"], utc=True)
    entry_date = pd.to_datetime(trades["entry_date"], utc=True)
    exit_date = pd.to_datetime(trades["exit_date"], utc=True)
    invalid_entry = entry_date <= signal_date
    invalid_exit = exit_date < entry_date
    return int((invalid_entry | invalid_exit).sum())


def duplicate_trade_ids(trades: pd.DataFrame) -> int:
    if trades.empty:
        return 0
    id_column = "trade_id" if "trade_id" in trades.columns else "position_id"
    if id_column not in trades.columns:
        return len(trades)
    return int(trades[id_column].duplicated().sum())


def exposure_column(equity: pd.DataFrame, preferred: str, fallback: str) -> pd.Series:
    if preferred in equity.columns:
        return equity[preferred].astype(float)
    return equity[fallback].astype(float)


def exposure_violations_for_limit(
    equity: pd.DataFrame,
    preferred: str,
    secondary: str,
    fallback: str,
    limit: float,
) -> int:
    if preferred in equity.columns:
        values = equity[preferred].astype(float)
    elif secondary in equity.columns:
        values = equity[secondary].astype(float)
    else:
        values = equity[fallback].astype(float)
    return int((values > limit + TOLERANCE).sum())


def validate_account_outputs(
    storage: Path,
    rules_path: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    config, rules_version, rules_hash = load_account_config(rules_path)
    results = storage / "results"
    trades = read_parquet(results / "account_trades.parquet")
    equity = read_parquet(results / "account_equity.parquet")
    summary = read_parquet(results / "account_summary.parquet")
    rejections = read_parquet(results / "account_rejections.parquet")

    if run_id:
        frames = []
        for df in [trades, equity, summary, rejections]:
            if not df.empty and "run_id" in df.columns:
                frames.append(df[df["run_id"].astype(str) == str(run_id)].copy())
            else:
                frames.append(df)
        trades, equity, summary, rejections = frames

    missing_files = [
        name
        for name in [
            "account_trades.parquet",
            "account_equity.parquet",
            "account_summary.parquet",
            "account_rejections.parquet",
        ]
        if not (results / name).exists()
    ]

    if equity.empty:
        equity_identity_failures = 1
        negative_cash_rows = 1
        exposure_violations = 1
        pair_exposure_violations = 1
        stake_exposure_drift_rows = 1
        pair_stake_exposure_drift_rows = 1
        position_limit_violations = 1
    else:
        equity_identity_failures = int(
            (
                (
                    equity["equity"].astype(float)
                    - equity["cash"].astype(float)
                    - equity["market_value"].astype(float)
                ).abs()
                > TOLERANCE
            ).sum()
        )
        negative_cash_rows = int((equity["cash"].astype(float) < -TOLERANCE).sum())
        exposure_violations = exposure_violations_for_limit(
            equity,
            "max_entry_stake_exposure",
            "stake_exposure",
            "exposure",
            config.max_total_exposure,
        )
        if (
            "max_entry_pair_stake_exposure" in equity.columns
            or "max_pair_stake_exposure" in equity.columns
            or "max_pair_exposure" in equity.columns
        ):
            pair_exposure_violations = exposure_violations_for_limit(
                equity,
                "max_entry_pair_stake_exposure",
                "max_pair_stake_exposure",
                "max_pair_exposure",
                config.max_pair_exposure,
            )
        else:
            pair_exposure_violations = len(equity)
        stake_exposure_drift_rows = (
            int(
                (
                    exposure_column(equity, "stake_exposure", "exposure")
                    > config.max_total_exposure + TOLERANCE
                ).sum()
            )
            if "stake_exposure" in equity.columns or "exposure" in equity.columns
            else 0
        )
        pair_stake_exposure_drift_rows = (
            int(
                (
                    exposure_column(equity, "max_pair_stake_exposure", "max_pair_exposure")
                    > config.max_pair_exposure + TOLERANCE
                ).sum()
            )
            if "max_pair_stake_exposure" in equity.columns or "max_pair_exposure" in equity.columns
            else 0
        )
        position_limit_violations = int(
            (equity["open_positions"].astype(int) > config.max_open_positions).sum()
        )

    frames = [trades, equity, summary, rejections]
    nonfinite = sum(nonfinite_rows(df) for df in frames)
    run_id_mismatch_rows = run_id_mismatches(frames, run_id)
    missing_required_columns = {
        "account_equity": missing_columns(
            equity,
            ["run_id", "date", "cash", "market_value", "equity", "exposure", "open_positions"],
        ),
        "account_summary": missing_columns(
            summary, ["run_id", "edge", "timeframe", "final_equity"]
        ),
    }
    missing_required_count = sum(len(value) for value in missing_required_columns.values())

    report = {
        "version": ACCOUNT_VALIDATION_VERSION,
        "status": "passed",
        "run_id": run_id,
        "rules_version": rules_version,
        "rules_hash": rules_hash,
        "negative_cash_rows": negative_cash_rows,
        "equity_identity_failures": equity_identity_failures,
        "exposure_violations": exposure_violations,
        "pair_exposure_violations": pair_exposure_violations,
        "stake_exposure_drift_rows": stake_exposure_drift_rows,
        "pair_stake_exposure_drift_rows": pair_stake_exposure_drift_rows,
        "position_limit_violations": position_limit_violations,
        "invalid_trade_dates": invalid_trade_dates(trades),
        "duplicate_trade_ids": duplicate_trade_ids(trades),
        "nonfinite_rows": int(nonfinite),
        "run_id_mismatch_rows": run_id_mismatch_rows,
        "missing_files": missing_files,
        "missing_required_columns": missing_required_columns,
        "trades_checked": len(trades),
        "equity_rows_checked": len(equity),
        "summary_rows_checked": len(summary),
        "rejections_checked": len(rejections),
    }
    failure_keys = [
        "negative_cash_rows",
        "equity_identity_failures",
        "exposure_violations",
        "pair_exposure_violations",
        "position_limit_violations",
        "invalid_trade_dates",
        "duplicate_trade_ids",
        "nonfinite_rows",
        "run_id_mismatch_rows",
    ]
    if missing_files or missing_required_count or any(report[key] for key in failure_keys):
        report["status"] = "failed"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate account simulator outputs")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--rules", default="research_lab/config/account_rules.json", type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = validate_account_outputs(args.storage, args.rules, args.run_id)
    out_path = args.storage / "results" / "account_validation.json"
    atomic_write_json(out_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_error and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
