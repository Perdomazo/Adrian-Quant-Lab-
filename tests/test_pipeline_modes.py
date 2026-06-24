from __future__ import annotations

import pandas as pd

from research_lab.deep_validation import run_deep_validation
from research_lab.pipeline_metrics import update_metrics
from tests.test_account_simulator import cfg, entry_signal, features
from research_lab.account_simulator import simulate_account


def test_pipeline_metrics_updates_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    update_metrics([manifest], "download", 1.2345, None, 42.0)
    update_metrics([manifest], "features", 2.0, 4.0, 40.0)

    payload = pd.read_json(manifest, typ="series").to_dict()
    assert payload["download_seconds"] == 1.234
    assert payload["features_seconds"] == 2.0
    assert payload["total_seconds"] == 4.0
    assert payload["peak_memory_mb"] == 42.0


def test_deep_validation_writes_summary_and_manifest(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
            ("2025-01-01 02:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 03:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )
    trades, _, _, _ = simulate_account(
        data,
        pd.DataFrame(
            [
                entry_signal(risk=0.02),
                entry_signal(date="2025-01-01 02:00:00+00:00", risk=0.02),
            ]
        ),
        cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
        "run-a",
        "edge_a",
        "1h",
    )
    trades.to_parquet(results / "account_trades.parquet")

    summary, manifest = run_deep_validation(
        tmp_path,
        "run-a",
        sims=50,
        block_size=2,
        seed=42,
        initial_cash=1000.0,
        research_seconds=12.0,
    )

    assert manifest["status"] == "passed"
    assert manifest["research_seconds"] == 12.0
    assert manifest["summary_rows"] == 1
    assert (results / "deep_validation_summary.parquet").exists()
    assert (results / "deep_validation_manifest.json").exists()
    assert summary.iloc[0]["sims"] == 50
