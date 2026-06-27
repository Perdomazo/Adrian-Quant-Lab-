from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from research_lab.account_candidate import make_candidate_id, run_account_candidate


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def base_rules(min_oos_folds: int = 5) -> dict:
    return {
        "version": "account-decision-rules-test",
        "eligibility": {
            "require_data_quality_passed": True,
            "require_account_validation_passed": True,
            "require_account_oos_validation_passed": True,
            "min_oos_folds": min_oos_folds,
            "min_observation_months": 0,
        },
        "candidate": {
            "min_full_trades": 80,
            "min_full_profit_factor": 1.10,
            "min_full_total_return": 0.0,
            "min_full_max_drawdown": -0.20,
            "min_oos_positive_fold_rate": 0.60,
            "min_oos_median_return": 0.0,
            "min_oos_median_profit_factor": 1.05,
            "min_oos_worst_drawdown": -0.25,
            "min_oos_total_trades": 60,
            "max_open_pnl_share": 0.25,
        },
        "watch": {
            "min_full_trades": 50,
            "min_full_profit_factor": 1.00,
            "min_full_total_return": -0.05,
            "min_full_max_drawdown": -0.35,
            "min_oos_folds": 3,
            "min_oos_positive_fold_rate": 0.50,
            "min_oos_median_return": -0.02,
            "min_oos_median_profit_factor": 1.00,
            "min_oos_worst_drawdown": -0.35,
            "min_oos_total_trades": 30,
        },
    }


def setup_candidate_storage(
    tmp_path,
    *,
    run_id: str = "run-a",
    manifest_status: str = "success",
    data_quality_status: str = "passed",
    account_validation_status: str = "passed",
    account_oos_validation_status: str = "passed",
    folds: int = 5,
    full_profit_factor: float = 1.20,
    full_total_return: float = 0.10,
    full_max_drawdown: float = -0.10,
    full_trades: int = 120,
    oos_positive_fold_rate: float = 0.80,
    oos_median_return: float = 0.03,
    oos_median_profit_factor: float = 1.12,
    oos_worst_drawdown: float = -0.10,
    oos_total_trades: int = 100,
    empty: bool = False,
) -> tuple[Path, Path, Path]:
    root = tmp_path
    storage = tmp_path / "storage"
    results = storage / "results"
    latest = results / "latest"
    run_dir = results / "runs" / run_id
    latest.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"path": ["synthetic"]}).to_parquet(storage / "ohlcv_manifest.parquet")
    rules_path = root / "research_lab" / "config" / "account_decision_rules.json"
    universe_path = root / "research_lab" / "config" / "pair_universe.json"
    write_json(rules_path, base_rules())
    write_json(
        universe_path,
        {"version": "core-universe-test", "exchange": "kucoin", "pairs": ["BTC/USDT"]},
    )
    manifest = {
        "run_id": run_id,
        "status": manifest_status,
        "data_quality_status": data_quality_status,
        "account_validation_status": account_validation_status,
        "account_oos_validation_status": account_oos_validation_status,
        "pair_universe_version": "core-universe-test",
        "pair_universe_hash": "universe-hash",
        "account_rules_version": "account-rules-test",
        "account_rules_hash": "account-rules-hash",
        "account_simulator_version": "account-sim-v2",
        "features_version": "features-v2",
        "source_commit": "abc123",
        "data_hash": "data-hash",
    }
    write_json(latest / "run_manifest.json", manifest)
    write_json(run_dir / "run_manifest.json", manifest)
    write_json(
        results / "data_quality_report.json",
        {"run_id": run_id, "status": data_quality_status},
    )
    write_json(
        results / "account_validation.json",
        {"run_id": run_id, "status": account_validation_status},
    )
    write_json(
        results / "account_oos_validation.json",
        {"run_id": run_id, "status": account_oos_validation_status},
    )
    if empty:
        pd.DataFrame(columns=["run_id", "edge", "edge_version", "timeframe"]).to_parquet(
            results / "account_summary.parquet"
        )
        pd.DataFrame(columns=["run_id", "edge", "edge_version", "timeframe"]).to_parquet(
            results / "account_oos_aggregate.parquet"
        )
        pd.DataFrame(columns=["run_id", "edge", "edge_version", "timeframe"]).to_parquet(
            results / "account_oos_summary.parquet"
        )
        pd.DataFrame(columns=["run_id", "edge", "timeframe", "date"]).to_parquet(
            results / "account_equity.parquet"
        )
        return root, storage, rules_path

    pd.DataFrame(
        [
            {
                "run_id": run_id,
                "edge": "edge_a",
                "edge_version": "edges-v2",
                "timeframe": "4h",
                "trades": full_trades,
                "total_return": full_total_return,
                "profit_factor": full_profit_factor,
                "max_drawdown": full_max_drawdown,
                "open_positions_end": 0,
            }
        ]
    ).to_parquet(results / "account_summary.parquet")
    pd.DataFrame(
        [
            {
                "run_id": run_id,
                "edge": "edge_a",
                "edge_version": "edges-v2",
                "timeframe": "4h",
                "folds": folds,
                "positive_folds": int(folds * oos_positive_fold_rate),
                "positive_fold_rate": oos_positive_fold_rate,
                "median_oos_return": oos_median_return,
                "mean_oos_return": oos_median_return,
                "median_oos_profit_factor": oos_median_profit_factor,
                "worst_oos_drawdown": oos_worst_drawdown,
                "best_oos_return": oos_median_return,
                "worst_oos_return": oos_median_return,
                "total_oos_trades": oos_total_trades,
                "median_oos_trades": oos_total_trades / max(folds, 1),
            }
        ]
    ).to_parquet(results / "account_oos_aggregate.parquet")
    pd.DataFrame(
        [
            {
                "run_id": run_id,
                "edge": "edge_a",
                "edge_version": "edges-v2",
                "timeframe": "4h",
                "fold": fold,
                "train_start": "2021-01-01T00:00:00Z",
                "test_end": "2024-01-01T00:00:00Z",
            }
            for fold in range(1, folds + 1)
        ]
    ).to_parquet(results / "account_oos_summary.parquet")
    pd.DataFrame(
        [
            {
                "run_id": run_id,
                "edge": "edge_a",
                "timeframe": "4h",
                "date": "2024-01-01T00:00:00Z",
                "equity": 1100.0,
                "unrealized_pnl": 0.0,
            }
        ]
    ).to_parquet(results / "account_equity.parquet")
    return root, storage, rules_path


def run_candidate(tmp_path, **kwargs):
    root, storage, rules_path = setup_candidate_storage(tmp_path, **kwargs)
    summary, decision, history = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        kwargs.get("run_id", "run-a"),
    )
    return summary, decision, history, root, storage, rules_path


def test_manifest_failed_produces_invalid_run(tmp_path):
    summary, _, _, *_ = run_candidate(tmp_path, manifest_status="failed")

    assert summary.iloc[0]["eligibility_status"] == "invalid_run"


def test_running_manifest_requires_explicit_pipeline_flag(tmp_path):
    root, storage, rules_path = setup_candidate_storage(
        tmp_path, manifest_status="running", folds=3
    )

    summary, _, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
    )
    assert summary.iloc[0]["eligibility_status"] == "invalid_run"

    allowed, decision, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
        allow_running_run=True,
    )
    assert allowed.iloc[0]["eligibility_status"] == "insufficient_history"
    assert decision["insufficient_history_count"] == 1


def test_data_quality_failed_blocks(tmp_path):
    summary, _, _, *_ = run_candidate(tmp_path, data_quality_status="failed")

    assert summary.iloc[0]["eligibility_status"] == "failed_validation"


def test_account_validation_failed_blocks(tmp_path):
    summary, _, _, *_ = run_candidate(tmp_path, account_validation_status="failed")

    assert summary.iloc[0]["eligibility_status"] == "failed_validation"


def test_oos_validation_failed_blocks(tmp_path):
    summary, _, _, *_ = run_candidate(tmp_path, account_oos_validation_status="failed")

    assert summary.iloc[0]["eligibility_status"] == "failed_validation"


def test_historical_run_uses_run_snapshot_before_global_artifacts(tmp_path):
    root, storage, rules_path = setup_candidate_storage(tmp_path, folds=3)
    results = storage / "results"
    run_dir = results / "runs" / "run-a"
    for name in [
        "data_quality_report.json",
        "account_validation.json",
        "account_oos_validation.json",
    ]:
        shutil.copy2(results / name, run_dir / name)

    write_json(results / "data_quality_report.json", {"run_id": "run-b", "status": "failed"})
    write_json(results / "account_validation.json", {"run_id": "run-b", "status": "failed"})
    write_json(results / "account_oos_validation.json", {"run_id": "run-b", "status": "failed"})

    summary, decision, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
    )

    assert summary.iloc[0]["eligibility_status"] == "insufficient_history"
    assert decision["insufficient_history_count"] == 1


def test_three_folds_with_minimum_five_is_insufficient_history(tmp_path):
    summary, decision, _, *_ = run_candidate(tmp_path, folds=3)

    assert summary.iloc[0]["eligibility_status"] == "insufficient_history"
    assert decision["insufficient_history_count"] == 1


def test_candidate_metrics_produce_candidate(tmp_path):
    summary, decision, _, *_ = run_candidate(tmp_path)

    assert summary.iloc[0]["eligibility_status"] == "eligible"
    assert summary.iloc[0]["account_verdict"] == "candidate"
    assert decision["candidate_count"] == 1


def test_hard_gate_failure_prevents_candidate(tmp_path):
    summary, _, _, *_ = run_candidate(
        tmp_path,
        full_profit_factor=0.80,
        full_total_return=-0.20,
        oos_median_profit_factor=0.80,
        oos_median_return=-0.10,
    )

    assert summary.iloc[0]["account_verdict"] == "reject"
    assert "candidate.full_profit_factor" in summary.iloc[0]["failed_rules"]


def test_watch_metrics_produce_watch(tmp_path):
    summary, _, _, *_ = run_candidate(
        tmp_path,
        full_profit_factor=1.02,
        full_total_return=-0.01,
        full_max_drawdown=-0.25,
        oos_positive_fold_rate=0.60,
        oos_median_return=-0.01,
        oos_median_profit_factor=1.01,
        oos_worst_drawdown=-0.20,
        oos_total_trades=50,
    )

    assert summary.iloc[0]["account_verdict"] == "watch"


def test_weak_metrics_produce_reject(tmp_path):
    summary, _, _, *_ = run_candidate(
        tmp_path,
        full_trades=10,
        full_profit_factor=0.70,
        full_total_return=-0.30,
        full_max_drawdown=-0.50,
        oos_positive_fold_rate=0.0,
        oos_median_profit_factor=0.50,
        oos_total_trades=5,
    )

    assert summary.iloc[0]["account_verdict"] == "reject"


def test_candidate_id_is_stable_for_same_input():
    payload = {
        "edge": "edge_a",
        "edge_version": "edges-v2",
        "timeframe": "4h",
        "pair_universe_version": "u1",
        "pair_universe_hash": "h1",
        "account_rules_version": "a1",
        "account_rules_hash": "h2",
        "account_simulator_version": "s1",
        "features_version": "f1",
    }

    assert make_candidate_id(payload) == make_candidate_id(dict(reversed(payload.items())))


def test_account_rules_change_changes_candidate_id(tmp_path):
    first, _, _, root, storage, rules_path = run_candidate(tmp_path)
    manifest_path = storage / "results" / "latest" / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["account_rules_hash"] = "different-rules"
    write_json(manifest_path, manifest)
    write_json(storage / "results" / "runs" / "run-a" / "run_manifest.json", manifest)

    second, _, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
    )

    assert first.iloc[0]["candidate_id"] != second.iloc[0]["candidate_id"]


def test_universe_change_changes_candidate_id(tmp_path):
    first, _, _, root, storage, rules_path = run_candidate(tmp_path)
    universe = root / "research_lab" / "config" / "pair_universe.json"
    write_json(
        universe,
        {"version": "core-universe-test", "exchange": "kucoin", "pairs": ["BTC/USDT", "ETH/USDT"]},
    )

    second, _, _ = run_account_candidate(root, storage, rules_path, universe, "run-a")

    assert first.iloc[0]["candidate_id"] != second.iloc[0]["candidate_id"]


def test_zero_candidates_does_not_fail(tmp_path):
    _, decision, _, *_ = run_candidate(tmp_path, empty=True)

    assert decision["status"] == "success"
    assert decision["rows"] == 0


def test_history_does_not_duplicate_same_run(tmp_path):
    first, _, history, root, storage, rules_path = run_candidate(tmp_path)
    second, _, history = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-a",
    )

    assert first.iloc[0]["candidate_id"] == second.iloc[0]["candidate_id"]
    assert len(history) == 1


def test_candidate_streak_resets_when_verdict_stops_being_candidate(tmp_path):
    first, _, _, root, storage, rules_path = run_candidate(tmp_path, run_id="run-a")
    assert first.iloc[0]["consecutive_candidate_runs"] == 1

    setup_candidate_storage(tmp_path, run_id="run-b")
    second, _, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-b",
    )
    assert second.iloc[0]["consecutive_candidate_runs"] == 2

    setup_candidate_storage(
        tmp_path,
        run_id="run-c",
        full_profit_factor=0.80,
        full_total_return=-0.20,
        oos_median_profit_factor=0.80,
        oos_median_return=-0.10,
    )
    third, _, _ = run_account_candidate(
        root,
        storage,
        rules_path,
        root / "research_lab" / "config" / "pair_universe.json",
        "run-c",
    )

    assert third.iloc[0]["account_verdict"] == "reject"
    assert third.iloc[0]["consecutive_candidate_runs"] == 0
