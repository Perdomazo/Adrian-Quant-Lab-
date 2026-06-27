from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from research_lab.account_candidate import run_account_candidate
from research_lab.account_oos import run_account_oos
from research_lab.account_oos_validation import validate_account_oos_outputs
from research_lab.account_simulator import (
    AccountConfig,
    load_account_config,
    run_account_simulator,
    simulate_account,
    validate_cli_profile,
    with_pair_universe,
)
from research_lab.account_validation import validate_account_outputs
from research_lab.profile_paths import profile_results_dir
from research_lab.warehouse import feature_path
from tests.test_account_candidate import base_rules, setup_candidate_storage
from tests.test_account_simulator import cfg, entry_signal, exit_signal, features


def freqtrade_cfg(**overrides) -> AccountConfig:
    base = dict(cfg().__dict__)
    base.update(
        {
            "execution_profile": "freqtrade",
            "execution_model": "freqtrade_backtest_v1",
            "allocation_policy": "pairlist_sequential",
            "cost_model_version": "freqtrade-backtest-costs-v1",
            "entry_slippage": 0.0,
            "exit_slippage": 0.0,
            "stop_slippage": 0.0,
        }
    )
    base.update(overrides)
    return AccountConfig.from_dict(base)


def multi_features(pairs: list[str]) -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        data.update(
            features(
                pair=pair,
                rows=[
                    ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
                    ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
                    ("2025-01-01 02:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
                ],
            )
        )
    return data


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_research_and_freqtrade_configs_load():
    research, research_version, _ = load_account_config(
        Path("research_lab/config/account_rules_research.json")
    )
    freqtrade, freqtrade_version, _ = load_account_config(
        Path("research_lab/config/account_rules_freqtrade.json")
    )

    assert research.execution_profile == "research"
    assert research.allocation_policy == "pro_rata"
    assert research_version == "account-rules-research-v1"
    assert freqtrade.execution_profile == "freqtrade"
    assert freqtrade.allocation_policy == "pairlist_sequential"
    assert freqtrade.entry_slippage == 0.0
    assert freqtrade_version == "account-rules-freqtrade-v1"


def test_unknown_profile_and_cli_mismatch_fail():
    with pytest.raises(ValueError, match="Unsupported execution profile"):
        AccountConfig.from_dict({**cfg().__dict__, "execution_profile": "bad"})

    with pytest.raises(ValueError, match="does not match"):
        validate_cli_profile(cfg(), "freqtrade")


def test_freqtrade_rejects_nonzero_slippage():
    with pytest.raises(ValueError, match="zero slippage"):
        freqtrade_cfg(entry_slippage=0.0001)


def test_freqtrade_rejects_pro_rata_policy():
    with pytest.raises(ValueError, match="pairlist_sequential"):
        freqtrade_cfg(allocation_policy="pro_rata")


def test_research_rejects_pairlist_policy():
    with pytest.raises(ValueError, match="pro_rata"):
        AccountConfig.from_dict({**cfg().__dict__, "allocation_policy": "pairlist_sequential"})


def test_pairlist_sequential_uses_pair_universe_order_and_slots():
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    config = with_pair_universe(
        freqtrade_cfg(max_open_positions=2, fee=0.0),
        ["SOL/USDT", "BTC/USDT", "ETH/USDT"],
    )
    result = simulate_account(
        pd.DataFrame([entry_signal(pair=pair, risk=0.02) for pair in pairs]),
        multi_features(pairs),
        config,
        "run-a",
        "edge_a",
        "1h",
    )

    trades = result.trades.sort_values("allocation_sequence")
    assert trades["pair"].tolist() == ["SOL/USDT", "BTC/USDT"]
    assert set(result.rejections["pair"]) == {"ETH/USDT"}
    assert result.rejections.iloc[0]["reason"] == "max_positions"
    assert trades["pair_priority"].tolist() == [0, 1]


def test_changing_pair_universe_changes_selected_slots():
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    config = with_pair_universe(
        freqtrade_cfg(max_open_positions=1, fee=0.0),
        ["ETH/USDT", "SOL/USDT", "BTC/USDT"],
    )
    result = simulate_account(
        pd.DataFrame([entry_signal(pair=pair, risk=0.02) for pair in pairs]),
        multi_features(pairs),
        config,
        "run-a",
        "edge_a",
        "1h",
    )

    assert result.trades.iloc[0]["pair"] == "ETH/USDT"
    assert set(result.rejections["pair"]) == {"BTC/USDT", "SOL/USDT"}


def test_freqtrade_does_not_allocate_pro_rata():
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    config = with_pair_universe(
        freqtrade_cfg(
            fee=0.0,
            risk_per_trade=1.0,
            max_open_positions=2,
            max_total_exposure=0.75,
            max_pair_exposure=0.30,
            max_stake=1000.0,
            min_stake=1.0,
        ),
        pairs,
    )
    result = simulate_account(
        pd.DataFrame([entry_signal(pair=pair, risk=0.01) for pair in pairs]),
        multi_features(pairs),
        config,
        "run-a",
        "edge_a",
        "1h",
    )

    trades = result.trades.sort_values("allocation_sequence")
    assert len(trades) == 2
    assert trades["stake"].iloc[0] == pytest.approx(300.0 * 0.999)
    assert trades["stake"].iloc[1] == pytest.approx(300.0 * 0.999)
    assert set(result.rejections["reason"]) == {"max_positions"}


def test_research_keeps_pro_rata_policy():
    pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    config = cfg(
        fee=0.0,
        risk_per_trade=1.0,
        max_total_exposure=0.30,
        max_pair_exposure=0.30,
        max_stake=1000.0,
        min_stake=1.0,
    )
    result = simulate_account(
        pd.DataFrame([entry_signal(pair=pair, risk=0.01) for pair in pairs]),
        multi_features(pairs),
        config,
        "run-a",
        "edge_a",
        "1h",
    )

    assert len(result.trades) == 3
    assert result.trades["stake"].round(6).nunique() == 1
    assert result.summary["allocation_policy"] == "pro_rata"


def test_freqtrade_entry_price_uses_zero_slippage_open():
    result = simulate_account(
        pd.DataFrame([entry_signal(risk=0.02)]),
        features(
            rows=[
                ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
                ("2025-01-01 01:00:00+00:00", 101.0, 120.0, 99.0, 110.0),
            ]
        ),
        freqtrade_cfg(fee=0.0),
        "run-a",
        "edge_a",
        "1h",
    )

    assert result.trades.iloc[0]["entry_price"] == 101.0


def test_research_entry_price_applies_slippage():
    result = simulate_account(
        pd.DataFrame([entry_signal(risk=0.02)]),
        features(
            rows=[
                ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
                ("2025-01-01 01:00:00+00:00", 101.0, 120.0, 99.0, 110.0),
            ]
        ),
        cfg(fee=0.0, entry_slippage=0.001, exit_slippage=0.0),
        "run-a",
        "edge_a",
        "1h",
    )

    assert result.trades.iloc[0]["entry_price"] == pytest.approx(101.101)


def test_trade_metadata_contains_profile_fields():
    result = simulate_account(
        pd.DataFrame([entry_signal(risk=0.02)]),
        multi_features(["BTC/USDT"]),
        freqtrade_cfg(fee=0.0),
        "run-a",
        "edge_a",
        "1h",
    )

    trade = result.trades.iloc[0]
    assert trade["execution_profile"] == "freqtrade"
    assert trade["execution_model"] == "freqtrade_backtest_v1"
    assert trade["allocation_policy"] == "pairlist_sequential"
    assert trade["cost_model_version"] == "freqtrade-backtest-costs-v1"


def test_exits_free_slots_before_entries():
    data = multi_features(["BTC/USDT", "ETH/USDT"])
    config = with_pair_universe(
        freqtrade_cfg(max_open_positions=1, fee=0.0), ["BTC/USDT", "ETH/USDT"]
    )
    result = simulate_account(
        pd.DataFrame(
            [
                entry_signal(pair="BTC/USDT", risk=0.02),
                exit_signal(pair="BTC/USDT", date="2025-01-01 01:00:00+00:00"),
                entry_signal(pair="ETH/USDT", date="2025-01-01 01:00:00+00:00", risk=0.02),
            ]
        ),
        data,
        config,
        "run-a",
        "edge_a",
        "1h",
    )

    assert result.summary["entries_opened"] == 2
    assert result.trades["pair"].tolist() == ["BTC/USDT", "ETH/USDT"]


def test_run_account_simulator_profile_mismatch_fails(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        run_account_simulator(tmp_path, "kucoin", [], ["1h"], cfg(), "run-a", "freqtrade")


def test_profile_artifacts_do_not_overwrite(tmp_path):
    storage = tmp_path
    results = storage / "results"
    results.mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    pd.DataFrame([entry_signal(pair="BTC/USDT", risk=0.02)]).to_parquet(
        results / "edge_signals.parquet"
    )
    feature_path(storage, "kucoin", "BTC/USDT", "1h").parent.mkdir(parents=True)
    features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )["BTC/USDT"].to_parquet(feature_path(storage, "kucoin", "BTC/USDT", "1h"))

    run_account_simulator(storage, "kucoin", ["BTC/USDT"], ["1h"], cfg(), "run-a", "research")
    run_account_simulator(
        storage, "kucoin", ["BTC/USDT"], ["1h"], freqtrade_cfg(), "run-a", "freqtrade"
    )

    assert (profile_results_dir(storage, "research") / "account_summary.parquet").exists()
    assert (profile_results_dir(storage, "freqtrade") / "account_summary.parquet").exists()
    assert (results / "account_summary.parquet").exists()
    research = pd.read_parquet(profile_results_dir(storage, "research") / "account_summary.parquet")
    freqtrade = pd.read_parquet(
        profile_results_dir(storage, "freqtrade") / "account_summary.parquet"
    )
    assert research.iloc[0]["execution_profile"] == "research"
    assert freqtrade.iloc[0]["execution_profile"] == "freqtrade"


def test_account_validation_reads_profile_metadata(tmp_path):
    storage = tmp_path
    results = storage / "results"
    results.mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    pd.DataFrame([entry_signal(pair="BTC/USDT", risk=0.02)]).to_parquet(
        results / "edge_signals.parquet"
    )
    feature_path(storage, "kucoin", "BTC/USDT", "1h").parent.mkdir(parents=True)
    features(
        rows=[
            ("2025-01-01 00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            ("2025-01-01 01:00:00+00:00", 100.0, 120.0, 99.0, 110.0),
        ]
    )["BTC/USDT"].to_parquet(feature_path(storage, "kucoin", "BTC/USDT", "1h"))
    rules = tmp_path / "account_rules_freqtrade.json"
    rules.write_text(json.dumps({**freqtrade_cfg().__dict__, "version": "freqtrade-test"}))

    run_account_simulator(
        storage, "kucoin", ["BTC/USDT"], ["1h"], freqtrade_cfg(), "run-a", "freqtrade"
    )
    report = validate_account_outputs(storage, rules, "run-a", "freqtrade")

    assert report["status"] == "passed"
    assert report["execution_profile"] == "freqtrade"
    assert report["allocation_policy"] == "pairlist_sequential"


def test_oos_writes_freqtrade_profile_artifacts(tmp_path):
    storage = tmp_path
    (storage / "results").mkdir(parents=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    dates = pd.date_range("2025-01-01", "2025-04-15", freq="D", tz="UTC")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 120.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "composite_vol": 0.02,
        }
    )
    path = feature_path(storage, "kucoin", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    frame.to_parquet(path)
    pd.DataFrame([entry_signal(date="2025-02-02 00:00:00+00:00")]).to_parquet(
        storage / "results" / "edge_signals.parquet"
    )

    summary, *_ = run_account_oos(
        storage=storage,
        exchange="kucoin",
        pairs=["BTC/USDT"],
        timeframes=["1h"],
        config=freqtrade_cfg(),
        account_rules_version="freqtrade-test",
        pair_universe_version="universe-test",
        run_id="run-a",
        train_months=1,
        test_months=1,
        step_months=1,
        profile="freqtrade",
    )

    assert not summary.empty
    assert (profile_results_dir(storage, "freqtrade") / "account_oos_summary.parquet").exists()
    assert summary.iloc[0]["execution_profile"] == "freqtrade"


def test_oos_validation_fails_missing_freqtrade_artifacts(tmp_path):
    report = validate_account_oos_outputs(tmp_path, "run-a", "freqtrade")

    assert report["status"] == "failed"
    assert report["missing_artifacts"]


def test_candidate_id_changes_between_profiles(tmp_path):
    root, storage, rules_path = setup_candidate_storage(tmp_path)
    write_json(rules_path, base_rules(min_oos_folds=5))
    research_summary, _, _ = run_account_candidate(
        root, storage, rules_path, root / "research_lab" / "config" / "pair_universe.json", "run-a"
    )
    setup_candidate_storage(tmp_path)
    freqtrade_root = root
    profile = profile_results_dir(storage, "freqtrade")
    profile.mkdir(parents=True, exist_ok=True)
    for name in [
        "account_summary.parquet",
        "account_equity.parquet",
        "account_oos_summary.parquet",
        "account_oos_aggregate.parquet",
    ]:
        source = storage / "results" / name
        target = profile / name
        target.write_bytes(source.read_bytes())
    write_json(
        profile / "account_validation.json",
        {
            "run_id": "run-a",
            "status": "passed",
            "rules_version": "account-rules-freqtrade-v1",
            "rules_hash": "freqtrade-rules",
            "execution_profile": "freqtrade",
            "execution_model": "freqtrade_backtest_v1",
            "allocation_policy": "pairlist_sequential",
            "cost_model_version": "freqtrade-backtest-costs-v1",
        },
    )
    write_json(profile / "account_oos_validation.json", {"run_id": "run-a", "status": "passed"})
    freqtrade_summary, _, _ = run_account_candidate(
        freqtrade_root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
        profile="freqtrade",
    )

    assert research_summary.iloc[0]["candidate_id"] != freqtrade_summary.iloc[0]["candidate_id"]
