from __future__ import annotations

import pandas as pd

from research_lab.account_oos_validation import validate_account_oos_outputs
from tests.test_account_oos import make_storage, run_oos, write_features, write_signals
from tests.test_account_simulator import entry_signal


def prepare_valid_oos(tmp_path):
    storage = make_storage(tmp_path)
    write_features(storage)
    write_signals(
        storage,
        [
            entry_signal(date="2025-02-02 00:00:00+00:00"),
            entry_signal(date="2025-03-02 00:00:00+00:00"),
        ],
    )
    run_oos(storage)
    return storage


def test_account_oos_validation_passes_valid_outputs(tmp_path):
    storage = prepare_valid_oos(tmp_path)

    report = validate_account_oos_outputs(storage, "run-a")

    assert report["status"] == "passed"
    assert report["trades_outside_test"] == 0
    assert report["duplicate_folds"] == 0
    assert report["aggregate_mismatches"] == 0


def test_account_oos_validation_detects_trade_outside_test(tmp_path):
    storage = prepare_valid_oos(tmp_path)
    path = storage / "results" / "account_oos_trades.parquet"
    trades = pd.read_parquet(path)
    trades.loc[trades.index[0], "entry_date"] = pd.Timestamp("2025-01-15 00:00:00+00:00")
    trades.to_parquet(path)

    report = validate_account_oos_outputs(storage, "run-a")

    assert report["status"] == "failed"
    assert report["trades_outside_test"] == 1


def test_account_oos_validation_detects_duplicate_fold(tmp_path):
    storage = prepare_valid_oos(tmp_path)
    path = storage / "results" / "account_oos_summary.parquet"
    summary = pd.read_parquet(path)
    pd.concat([summary, summary.iloc[[0]]], ignore_index=True).to_parquet(path)

    report = validate_account_oos_outputs(storage, "run-a")

    assert report["status"] == "failed"
    assert report["duplicate_folds"] == 1


def test_account_oos_validation_detects_mixed_run_id(tmp_path):
    storage = prepare_valid_oos(tmp_path)
    path = storage / "results" / "account_oos_summary.parquet"
    summary = pd.read_parquet(path)
    summary.loc[summary.index[0], "run_id"] = "run-b"
    summary.to_parquet(path)

    report = validate_account_oos_outputs(storage, "run-a")

    assert report["status"] == "failed"
    assert report["mixed_run_ids"] == 1


def test_account_oos_validation_detects_aggregate_mismatch(tmp_path):
    storage = prepare_valid_oos(tmp_path)
    path = storage / "results" / "account_oos_aggregate.parquet"
    aggregate = pd.read_parquet(path)
    aggregate.loc[aggregate.index[0], "median_oos_return"] = 123.0
    aggregate.to_parquet(path)

    report = validate_account_oos_outputs(storage, "run-a")

    assert report["status"] == "failed"
    assert report["aggregate_mismatches"] >= 1
