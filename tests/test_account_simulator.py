from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_lab.account_simulator import (
    ALLOCATION_CAPACITY_BUFFER,
    AccountConfig,
    simulate_account,
)


def cfg(**overrides) -> AccountConfig:
    base = {
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
    base.update(overrides)
    return AccountConfig(**base)


def features(
    pair: str = "BTC/USDT", rows: list[tuple[str, float, float, float, float]] | None = None
) -> dict[str, pd.DataFrame]:
    rows = rows or [
        ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
        ("2025-01-01 01:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
        ("2025-01-01 02:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
    ]
    return {
        pair: pd.DataFrame(
            {
                "date": [row[0] for row in rows],
                "open": [row[1] for row in rows],
                "high": [row[2] for row in rows],
                "low": [row[3] for row in rows],
                "close": [row[4] for row in rows],
                "volume": [100.0 for _ in rows],
                "composite_vol": [0.02 for _ in rows],
            }
        )
    }


def entry_signal(
    pair: str = "BTC/USDT",
    date: str = "2025-01-01 00:00:00+00:00",
    risk: float = 0.02,
    max_hold: int = 10,
) -> dict:
    return {
        "run_id": "run-a",
        "exchange": "kucoin",
        "pair": pair,
        "timeframe": "1h",
        "edge": "edge_a",
        "edge_version": "edges-v2",
        "signal_type": "entry",
        "signal_date": pd.Timestamp(date),
        "signal_index": 0,
        "signal_close": 100.0,
        "composite_vol_at_signal": risk,
        "max_hold": max_hold,
    }


def exit_signal(pair: str = "BTC/USDT", date: str = "2025-01-01 01:00:00+00:00") -> dict:
    payload = entry_signal(pair=pair, date=date)
    payload["signal_type"] = "exit"
    payload["composite_vol_at_signal"] = np.nan
    return payload


def run_sim(feature_data, signal_rows, config=None):
    return simulate_account(
        feature_data,
        pd.DataFrame(signal_rows),
        config or cfg(),
        "run-a",
        "edge_a",
        "1h",
    )


def test_no_signals_keeps_initial_cash():
    trades, equity, summary, rejections = run_sim(features(), [])

    assert trades.empty
    assert rejections.empty
    assert summary.iloc[0]["final_equity"] == pytest.approx(1000.0)
    assert summary.iloc[0]["trades"] == 0
    assert summary.iloc[0]["fees_paid"] == pytest.approx(0.0)
    assert equity.iloc[-1]["cash"] == pytest.approx(1000.0)


def test_take_profit_closes_with_expected_fee_and_pnl():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )
    config = cfg(fee=0.001, entry_slippage=0.0, exit_slippage=0.0)
    trades, _, summary, _ = run_sim(data, [entry_signal(risk=0.02)], config)

    stake = 1000.0 * 0.005 / 0.06
    exit_price = 100.0 * (1.0 + 0.10)
    entry_fee = stake * 0.001
    quantity = stake / 100.0
    exit_fee = quantity * exit_price * 0.001
    expected_pnl = quantity * exit_price - exit_fee - stake - entry_fee

    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "take_profit"
    assert trades.iloc[0]["exit_price"] == pytest.approx(exit_price)
    assert trades.iloc[0]["pnl"] == pytest.approx(expected_pnl)
    assert summary.iloc[0]["final_equity"] == pytest.approx(1000.0 + expected_pnl)


def test_stop_normal():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 101.0, 93.0, 95.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data, [entry_signal(risk=0.02)], cfg(fee=0.0, entry_slippage=0.0, stop_slippage=0.0)
    )

    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "stop"
    assert trades.iloc[0]["exit_price"] == pytest.approx(94.0)


def test_gap_below_stop_uses_open_as_base():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 101.0, 95.0, 100.0),
            ("2025-01-01 02:00:00+00:00", 90.0, 91.0, 85.0, 88.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data, [entry_signal(risk=0.02)], cfg(fee=0.0, entry_slippage=0.0, stop_slippage=0.0)
    )

    assert trades.iloc[0]["exit_reason"] == "stop"
    assert trades.iloc[0]["exit_price"] == pytest.approx(90.0)


def test_stop_and_take_profit_same_candle_uses_stop_first():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 112.0, 93.0, 105.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data, [entry_signal(risk=0.02)], cfg(fee=0.0, entry_slippage=0.0, stop_slippage=0.0)
    )

    assert trades.iloc[0]["exit_reason"] == "stop"
    assert trades.iloc[0]["exit_price"] == pytest.approx(94.0)


def test_entry_and_exit_signal_execute_on_next_open():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 101.0, 102.0, 100.0, 101.0),
            ("2025-01-01 02:00:00+00:00", 103.0, 104.0, 102.0, 103.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data,
        [entry_signal(risk=0.02), exit_signal(date="2025-01-01 01:00:00+00:00")],
        cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
    )

    assert trades.iloc[0]["entry_date"] == pd.Timestamp("2025-01-01 01:00:00+00:00")
    assert trades.iloc[0]["exit_date"] == pd.Timestamp("2025-01-01 02:00:00+00:00")
    assert trades.iloc[0]["exit_price"] == pytest.approx(103.0)
    assert trades.iloc[0]["exit_reason"] == "signal"


def test_max_hold_exits_on_next_open():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 02:00:00+00:00", 102.0, 103.0, 101.0, 102.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data,
        [entry_signal(risk=0.02, max_hold=1)],
        cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
    )

    assert trades.iloc[0]["exit_reason"] == "max_hold"
    assert trades.iloc[0]["exit_date"] == pd.Timestamp("2025-01-01 02:00:00+00:00")
    assert trades.iloc[0]["exit_price"] == pytest.approx(102.0)


def test_three_simultaneous_signals_allocate_pro_rata():
    data = {}
    signals = []
    for pair in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        data.update(features(pair=pair))
        signals.append(entry_signal(pair=pair, risk=0.01))
    config = cfg(
        fee=0.0,
        risk_per_trade=1.0,
        max_total_exposure=0.30,
        max_pair_exposure=0.30,
        max_stake=1000.0,
        min_stake=1.0,
    )
    _, equity, summary, rejections = run_sim(data, signals, config)

    first_invested = 1000.0 - equity.iloc[1]["cash"]
    assert first_invested == pytest.approx(300.0 * ALLOCATION_CAPACITY_BUFFER)
    assert summary.iloc[0]["max_concurrent_positions"] == 3
    assert rejections.empty


def test_more_signals_than_slots_opens_available_slots_only():
    data = {}
    signals = []
    for pair in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        data.update(features(pair=pair))
        signals.append(entry_signal(pair=pair, risk=0.01))
    config = cfg(
        fee=0.0,
        risk_per_trade=1.0,
        max_open_positions=2,
        max_total_exposure=0.75,
        max_pair_exposure=0.30,
        max_stake=1000.0,
        min_stake=1.0,
    )
    _, _, summary, rejections = run_sim(data, signals, config)

    assert summary.iloc[0]["entries_opened"] == 2
    assert summary.iloc[0]["max_concurrent_positions"] == 2
    assert len(rejections) == 1
    assert rejections.iloc[0]["reason"] == "max_positions"


def test_global_exposure_limit_is_never_exceeded():
    data = {}
    signals = []
    for pair in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        data.update(features(pair=pair))
        signals.append(entry_signal(pair=pair, risk=0.01))
    config = cfg(
        fee=0.0,
        risk_per_trade=1.0,
        max_total_exposure=0.25,
        max_pair_exposure=0.25,
        max_stake=1000.0,
        min_stake=1.0,
    )
    _, equity, summary, _ = run_sim(data, signals, config)

    assert equity["stake_exposure"].max() <= 0.25 + 1e-9
    assert summary.iloc[0]["max_stake_exposure"] <= 0.25 + 1e-9


def test_pair_exposure_limit_is_never_exceeded():
    config = cfg(
        fee=0.0,
        risk_per_trade=1.0,
        max_total_exposure=0.75,
        max_pair_exposure=0.10,
        max_stake=1000.0,
        min_stake=1.0,
    )
    _, equity, summary, _ = run_sim(features(), [entry_signal(risk=0.01)], config)

    assert equity["max_pair_stake_exposure"].max() <= 0.10 + 1e-9
    assert summary.iloc[0]["max_pair_stake_exposure"] <= 0.10 + 1e-9


def test_insufficient_capital_never_makes_cash_negative():
    config = cfg(fee=0.0, risk_per_trade=1.0, max_total_exposure=0.01, min_stake=20.0)
    trades, equity, summary, rejections = run_sim(features(), [entry_signal(risk=0.01)], config)

    assert trades.empty
    assert summary.iloc[0]["entries_opened"] == 0
    assert rejections.iloc[0]["reason"] == "below_min_stake"
    assert (equity["cash"] >= -0.000001).all()


def test_entry_and_exit_same_candle_prioritizes_exit():
    trades, _, summary, rejections = run_sim(
        features(),
        [entry_signal(risk=0.02), exit_signal(date="2025-01-01 00:00:00+00:00")],
        cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
    )

    assert trades.empty
    assert summary.iloc[0]["entries_opened"] == 0
    assert rejections.iloc[0]["reason"] == "exit_priority"


def test_trade_ids_are_unique_for_repeated_pair_trades():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
            ("2025-01-01 02:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 03:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )
    trades, _, _, _ = run_sim(
        data,
        [
            entry_signal(risk=0.02),
            entry_signal(date="2025-01-01 02:00:00+00:00", risk=0.02),
        ],
        cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
    )

    assert len(trades) == 2
    assert not trades["trade_id"].duplicated().any()
    assert not trades["position_id"].duplicated().any()


def test_invalid_account_config_is_rejected():
    payload = {
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
    payload["risk_per_trade"] = 0.0

    with pytest.raises(ValueError, match="risk_per_trade"):
        AccountConfig.from_dict(payload)


def test_reproducibility_same_inputs_same_outputs_except_run_id():
    data = features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )
    config = cfg(fee=0.001, entry_slippage=0.0, exit_slippage=0.0)
    result_a = run_sim(data, [entry_signal(risk=0.02)], config)
    result_b = simulate_account(
        data, pd.DataFrame([entry_signal(risk=0.02)]), config, "run-b", "edge_a", "1h"
    )

    for left, right in zip(result_a, result_b, strict=True):
        left_cmp = left.drop(columns=["run_id"], errors="ignore").reset_index(drop=True)
        right_cmp = right.drop(columns=["run_id"], errors="ignore").reset_index(drop=True)
        pd.testing.assert_frame_equal(left_cmp, right_cmp)
