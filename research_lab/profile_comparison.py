from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.profile_paths import profile_results_dir
from research_lab.run_manager import atomic_write_json
from research_lab.warehouse import refresh_duckdb, write_parquet_atomic


PROFILE_COMPARISON_VERSION = "profile-comparison-v1"

SUMMARY_COLUMNS = [
    "run_id",
    "edge",
    "edge_version",
    "timeframe",
    "research_return",
    "freqtrade_return",
    "return_delta",
    "research_pf",
    "freqtrade_pf",
    "pf_delta",
    "research_max_drawdown",
    "freqtrade_max_drawdown",
    "drawdown_delta",
    "research_oos_positive_fold_rate",
    "freqtrade_oos_positive_fold_rate",
    "research_entries",
    "freqtrade_entries",
    "entry_overlap_rate",
    "research_trades",
    "freqtrade_trades",
    "trade_overlap_rate",
    "research_verdict",
    "freqtrade_verdict",
    "profile_comparison_version",
]

OVERLAP_COLUMNS = [
    "run_id",
    "edge",
    "edge_version",
    "timeframe",
    "entry_overlap_rate",
    "trade_overlap_rate",
    "research_trade_count",
    "freqtrade_trade_count",
    "profile_comparison_version",
]


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out[columns + [column for column in out.columns if column not in columns]]


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def filter_run(df: pd.DataFrame, run_id: str | None) -> pd.DataFrame:
    if df.empty or not run_id or "run_id" not in df.columns:
        return df
    return df[df["run_id"].astype(str) == str(run_id)].copy()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if np.isfinite(out) else default


def overlap_rate(left: pd.DataFrame, right: pd.DataFrame, keys: list[str]) -> float:
    if left.empty and right.empty:
        return 1.0
    if left.empty or right.empty:
        return 0.0
    if any(column not in left.columns or column not in right.columns for column in keys):
        return 0.0
    left_keys = left[keys].astype(str).drop_duplicates()
    right_keys = right[keys].astype(str).drop_duplicates()
    if left_keys.empty and right_keys.empty:
        return 1.0
    merged = left_keys.merge(right_keys, on=keys, how="inner")
    denominator = max(len(left_keys), len(right_keys), 1)
    return float(len(merged) / denominator)


def profile_frames(storage: Path, profile: str, run_id: str | None) -> dict[str, pd.DataFrame]:
    root = profile_results_dir(storage, profile)
    return {
        "summary": filter_run(read_parquet(root / "account_summary.parquet"), run_id),
        "trades": filter_run(read_parquet(root / "account_trades.parquet"), run_id),
        "oos": filter_run(read_parquet(root / "account_oos_aggregate.parquet"), run_id),
        "candidate": filter_run(read_parquet(root / "account_candidate_summary.parquet"), run_id),
    }


def comparison_rows(
    storage: Path,
    run_id: str | None,
    research_profile: str,
    freqtrade_profile: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    research = profile_frames(storage, research_profile, run_id)
    freqtrade = profile_frames(storage, freqtrade_profile, run_id)

    left = research["summary"]
    right = freqtrade["summary"]
    keys = ["edge", "edge_version", "timeframe"]
    if left.empty or right.empty or any(column not in left.columns for column in keys):
        return pd.DataFrame(columns=SUMMARY_COLUMNS), pd.DataFrame(columns=OVERLAP_COLUMNS)

    merged = left.merge(
        right,
        on=keys,
        how="outer",
        suffixes=("_research", "_freqtrade"),
    )
    rows: list[dict[str, Any]] = []
    overlap_rows: list[dict[str, Any]] = []
    for item in merged.to_dict("records"):
        edge = str(item.get("edge"))
        edge_version = str(item.get("edge_version"))
        timeframe = str(item.get("timeframe"))
        research_oos = research["oos"]
        freqtrade_oos = freqtrade["oos"]
        research_candidate = research["candidate"]
        freqtrade_candidate = freqtrade["candidate"]
        r_oos = research_oos[
            (research_oos["edge"] == edge)
            & (research_oos["edge_version"] == edge_version)
            & (research_oos["timeframe"] == timeframe)
        ]
        f_oos = freqtrade_oos[
            (freqtrade_oos["edge"] == edge)
            & (freqtrade_oos["edge_version"] == edge_version)
            & (freqtrade_oos["timeframe"] == timeframe)
        ]
        r_candidate = research_candidate[
            (research_candidate["edge"] == edge)
            & (research_candidate["edge_version"] == edge_version)
            & (research_candidate["timeframe"] == timeframe)
        ]
        f_candidate = freqtrade_candidate[
            (freqtrade_candidate["edge"] == edge)
            & (freqtrade_candidate["edge_version"] == edge_version)
            & (freqtrade_candidate["timeframe"] == timeframe)
        ]
        r_trades = research["trades"][
            (research["trades"]["edge"] == edge) & (research["trades"]["timeframe"] == timeframe)
        ]
        f_trades = freqtrade["trades"][
            (freqtrade["trades"]["edge"] == edge) & (freqtrade["trades"]["timeframe"] == timeframe)
        ]
        entry_overlap = overlap_rate(r_trades, f_trades, ["pair", "signal_date", "entry_date"])
        trade_overlap = overlap_rate(
            r_trades, f_trades, ["pair", "signal_date", "entry_date", "exit_date", "exit_reason"]
        )
        research_return = safe_float(item.get("total_return_research"))
        freqtrade_return = safe_float(item.get("total_return_freqtrade"))
        research_pf = safe_float(item.get("profit_factor_research"))
        freqtrade_pf = safe_float(item.get("profit_factor_freqtrade"))
        research_dd = safe_float(item.get("max_drawdown_research"))
        freqtrade_dd = safe_float(item.get("max_drawdown_freqtrade"))
        rows.append(
            {
                "run_id": run_id,
                "edge": edge,
                "edge_version": edge_version,
                "timeframe": timeframe,
                "research_return": research_return,
                "freqtrade_return": freqtrade_return,
                "return_delta": freqtrade_return - research_return,
                "research_pf": research_pf,
                "freqtrade_pf": freqtrade_pf,
                "pf_delta": freqtrade_pf - research_pf,
                "research_max_drawdown": research_dd,
                "freqtrade_max_drawdown": freqtrade_dd,
                "drawdown_delta": freqtrade_dd - research_dd,
                "research_oos_positive_fold_rate": (
                    safe_float(r_oos.iloc[0]["positive_fold_rate"]) if not r_oos.empty else 0.0
                ),
                "freqtrade_oos_positive_fold_rate": (
                    safe_float(f_oos.iloc[0]["positive_fold_rate"]) if not f_oos.empty else 0.0
                ),
                "research_entries": int(item.get("entries_opened_research") or 0),
                "freqtrade_entries": int(item.get("entries_opened_freqtrade") or 0),
                "entry_overlap_rate": entry_overlap,
                "research_trades": int(item.get("trades_research") or 0),
                "freqtrade_trades": int(item.get("trades_freqtrade") or 0),
                "trade_overlap_rate": trade_overlap,
                "research_verdict": (
                    str(r_candidate.iloc[0]["account_verdict"])
                    if not r_candidate.empty
                    else "missing"
                ),
                "freqtrade_verdict": (
                    str(f_candidate.iloc[0]["account_verdict"])
                    if not f_candidate.empty
                    else "missing"
                ),
                "profile_comparison_version": PROFILE_COMPARISON_VERSION,
            }
        )
        overlap_rows.append(
            {
                "run_id": run_id,
                "edge": edge,
                "edge_version": edge_version,
                "timeframe": timeframe,
                "entry_overlap_rate": entry_overlap,
                "trade_overlap_rate": trade_overlap,
                "research_trade_count": len(r_trades),
                "freqtrade_trade_count": len(f_trades),
                "profile_comparison_version": PROFILE_COMPARISON_VERSION,
            }
        )
    return ensure_columns(pd.DataFrame(rows), SUMMARY_COLUMNS), ensure_columns(
        pd.DataFrame(overlap_rows), OVERLAP_COLUMNS
    )


def run_profile_comparison(
    storage: Path,
    run_id: str | None,
    research_profile: str = "research",
    freqtrade_profile: str = "freqtrade",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    summary, overlap = comparison_rows(storage, run_id, research_profile, freqtrade_profile)
    results = storage / "results"
    write_parquet_atomic(summary, results / "profile_comparison_summary.parquet")
    write_parquet_atomic(overlap, results / "profile_trade_overlap.parquet")
    decision = {
        "status": "success" if not summary.empty else "no_results",
        "run_id": run_id,
        "profile_comparison_version": PROFILE_COMPARISON_VERSION,
        "research_profile": research_profile,
        "freqtrade_profile": freqtrade_profile,
        "rows": len(summary),
        "freqtrade_watch_count": int((summary.get("freqtrade_verdict", "") == "watch").sum())
        if not summary.empty
        else 0,
        "freqtrade_candidate_count": int(
            (summary.get("freqtrade_verdict", "") == "candidate").sum()
        )
        if not summary.empty
        else 0,
    }
    atomic_write_json(results / "profile_comparison.json", decision)
    refresh_duckdb(storage)
    return summary, overlap, decision


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare research and freqtrade execution profiles"
    )
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--research-profile", default="research")
    parser.add_argument("--freqtrade-profile", default="freqtrade")
    args = parser.parse_args()

    summary, _, decision = run_profile_comparison(
        args.storage, args.run_id, args.research_profile, args.freqtrade_profile
    )
    print(json.dumps(decision, indent=2, sort_keys=True))
    if not summary.empty:
        cols = [
            "edge",
            "timeframe",
            "research_verdict",
            "freqtrade_verdict",
            "return_delta",
            "pf_delta",
            "entry_overlap_rate",
            "trade_overlap_rate",
        ]
        print(summary[cols].to_string(index=False))


if __name__ == "__main__":
    main()
