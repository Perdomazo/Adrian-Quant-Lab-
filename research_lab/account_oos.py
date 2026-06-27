from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.account_simulator import (
    ACCOUNT_SIMULATOR_VERSION,
    AccountConfig,
    load_account_config,
    load_features,
    normalize_signals,
    parse_csv,
    simulate_account,
    validate_cli_profile,
    with_pair_universe,
)
from research_lab.profile_paths import LEGACY_PROFILE, profile_results_dir
from research_lab.validation_windows import TemporalFold, build_temporal_folds
from research_lab.warehouse import refresh_duckdb, write_parquet_atomic


ACCOUNT_OOS_VERSION = "account-oos-v1"

SUMMARY_COLUMNS = [
    "run_id",
    "edge",
    "edge_version",
    "timeframe",
    "fold",
    "train_start",
    "train_end",
    "test_start",
    "test_end",
    "pair_universe_version",
    "account_rules_version",
    "account_simulator_version",
    "account_oos_version",
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "cost_model_version",
    "available_pairs",
    "missing_pairs",
    "available_pair_count",
    "universe_pair_count",
    "pair_coverage_rate",
    "initial_cash",
    "final_equity",
    "total_return",
    "trades",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "drawdown_duration",
    "fees_paid",
    "max_exposure",
    "max_pair_exposure",
    "max_stake_exposure",
    "max_pair_stake_exposure",
    "max_entry_stake_exposure",
    "max_entry_pair_stake_exposure",
    "average_exposure",
    "max_concurrent_positions",
    "signals_seen",
    "entries_opened",
    "signals_rejected",
    "open_positions_end",
    "positive_fold",
]

TRADE_COLUMNS = [
    "trade_id",
    "position_id",
    "run_id",
    "edge",
    "edge_version",
    "pair",
    "timeframe",
    "signal_date",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "quantity",
    "stake",
    "entry_fee",
    "exit_fee",
    "fees_total",
    "stop_price",
    "take_profit_price",
    "entry_stake_exposure",
    "entry_pair_stake_exposure",
    "bars_held",
    "exit_reason",
    "pnl",
    "return_on_stake",
    "fold",
    "test_start",
    "test_end",
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "pair_priority",
    "allocation_sequence",
    "cost_model_version",
]

EQUITY_COLUMNS = [
    "run_id",
    "edge",
    "timeframe",
    "date",
    "cash",
    "market_value",
    "equity",
    "realized_pnl",
    "unrealized_pnl",
    "fees_paid",
    "exposure",
    "max_pair_exposure",
    "pair_exposures",
    "allocated_stake",
    "stake_exposure",
    "max_pair_stake_exposure",
    "pair_stake_exposures",
    "max_entry_stake_exposure",
    "max_entry_pair_stake_exposure",
    "open_positions",
    "drawdown",
    "fold",
    "test_start",
    "test_end",
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "cost_model_version",
]

REJECTION_COLUMNS = [
    "run_id",
    "edge",
    "pair",
    "timeframe",
    "signal_date",
    "execution_date",
    "reason",
    "desired_stake",
    "available_cash",
    "current_exposure",
    "open_positions",
    "fold",
    "test_start",
    "test_end",
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "pair_priority",
    "allocation_sequence",
    "cost_model_version",
]

AGGREGATE_COLUMNS = [
    "run_id",
    "edge",
    "edge_version",
    "timeframe",
    "pair_universe_version",
    "account_rules_version",
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "cost_model_version",
    "folds",
    "positive_folds",
    "positive_fold_rate",
    "median_oos_return",
    "mean_oos_return",
    "median_oos_profit_factor",
    "worst_oos_drawdown",
    "best_oos_return",
    "worst_oos_return",
    "total_oos_trades",
    "median_oos_trades",
    "min_pair_coverage_rate",
    "median_pair_coverage_rate",
    "account_oos_version",
]


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out[columns + [column for column in out.columns if column not in columns]]


def load_pair_universe(path: Path) -> tuple[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = str(payload.get("version", "unknown"))
    pairs = [str(pair) for pair in payload.get("pairs", [])]
    return version, pairs


def common_feature_dates(features_by_pair: dict[str, pd.DataFrame]) -> pd.Series:
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    normalized: list[pd.Series] = []
    for df in features_by_pair.values():
        if df.empty or "date" not in df.columns:
            continue
        dates = pd.to_datetime(df["date"], utc=True).dropna().sort_values()
        if dates.empty:
            continue
        ranges.append((pd.Timestamp(dates.min()), pd.Timestamp(dates.max())))
        normalized.append(dates)

    if not ranges or not normalized:
        return pd.Series(dtype="datetime64[ns, UTC]")

    start = max(item[0] for item in ranges)
    end = min(item[1] for item in ranges)
    if start > end:
        return pd.Series(dtype="datetime64[ns, UTC]")

    values = pd.concat(
        [dates[(dates >= start) & (dates <= end)] for dates in normalized],
        ignore_index=True,
    )
    return values.drop_duplicates().sort_values().reset_index(drop=True)


def all_feature_dates(features_by_pair: dict[str, pd.DataFrame]) -> pd.Series:
    values: list[pd.Series] = []
    for df in features_by_pair.values():
        if df.empty or "date" not in df.columns:
            continue
        dates = pd.to_datetime(df["date"], utc=True).dropna().sort_values()
        if not dates.empty:
            values.append(dates)
    if not values:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return (
        pd.concat(values, ignore_index=True).drop_duplicates().sort_values().reset_index(drop=True)
    )


def pair_dates(features_by_pair: dict[str, pd.DataFrame], pair: str) -> pd.Series:
    df = features_by_pair.get(pair)
    if df is None or df.empty or "date" not in df.columns:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df["date"], utc=True).dropna().sort_values()


def fold_pair_coverage(
    features_by_pair: dict[str, pd.DataFrame],
    pairs: list[str],
    fold: TemporalFold,
) -> dict[str, Any]:
    available_pairs: list[str] = []
    for pair in pairs:
        dates = pair_dates(features_by_pair, pair)
        if dates.empty:
            continue
        in_test = (dates >= fold.test_start) & (dates < fold.test_end)
        if bool(in_test.any()):
            available_pairs.append(pair)

    missing_pairs = [pair for pair in pairs if pair not in set(available_pairs)]
    universe_pair_count = len(pairs)
    available_pair_count = len(available_pairs)
    return {
        "available_pairs": available_pairs,
        "missing_pairs": missing_pairs,
        "available_pair_count": available_pair_count,
        "universe_pair_count": universe_pair_count,
        "pair_coverage_rate": (
            available_pair_count / universe_pair_count if universe_pair_count else 0.0
        ),
    }


def add_fold_metadata(
    frame: pd.DataFrame,
    fold: TemporalFold,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out["fold"] = fold.fold
    out["test_start"] = fold.test_start
    out["test_end"] = fold.test_end
    return out


def oos_summary_row(
    summary: dict[str, Any],
    fold: TemporalFold,
    pair_universe_version: str,
    account_rules_version: str,
    pair_coverage: dict[str, Any],
) -> dict[str, Any]:
    row = dict(summary)
    row.update(
        {
            "fold": fold.fold,
            "train_start": fold.train_start,
            "train_end": fold.train_end,
            "test_start": fold.test_start,
            "test_end": fold.test_end,
            "pair_universe_version": pair_universe_version,
            "account_rules_version": account_rules_version,
            "account_simulator_version": ACCOUNT_SIMULATOR_VERSION,
            "account_oos_version": ACCOUNT_OOS_VERSION,
            "execution_profile": summary.get("execution_profile"),
            "execution_model": summary.get("execution_model"),
            "allocation_policy": summary.get("allocation_policy"),
            "cost_model_version": summary.get("cost_model_version"),
            "available_pairs": pair_coverage["available_pairs"],
            "missing_pairs": pair_coverage["missing_pairs"],
            "available_pair_count": pair_coverage["available_pair_count"],
            "universe_pair_count": pair_coverage["universe_pair_count"],
            "pair_coverage_rate": pair_coverage["pair_coverage_rate"],
            "positive_fold": float(summary.get("total_return", 0.0)) > 0.0,
        }
    )
    return row


def aggregate_oos(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()

    grouped = summary.groupby(
        [
            "run_id",
            "edge",
            "edge_version",
            "timeframe",
            "pair_universe_version",
            "account_rules_version",
            "execution_profile",
            "execution_model",
            "allocation_policy",
            "cost_model_version",
        ],
        as_index=False,
    ).agg(
        folds=("fold", "nunique"),
        positive_folds=("positive_fold", "sum"),
        median_oos_return=("total_return", "median"),
        mean_oos_return=("total_return", "mean"),
        median_oos_profit_factor=("profit_factor", "median"),
        worst_oos_drawdown=("max_drawdown", "min"),
        best_oos_return=("total_return", "max"),
        worst_oos_return=("total_return", "min"),
        total_oos_trades=("trades", "sum"),
        median_oos_trades=("trades", "median"),
        min_pair_coverage_rate=("pair_coverage_rate", "min"),
        median_pair_coverage_rate=("pair_coverage_rate", "median"),
    )
    grouped["positive_fold_rate"] = grouped["positive_folds"] / grouped["folds"].replace(0, np.nan)
    grouped["account_oos_version"] = ACCOUNT_OOS_VERSION
    return grouped


def simulate_oos_for_edge(
    signals: pd.DataFrame,
    features_by_pair: dict[str, pd.DataFrame],
    config: AccountConfig,
    run_id: str,
    edge: str,
    timeframe: str,
    folds: list[TemporalFold],
    pair_universe_version: str,
    account_rules_version: str,
    pair_coverages: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[pd.DataFrame], list[pd.DataFrame], list[pd.DataFrame]]:
    summaries: list[dict[str, Any]] = []
    trades: list[pd.DataFrame] = []
    equity: list[pd.DataFrame] = []
    rejections: list[pd.DataFrame] = []

    edge_signals = signals[(signals["edge"] == edge) & (signals["timeframe"] == timeframe)].copy()
    for fold in folds:
        pair_coverage = pair_coverages[fold.fold]
        available_pairs = pair_coverage["available_pairs"]
        fold_features = {
            pair: features_by_pair[pair] for pair in available_pairs if pair in features_by_pair
        }
        fold_signals = edge_signals[edge_signals["pair"].isin(available_pairs)].copy()
        result = simulate_account(
            signals=fold_signals,
            features_by_pair=fold_features,
            config=config,
            run_id=run_id,
            edge=edge,
            timeframe=timeframe,
            start_date=fold.test_start,
            end_date=fold.test_end,
        )
        summaries.append(
            oos_summary_row(
                result.summary,
                fold,
                pair_universe_version,
                account_rules_version,
                pair_coverage,
            )
        )
        trades.append(add_fold_metadata(result.trades, fold))
        equity.append(add_fold_metadata(result.equity, fold))
        rejections.append(add_fold_metadata(result.rejections, fold))

    return summaries, trades, equity, rejections


def run_account_oos(
    storage: Path,
    exchange: str,
    pairs: list[str],
    timeframes: list[str],
    config: AccountConfig,
    account_rules_version: str,
    pair_universe_version: str,
    run_id: str,
    train_months: int,
    test_months: int,
    step_months: int,
    profile: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    active_profile = validate_cli_profile(config, profile or config.execution_profile)
    signals_path = storage / "results" / "edge_signals.parquet"
    if not signals_path.exists():
        raise FileNotFoundError(f"Missing {signals_path}. Run edge_engine first.")

    signals = normalize_signals(pd.read_parquet(signals_path))
    if run_id and "run_id" in signals.columns:
        signals = signals[signals["run_id"].astype(str) == str(run_id)].copy()

    all_summaries: list[dict[str, Any]] = []
    all_trades: list[pd.DataFrame] = []
    all_equity: list[pd.DataFrame] = []
    all_rejections: list[pd.DataFrame] = []

    for timeframe in timeframes:
        features = load_features(storage, exchange, pairs, timeframe)
        fold_dates = all_feature_dates(features)
        folds = build_temporal_folds(fold_dates, train_months, test_months, step_months)
        pair_coverages = {fold.fold: fold_pair_coverage(features, pairs, fold) for fold in folds}
        tf_signals = signals[signals["timeframe"] == timeframe].copy()
        edges = sorted(tf_signals["edge"].dropna().unique().tolist())
        for edge in edges:
            summaries, trades, equity, rejections = simulate_oos_for_edge(
                signals=tf_signals,
                features_by_pair=features,
                config=config,
                run_id=run_id,
                edge=edge,
                timeframe=timeframe,
                folds=folds,
                pair_universe_version=pair_universe_version,
                account_rules_version=account_rules_version,
                pair_coverages=pair_coverages,
            )
            all_summaries.extend(summaries)
            all_trades.extend(trades)
            all_equity.extend(equity)
            all_rejections.extend(rejections)

    summary_df = ensure_columns(pd.DataFrame(all_summaries), SUMMARY_COLUMNS)
    trades_df = ensure_columns(
        pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(),
        TRADE_COLUMNS,
    )
    equity_df = ensure_columns(
        pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame(),
        EQUITY_COLUMNS,
    )
    rejections_df = ensure_columns(
        pd.concat(all_rejections, ignore_index=True) if all_rejections else pd.DataFrame(),
        REJECTION_COLUMNS,
    )
    aggregate_df = ensure_columns(aggregate_oos(summary_df), AGGREGATE_COLUMNS)

    out_dir = profile_results_dir(storage, active_profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "account_oos_summary.parquet": summary_df,
        "account_oos_trades.parquet": trades_df,
        "account_oos_equity.parquet": equity_df,
        "account_oos_rejections.parquet": rejections_df,
        "account_oos_aggregate.parquet": aggregate_df,
    }
    for name, frame in outputs.items():
        write_parquet_atomic(frame, out_dir / name)
    if active_profile == LEGACY_PROFILE:
        legacy_dir = storage / "results"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        for name, frame in outputs.items():
            write_parquet_atomic(frame, legacy_dir / name)
    refresh_duckdb(storage)
    return summary_df, trades_df, equity_df, rejections_df, aggregate_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Run account-level out-of-sample simulation")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--exchange", default="kucoin")
    parser.add_argument("--pairs", default="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT")
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument(
        "--account-rules",
        "--rules",
        default="research_lab/config/account_rules.json",
        type=Path,
    )
    parser.add_argument(
        "--pair-universe",
        default="research_lab/config/pair_universe.json",
        type=Path,
    )
    parser.add_argument("--train-months", type=int, default=18)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--profile", default=None, choices=["research", "freqtrade"])
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID"))
    args = parser.parse_args()

    config, rules_version, _ = load_account_config(args.account_rules)
    universe_version, universe_pairs = load_pair_universe(args.pair_universe)
    if universe_pairs:
        config = with_pair_universe(config, universe_pairs)
    profile = validate_cli_profile(config, args.profile or config.execution_profile)
    cli_pairs = parse_csv(args.pairs)
    pairs = cli_pairs or universe_pairs
    if universe_pairs:
        pairs = [pair for pair in pairs if pair in universe_pairs]

    summary, _, _, _, aggregate = run_account_oos(
        storage=args.storage,
        exchange=args.exchange,
        pairs=pairs,
        timeframes=parse_csv(args.timeframes),
        config=config,
        account_rules_version=rules_version,
        pair_universe_version=universe_version,
        run_id=args.run_id or "manual",
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
        profile=profile,
    )

    if aggregate.empty:
        print("No account OOS results.")
        return
    out = aggregate[
        [
            "edge",
            "timeframe",
            "folds",
            "positive_fold_rate",
            "median_oos_return",
            "median_oos_profit_factor",
            "worst_oos_drawdown",
            "total_oos_trades",
        ]
    ].copy()
    out["positive_fold_rate"] = out["positive_fold_rate"].map(lambda value: f"{value:.2%}")
    out["median_oos_return"] = out["median_oos_return"].map(lambda value: f"{value:.2%}")
    out["worst_oos_drawdown"] = out["worst_oos_drawdown"].map(lambda value: f"{value:.2%}")
    print(out.sort_values(["positive_fold_rate", "median_oos_return"]).to_string(index=False))
    print(f"Account OOS rows: {len(summary)}")


if __name__ == "__main__":
    main()
