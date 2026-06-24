from __future__ import annotations

import json

import pandas as pd

from research_lab.account_simulator import simulate_account
from research_lab.account_validation import validate_account_outputs
from tests.test_account_simulator import cfg, entry_signal, features


def write_rules(path):
    payload = {
        "version": "account-rules-test",
        "initial_cash": 1000.0,
        "risk_per_trade": 0.005,
        "max_open_positions": 3,
        "max_total_exposure": 0.75,
        "max_pair_exposure": 0.30,
        "min_stake": 10.0,
        "max_stake": 250.0,
        "fee": 0.001,
        "entry_slippage": 0.0005,
        "exit_slippage": 0.0005,
        "stop_slippage": 0.001,
        "stop_mult": 3.0,
        "take_profit_mult": 5.0,
        "min_stop_distance": 0.01,
        "max_stop_distance": 0.12,
        "min_take_profit_distance": 0.015,
        "max_take_profit_distance": 0.25,
        "intrabar_policy": "stop_first",
        "allocation_policy": "pro_rata",
        "end_of_data_policy": "mark_to_market",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_outputs(storage, trades, equity, summary, rejections):
    results = storage / "results"
    results.mkdir(parents=True)
    trades.to_parquet(results / "account_trades.parquet")
    equity.to_parquet(results / "account_equity.parquet")
    summary.to_parquet(results / "account_summary.parquet")
    rejections.to_parquet(results / "account_rejections.parquet")


def valid_outputs():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )
    return simulate_account(
        data,
        pd.DataFrame([entry_signal(risk=0.02)]),
        cfg(fee=0.001, entry_slippage=0.0, exit_slippage=0.0),
        "run-a",
        "edge_a",
        "1h",
    )


def test_account_validation_passes_valid_outputs(tmp_path):
    rules = tmp_path / "account_rules.json"
    write_rules(rules)
    write_outputs(tmp_path, *valid_outputs())

    report = validate_account_outputs(tmp_path, rules, "run-a")

    assert report["status"] == "passed"
    assert report["negative_cash_rows"] == 0
    assert report["equity_identity_failures"] == 0
    assert report["exposure_violations"] == 0
    assert report["pair_exposure_violations"] == 0
    assert report["duplicate_trade_ids"] == 0


def test_account_validation_detects_negative_cash(tmp_path):
    rules = tmp_path / "account_rules.json"
    write_rules(rules)
    trades, equity, summary, rejections = valid_outputs()
    equity.loc[equity.index[-1], "cash"] = -1.0
    equity.loc[equity.index[-1], "equity"] = equity.loc[equity.index[-1], "market_value"] - 1.0
    write_outputs(tmp_path, trades, equity, summary, rejections)

    report = validate_account_outputs(tmp_path, rules, "run-a")

    assert report["status"] == "failed"
    assert report["negative_cash_rows"] == 1


def test_account_validation_detects_exposure_violation(tmp_path):
    rules = tmp_path / "account_rules.json"
    write_rules(rules)
    trades, equity, summary, rejections = valid_outputs()
    equity.loc[equity.index[-1], "stake_exposure"] = 0.90
    write_outputs(tmp_path, trades, equity, summary, rejections)

    report = validate_account_outputs(tmp_path, rules, "run-a")

    assert report["status"] == "failed"
    assert report["exposure_violations"] == 1
