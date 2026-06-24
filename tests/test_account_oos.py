from __future__ import annotations

import pandas as pd

from research_lab.account_oos import aggregate_oos, common_feature_dates, run_account_oos
from research_lab.warehouse import feature_path
from tests.test_account_simulator import cfg, entry_signal


def make_storage(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    return storage


def feature_frame(start: str = "2025-01-01", end: str = "2025-04-15") -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D", tz="UTC")
    out = pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "composite_vol": 0.02,
        }
    )
    out.loc[out["date"].isin(pd.to_datetime(["2025-02-03", "2025-03-03"], utc=True)), "high"] = (
        120.0
    )
    return out


def write_features(storage, pair: str = "BTC/USDT", timeframe: str = "1h") -> None:
    path = feature_path(storage, "kucoin", pair, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    feature_frame().to_parquet(path)


def write_signals(storage, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_parquet(storage / "results" / "edge_signals.parquet")


def run_oos(storage, run_id: str = "run-a"):
    return run_account_oos(
        storage=storage,
        exchange="kucoin",
        pairs=["BTC/USDT"],
        timeframes=["1h"],
        config=cfg(fee=0.0, entry_slippage=0.0, exit_slippage=0.0),
        account_rules_version="account-rules-test",
        pair_universe_version="core-universe-test",
        run_id=run_id,
        train_months=1,
        test_months=1,
        step_months=1,
    )


def test_common_feature_dates_uses_pair_overlap():
    left = feature_frame("2025-01-01", "2025-04-15")
    right = feature_frame("2025-02-01", "2025-03-15")

    dates = common_feature_dates({"BTC/USDT": left, "ETH/USDT": right})

    assert dates.min() == pd.Timestamp("2025-02-01 00:00:00+00:00")
    assert dates.max() == pd.Timestamp("2025-03-15 00:00:00+00:00")


def test_account_oos_ignores_signals_outside_test(tmp_path):
    storage = make_storage(tmp_path)
    write_features(storage)
    write_signals(
        storage,
        [
            entry_signal(date="2025-01-20 00:00:00+00:00"),
            entry_signal(date="2025-02-02 00:00:00+00:00"),
            entry_signal(date="2025-04-10 00:00:00+00:00"),
        ],
    )

    summary, trades, equity, rejections, aggregate = run_oos(storage)

    assert len(summary) >= 2
    assert len(trades) == 1
    assert trades.iloc[0]["entry_date"] == pd.Timestamp("2025-02-03 00:00:00+00:00")
    assert trades.iloc[0]["fold"] == 1
    assert not equity.empty
    assert rejections.empty
    assert aggregate.iloc[0]["total_oos_trades"] == 1


def test_account_oos_resets_account_by_fold(tmp_path):
    storage = make_storage(tmp_path)
    write_features(storage)
    write_signals(
        storage,
        [
            entry_signal(date="2025-02-02 00:00:00+00:00"),
            entry_signal(date="2025-03-02 00:00:00+00:00"),
        ],
    )

    summary, trades, equity, _, _ = run_oos(storage)

    assert set(trades["fold"]) == {1, 2}
    first_equity = (
        equity.sort_values("date")
        .groupby(["edge", "timeframe", "fold"], as_index=False)
        .first()[["fold", "equity"]]
    )
    assert (first_equity["equity"] == 1000.0).all()
    assert (summary["initial_cash"] == 1000.0).all()


def test_account_oos_aggregate_matches_summary(tmp_path):
    storage = make_storage(tmp_path)
    write_features(storage)
    write_signals(
        storage,
        [
            entry_signal(date="2025-02-02 00:00:00+00:00"),
            entry_signal(date="2025-03-02 00:00:00+00:00"),
        ],
    )

    summary, _, _, _, aggregate = run_oos(storage)
    expected = aggregate_oos(summary)

    pd.testing.assert_frame_equal(
        aggregate.sort_index(axis=1).reset_index(drop=True),
        expected.sort_index(axis=1).reset_index(drop=True),
    )


def test_account_oos_reproducible(tmp_path):
    storage = make_storage(tmp_path)
    write_features(storage)
    write_signals(storage, [entry_signal(date="2025-02-02 00:00:00+00:00")])

    first = run_oos(storage)
    second = run_oos(storage)

    for left, right in zip(first, second, strict=True):
        pd.testing.assert_frame_equal(left.reset_index(drop=True), right.reset_index(drop=True))
