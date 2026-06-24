from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.config import load_json
from research_lab.run_manager import atomic_write_json, load_manifest


DEFAULT_PROMOTION_RULES = {
    "version": "promotion-rules-v1",
    "enabled": True,
    "dry_run_only": True,
    "min_observation_days": 14,
    "min_score": 70,
    "min_profit_factor": 1.2,
    "min_positive_fold_rate": 0.6,
    "min_median_oos_pf": 1.05,
    "min_median_oos_expectancy": 0.0,
    "min_worst_oos_drawdown": -0.30,
    "max_drawdown_floor": -0.30,
    "min_total_test_trades": 80,
    "allowed_timeframes": ["4h"],
    "allowed_edges": ["donchian_20_10", "ema_trend_20_50_100", "volatility_expansion_20"],
    "require_parity_test": True,
    "parity_report": "parity_report.json",
    "docker_image": "freqtradeorg/freqtrade:stable",
    "paper_wallet": 1000,
    "stake_amount": 50,
    "service_name": "freqtrade-paper-promoted",
    "strategy_name": "PromotedEdgeStrategy",
}


def load_rules(path: Path) -> dict:
    return load_json(path, DEFAULT_PROMOTION_RULES)


def positive_fold_table(wfo: pd.DataFrame) -> pd.DataFrame:
    if wfo.empty:
        return pd.DataFrame()
    grouped = wfo.groupby(["edge", "pair", "timeframe"], as_index=False).agg(
        folds=("fold", "count"),
        positive_folds=("expectancy", lambda x: int((x > 0).sum())),
        median_pf=("profit_factor", "median"),
        median_expectancy=("expectancy", "median"),
        total_test_trades=("trades", "sum"),
        worst_test_dd=("max_drawdown", "min"),
    )
    grouped["positive_fold_rate"] = grouped["positive_folds"] / grouped["folds"].replace(0, pd.NA)
    return grouped


def observation_days(history: pd.DataFrame, edge: str, pair: str, timeframe: str) -> int:
    if history.empty or "snapshot_time" not in history.columns:
        return 0
    subset = history[
        (history["edge"] == edge)
        & (history["pair"] == pair)
        & (history["timeframe"] == timeframe)
        & (history.get("verdict", "") == "candidate")
    ].copy()
    if subset.empty:
        return 0
    return int(pd.to_datetime(subset["snapshot_time"]).dt.date.nunique())


def consecutive_candidate_stats(
    history: pd.DataFrame, edge: str, pair: str, timeframe: str
) -> dict:
    if history.empty or "snapshot_time" not in history.columns:
        return {
            "consecutive_candidate_days": 0,
            "consecutive_candidate_runs": 0,
            "first_current_streak_date": None,
        }
    subset = history[
        (history["edge"] == edge) & (history["pair"] == pair) & (history["timeframe"] == timeframe)
    ].copy()
    if subset.empty or "verdict" not in subset.columns:
        return {
            "consecutive_candidate_days": 0,
            "consecutive_candidate_runs": 0,
            "first_current_streak_date": None,
        }

    subset["snapshot_time"] = pd.to_datetime(subset["snapshot_time"], utc=True)
    subset = subset.sort_values("snapshot_time")
    daily = subset.groupby(subset["snapshot_time"].dt.date, as_index=False).tail(1)
    streak_days: list[Any] = []
    streak_runs = 0
    previous_day = None

    for row in reversed(list(daily.itertuples(index=False))):
        current_day = row.snapshot_time.date()
        if getattr(row, "verdict") != "candidate":
            break
        if previous_day is not None and (previous_day - current_day).days != 1:
            break
        streak_days.append(current_day)
        previous_day = current_day

    if streak_days:
        first_day = min(streak_days)
        last_day = max(streak_days)
        run_subset = subset[
            (subset["snapshot_time"].dt.date >= first_day)
            & (subset["snapshot_time"].dt.date <= last_day)
            & (subset["verdict"] == "candidate")
        ]
        streak_runs = int(len(run_subset))
        first_current_streak_date = first_day.isoformat()
    else:
        first_current_streak_date = None

    return {
        "consecutive_candidate_days": int(len(streak_days)),
        "consecutive_candidate_runs": streak_runs,
        "first_current_streak_date": first_current_streak_date,
    }


def results_dir(storage: Path, run_id: str | None = None) -> Path:
    run_dir = storage / "results" / "runs" / run_id if run_id else None
    if run_dir and run_dir.exists():
        return run_dir
    return storage / "results"


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def preflight_gate(storage: Path, run_id: str | None) -> tuple[bool, str, dict]:
    rdir = results_dir(storage, run_id)
    manifest = load_manifest(storage, run_id) if run_id else {}
    quality = load_json_file(rdir / "data_quality_report.json")

    if run_id and manifest.get("status") != "success":
        return False, "Run manifest is not success.", {"manifest_status": manifest.get("status")}
    if quality and quality.get("status") != "passed":
        return (
            False,
            "Data quality gate did not pass.",
            {"data_quality_status": quality.get("status")},
        )
    if not quality:
        return False, "Missing data quality report.", {}
    return (
        True,
        "ok",
        {
            "data_quality_status": quality.get("status"),
            "expected_last_complete_candle": quality.get("expected_last_complete_candle"),
            "actual_last_available_candle": quality.get("actual_last_available_candle"),
            "last_complete_candle": quality.get("actual_last_available_candle")
            or quality.get("last_complete_candle"),
            "max_lag_bars": quality.get("max_lag_bars"),
            "allowed_lag_bars": quality.get("allowed_lag_bars"),
        },
    )


def parity_gate(storage: Path, rules: dict, run_id: str | None) -> tuple[bool, str, dict]:
    if not rules.get("require_parity_test", True):
        return True, "ok", {"parity_required": False}
    rdir = results_dir(storage, run_id)
    report_name = str(rules.get("parity_report", "parity_report.json"))
    report_path = rdir / report_name
    if not report_path.exists():
        return (
            False,
            "Missing parity test report.",
            {"parity_required": True, "parity_report": str(report_path)},
        )
    report = load_json_file(report_path)
    if report.get("status") != "passed":
        return (
            False,
            "Parity test did not pass.",
            {
                "parity_required": True,
                "parity_status": report.get("status"),
                "parity_report": str(report_path),
            },
        )
    return (
        True,
        "ok",
        {
            "parity_required": True,
            "parity_status": report.get("status"),
            "parity_report": str(report_path),
        },
    )


def select_candidates(storage: Path, rules: dict, run_id: str | None = None) -> pd.DataFrame:
    rdir = results_dir(storage, run_id)
    summary_path = rdir / "edge_summary.parquet"
    wfo_path = rdir / "walk_forward_summary.parquet"
    history_path = storage / "results" / "run_history.parquet"
    if not summary_path.exists() or not wfo_path.exists():
        return pd.DataFrame()

    summary = pd.read_parquet(summary_path)
    wfo = pd.read_parquet(wfo_path)
    if run_id:
        if "run_id" in summary.columns:
            summary = summary[summary["run_id"] == run_id]
        if "run_id" in wfo.columns:
            wfo = wfo[wfo["run_id"] == run_id]
    if summary.empty or wfo.empty:
        return pd.DataFrame()
    history = pd.read_parquet(history_path) if history_path.exists() else pd.DataFrame()
    folds = positive_fold_table(wfo)
    if folds.empty:
        return pd.DataFrame()

    merged = summary.merge(folds, on=["edge", "pair", "timeframe"], how="left")
    merged["observation_days"] = [
        observation_days(history, row.edge, row.pair, row.timeframe)
        for row in merged.itertuples(index=False)
    ]
    streaks = [
        consecutive_candidate_stats(history, row.edge, row.pair, row.timeframe)
        for row in merged.itertuples(index=False)
    ]
    if streaks:
        merged = pd.concat([merged.reset_index(drop=True), pd.DataFrame(streaks)], axis=1)
    else:
        merged["consecutive_candidate_days"] = 0
        merged["consecutive_candidate_runs"] = 0
        merged["first_current_streak_date"] = None
    min_consecutive_days = int(
        rules.get("min_consecutive_candidate_days", rules["min_observation_days"])
    )
    filtered = merged[
        (merged["verdict"] == "candidate")
        & (merged["score"] >= float(rules["min_score"]))
        & (merged["profit_factor"] >= float(rules["min_profit_factor"]))
        & (merged["max_drawdown"] >= float(rules["max_drawdown_floor"]))
        & (merged["positive_fold_rate"] >= float(rules["min_positive_fold_rate"]))
        & (merged["median_pf"] >= float(rules.get("min_median_oos_pf", 1.05)))
        & (merged["median_expectancy"] >= float(rules.get("min_median_oos_expectancy", 0.0)))
        & (merged["worst_test_dd"] >= float(rules.get("min_worst_oos_drawdown", -0.30)))
        & (merged["total_test_trades"] >= int(rules["min_total_test_trades"]))
        & (merged["consecutive_candidate_days"] >= min_consecutive_days)
        & (merged["timeframe"].isin(rules["allowed_timeframes"]))
        & (merged["edge"].isin(rules["allowed_edges"]))
    ].copy()
    return filtered.sort_values(["score", "profit_factor", "positive_fold_rate"], ascending=False)


def strategy_code(edge: str, timeframe: str, class_name: str) -> str:
    return f'''from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta

from freqtrade.strategy import IStrategy


class {class_name}(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "{timeframe}"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 240

    minimal_roi = {{"0": 100.0}}
    stoploss = -0.12
    use_exit_signal = True
    exit_profit_only = False

    promoted_edge = "{edge}"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["ema_20"] = ta.EMA(df, timeperiod=20)
        df["ema_50"] = ta.EMA(df, timeperiod=50)
        df["ema_100"] = ta.EMA(df, timeperiod=100)
        df["ema_50_slope"] = df["ema_50"].pct_change(10)
        df["rsi_14"] = ta.RSI(df, timeperiod=14)
        df["donchian_high_20"] = df["high"].rolling(20).max()
        df["donchian_low_10"] = df["low"].rolling(10).min()
        df["donchian_high_55"] = df["high"].rolling(55).max()
        df["donchian_low_20"] = df["low"].rolling(20).min()
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        session = df["date"].dt.floor("D") if "date" in df else pd.Series(0, index=df.index)
        pv = typical * df["volume"]
        df["session_vwap"] = pv.groupby(session).cumsum() / df["volume"].groupby(session).cumsum().replace(0, np.nan)
        atr = ta.ATR(df, timeperiod=14) / df["close"]
        df["composite_vol"] = atr
        df["vol_ratio"] = atr / atr.rolling(100).median()
        log_vol = np.log1p(df["volume"])
        df["volume_pct_100"] = log_vol.rolling(100, min_periods=30).apply(lambda x: (x <= x[-1]).mean(), raw=True)
        trend_raw = ((df["close"] / df["ema_50"] - 1.0) + (df["close"] / df["ema_100"] - 1.0) + df["ema_50_slope"].fillna(0)) / atr.replace(0, np.nan)
        df["regime_score"] = np.tanh(trend_raw.clip(-5, 5) / 2.5)
        df["bear_prob"] = (-df["regime_score"]).clip(lower=0)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        prev_high_20 = df["donchian_high_20"].shift(1)
        if self.promoted_edge == "donchian_20_10":
            enter = (df["close"] > prev_high_20) & (df["volume_pct_100"] > 0.35)
        elif self.promoted_edge == "ema_trend_20_50_100":
            enter = (df["close"] > df["ema_20"]) & (df["ema_20"] > df["ema_50"]) & (df["ema_50"] > df["ema_100"]) & (df["ema_50_slope"] > 0) & (df["bear_prob"] < 0.35)
        elif self.promoted_edge == "volatility_expansion_20":
            enter = (df["close"] > prev_high_20) & (df["close"] > df["session_vwap"]) & (df["vol_ratio"] > 1.15) & (df["volume_pct_100"] > 0.60) & (df["bear_prob"] < 0.40)
        else:
            enter = pd.Series(False, index=df.index)
        df.loc[enter & (df["volume"] > 0), ["enter_long", "enter_tag"]] = (1, self.promoted_edge)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        prev_low_10 = df["donchian_low_10"].shift(1)
        prev_low_20 = df["donchian_low_20"].shift(1)
        if self.promoted_edge == "donchian_20_10":
            exit_ = df["close"] < prev_low_10
        elif self.promoted_edge == "ema_trend_20_50_100":
            exit_ = (df["close"] < df["ema_50"]) | (df["bear_prob"] > 0.55)
        elif self.promoted_edge == "volatility_expansion_20":
            exit_ = (df["close"] < df["ema_20"]) | (df["close"] < prev_low_20)
        else:
            exit_ = pd.Series(False, index=df.index)
        df.loc[exit_, ["exit_long", "exit_tag"]] = (1, "promoted_exit")
        return df
'''


def paper_config(
    pair: str, timeframe: str, strategy_name: str, paper_wallet: float, stake_amount: float
) -> dict:
    return {
        "trading_mode": "spot",
        "max_open_trades": 1,
        "stake_currency": "USDT",
        "stake_amount": stake_amount,
        "tradable_balance_ratio": 0.99,
        "dry_run": True,
        "dry_run_wallet": paper_wallet,
        "cancel_open_orders_on_exit": True,
        "timeframe": timeframe,
        "fee": 0.001,
        "exchange": {
            "name": "kucoin",
            "key": "",
            "secret": "",
            "password": "",
            "ccxt_config": {"enableRateLimit": True},
            "ccxt_async_config": {"enableRateLimit": True},
            "pair_whitelist": [pair],
            "pair_blacklist": [],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "dataformat_ohlcv": "feather",
        "strategy": strategy_name,
        "internals": {"process_throttle_secs": 5},
    }


def compose_yaml(service_name: str, strategy_name: str, docker_image: str) -> str:
    return f"""services:
  {service_name}:
    image: {docker_image}
    restart: unless-stopped
    container_name: {service_name}
    volumes:
      - "./user_data:/freqtrade/user_data"
    command: >
      trade
      --logfile /freqtrade/user_data/logs/{service_name}.log
      --db-url sqlite:////freqtrade/user_data/{service_name}.sqlite
      --config /freqtrade/user_data/config.promoted.paper.json
      --strategy {strategy_name}
"""


def safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")


def class_token(value: str) -> str:
    parts = [part for part in safe_token(value).split("_") if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def unique_strategy_name(base_name: str, candidate: pd.Series, run_id: str | None) -> str:
    suffix = "".join(
        [
            class_token(str(candidate["edge"])),
            class_token(str(candidate["pair"])),
            class_token(str(candidate["timeframe"])),
            class_token((run_id or "manual").split("-")[-1]),
        ]
    )
    return f"{class_token(base_name)}{suffix}"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    with tmp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def write_paper_files(
    root: Path, candidate: pd.Series, rules: dict, run_id: str | None = None
) -> dict:
    promoted_dir = root / "user_data" / "promoted"
    strategy_dir = root / "user_data" / "strategies"
    promoted_dir.mkdir(parents=True, exist_ok=True)
    strategy_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = unique_strategy_name(str(rules["strategy_name"]), candidate, run_id)
    service_name = rules["service_name"]
    strategy_path = strategy_dir / f"{strategy_name}.py"
    config_path = root / "user_data" / "config.promoted.paper.json"
    compose_path = root / "docker-compose.paper.yml"
    metadata_path = promoted_dir / "current_promotion.json"

    atomic_write_text(
        strategy_path,
        strategy_code(str(candidate["edge"]), str(candidate["timeframe"]), strategy_name),
    )
    atomic_write_json(
        config_path,
        paper_config(
            str(candidate["pair"]),
            str(candidate["timeframe"]),
            strategy_name,
            float(rules["paper_wallet"]),
            float(rules["stake_amount"]),
        ),
    )
    atomic_write_text(
        compose_path, compose_yaml(service_name, strategy_name, str(rules["docker_image"]))
    )
    metadata = json.loads(candidate.to_json())
    metadata["run_id"] = run_id
    metadata["strategy_name"] = strategy_name
    atomic_write_json(metadata_path, metadata)
    return {
        "strategy_name": strategy_name,
        "strategy_path": str(strategy_path),
        "config_path": str(config_path),
        "compose_path": str(compose_path),
        "metadata_path": str(metadata_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote robust research candidates to Freqtrade dry-run paper"
    )
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--rules", default="research_lab/config/promotion_rules.json", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually start/update the paper container when gates pass",
    )
    parser.add_argument(
        "--run-id", default=None, help="Only promote from this completed reproducible run"
    )
    args = parser.parse_args()

    rules = load_rules(args.rules)
    out_dir = args.storage / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    decision_path = out_dir / "promotion_decision.json"
    run_decision_path = None
    if args.run_id:
        run_decision_path = out_dir / "runs" / args.run_id / "promotion_decision.json"
    deployment_path = out_dir / "deployment_manifest.json"
    run_deployment_path = (
        out_dir / "runs" / args.run_id / "deployment_manifest.json" if args.run_id else None
    )

    def write_decision(decision: dict) -> None:
        decision["run_id"] = args.run_id
        atomic_write_json(decision_path, decision)
        if run_decision_path:
            atomic_write_json(run_decision_path, decision)
            atomic_write_json(out_dir / "latest" / "promotion_decision.json", decision)

    def write_deployment(deployment: dict) -> None:
        deployment["run_id"] = args.run_id
        atomic_write_json(deployment_path, deployment)
        if run_deployment_path:
            atomic_write_json(run_deployment_path, deployment)
            atomic_write_json(out_dir / "latest" / "deployment_manifest.json", deployment)

    if not rules.get("enabled", False):
        write_decision({"status": "disabled", "rules_version": rules["version"]})
        print("Promotion disabled.")
        return

    gate_ok, gate_reason, gate_context = preflight_gate(args.storage, args.run_id)
    if not gate_ok:
        decision = {
            "status": "blocked",
            "rules_version": rules["version"],
            "reason": gate_reason,
            **gate_context,
        }
        write_decision(decision)
        print(json.dumps(decision, indent=2))
        return

    parity_ok, parity_reason, parity_context = parity_gate(args.storage, rules, args.run_id)
    gate_context = {**gate_context, **parity_context}
    if not parity_ok:
        decision = {
            "status": "blocked",
            "rules_version": rules["version"],
            "reason": parity_reason,
            **gate_context,
        }
        write_decision(decision)
        print(json.dumps(decision, indent=2))
        return

    candidates = select_candidates(args.storage, rules, args.run_id)
    if candidates.empty:
        decision = {
            "status": "no_candidate",
            "rules_version": rules["version"],
            "reason": "No edge passed promotion gates.",
            **gate_context,
        }
        write_decision(decision)
        print(json.dumps(decision, indent=2))
        return

    top = candidates.iloc[0]
    files = write_paper_files(args.root, top, rules, args.run_id)
    decision = {
        "status": "ready",
        "rules_version": rules["version"],
        "edge": top["edge"],
        "pair": top["pair"],
        "timeframe": top["timeframe"],
        "score": float(top["score"]),
        "profit_factor": float(top["profit_factor"]),
        "positive_fold_rate": float(top["positive_fold_rate"]),
        "median_oos_pf": float(top["median_pf"]),
        "median_oos_expectancy": float(top["median_expectancy"]),
        "worst_oos_drawdown": float(top["worst_test_dd"]),
        "observation_days": int(top["observation_days"]),
        "consecutive_candidate_days": int(top["consecutive_candidate_days"]),
        "consecutive_candidate_runs": int(top["consecutive_candidate_runs"]),
        "first_current_streak_date": top.get("first_current_streak_date"),
        "files": files,
        **gate_context,
    }
    write_decision(decision)
    print(json.dumps(decision, indent=2))

    if args.execute:
        starting = {**decision, "status": "starting"}
        write_decision(starting)
        write_deployment(
            {
                "status": "starting",
                "rules_version": rules["version"],
                "service_name": rules["service_name"],
                **gate_context,
            }
        )
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.yml",
                    "-f",
                    "docker-compose.paper.yml",
                    "up",
                    "-d",
                    rules["service_name"],
                ],
                cwd=args.root,
                check=True,
            )
            running = subprocess.check_output(
                ["docker", "inspect", "-f", "{{.State.Running}}", rules["service_name"]],
                cwd=args.root,
                text=True,
            ).strip()
            if running != "true":
                raise RuntimeError(f"Container {rules['service_name']} is not running.")
        except Exception as exc:
            failed = {**decision, "status": "deployment_failed", "deployment_error": str(exc)}
            write_decision(failed)
            write_deployment(
                {
                    "status": "deployment_failed",
                    "rules_version": rules["version"],
                    "service_name": rules["service_name"],
                    "error": str(exc),
                    **gate_context,
                }
            )
            print(json.dumps(failed, indent=2))
            raise SystemExit(2) from exc

        started = {**decision, "status": "started"}
        write_decision(started)
        write_deployment(
            {
                "status": "started",
                "rules_version": rules["version"],
                "service_name": rules["service_name"],
                "files": files,
                **gate_context,
            }
        )
        print(json.dumps(started, indent=2))


if __name__ == "__main__":
    main()
