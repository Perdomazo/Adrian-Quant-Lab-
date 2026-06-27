from __future__ import annotations

import json

import pandas as pd
import pytest

from research_lab.profile_comparison import run_profile_comparison
from research_lab.profile_paths import profile_results_dir


def write_profile(storage, profile: str, return_value: float, pf: float, verdict: str) -> None:
    root = profile_results_dir(storage, profile)
    root.mkdir(parents=True, exist_ok=True)
    base = {
        "run_id": "run-a",
        "edge": "edge_a",
        "edge_version": "edges-v2",
        "timeframe": "1h",
        "execution_profile": profile,
    }
    pd.DataFrame(
        [
            {
                **base,
                "total_return": return_value,
                "profit_factor": pf,
                "max_drawdown": -0.1,
                "entries_opened": 1,
                "trades": 1,
            }
        ]
    ).to_parquet(root / "account_summary.parquet")
    pd.DataFrame(
        [
            {
                **base,
                "pair": "BTC/USDT",
                "signal_date": pd.Timestamp("2025-01-01", tz="UTC"),
                "entry_date": pd.Timestamp("2025-01-02", tz="UTC"),
                "exit_date": pd.Timestamp("2025-01-03", tz="UTC"),
                "exit_reason": "take_profit",
            }
        ]
    ).to_parquet(root / "account_trades.parquet")
    pd.DataFrame([{**base, "positive_fold_rate": 0.5}]).to_parquet(
        root / "account_oos_aggregate.parquet"
    )
    pd.DataFrame([{**base, "account_verdict": verdict}]).to_parquet(
        root / "account_candidate_summary.parquet"
    )


def test_profile_comparison_calculates_deltas(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    write_profile(storage, "research", 0.10, 1.2, "watch")
    write_profile(storage, "freqtrade", 0.05, 1.1, "reject")

    summary, overlap, decision = run_profile_comparison(storage, "run-a")

    assert decision["status"] == "success"
    assert decision["rows"] == 1
    assert summary.iloc[0]["return_delta"] == -0.05
    assert summary.iloc[0]["pf_delta"] == pytest.approx(-0.1)
    assert summary.iloc[0]["entry_overlap_rate"] == 1.0
    assert overlap.iloc[0]["trade_overlap_rate"] == 1.0
    assert json.loads((storage / "results" / "profile_comparison.json").read_text())["rows"] == 1


def test_profile_comparison_returns_no_results_without_profiles(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")

    summary, overlap, decision = run_profile_comparison(storage, "run-a")

    assert summary.empty
    assert overlap.empty
    assert decision["status"] == "no_results"


def test_profile_comparison_detects_zero_overlap(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    write_profile(storage, "research", 0.10, 1.2, "watch")
    write_profile(storage, "freqtrade", 0.05, 1.1, "reject")
    freqtrade_trades = profile_results_dir(storage, "freqtrade") / "account_trades.parquet"
    trades = pd.read_parquet(freqtrade_trades)
    trades["entry_date"] = pd.Timestamp("2025-02-01", tz="UTC")
    trades.to_parquet(freqtrade_trades)

    summary, overlap, _ = run_profile_comparison(storage, "run-a")

    assert summary.iloc[0]["entry_overlap_rate"] == 0.0
    assert overlap.iloc[0]["trade_overlap_rate"] == 0.0


def test_profile_comparison_counts_freqtrade_watch_and_candidate(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    write_profile(storage, "research", 0.10, 1.2, "watch")
    write_profile(storage, "freqtrade", 0.05, 1.1, "candidate")

    _, _, decision = run_profile_comparison(storage, "run-a")

    assert decision["freqtrade_candidate_count"] == 1
    assert decision["freqtrade_watch_count"] == 0
