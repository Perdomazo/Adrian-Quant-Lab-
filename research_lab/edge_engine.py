from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path

import numpy as np
import pandas as pd

from research_lab.config import load_decision_rules
from research_lab.warehouse import feature_path, refresh_duckdb, write_parquet_atomic


@dataclass(frozen=True)
class EdgeSpec:
    name: str
    description: str
    max_hold: int
    version: str = "edges-v2"


EDGES = [
    EdgeSpec(
        "donchian_20_10",
        "Long breakout over previous 20-bar high, exit below previous 10-bar low",
        120,
    ),
    EdgeSpec("ema_trend_20_50_100", "Long trend when close > EMA20 > EMA50 > EMA100", 120),
    EdgeSpec(
        "rsi_mean_reversion_25",
        "Long oversold RSI in non-bear regime, exit near RSI/bollinger mean",
        48,
    ),
    EdgeSpec(
        "volatility_expansion_20",
        "Long range expansion breakout with rising volume and volatility",
        96,
    ),
]


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def drawdown_stats(equity: pd.Series) -> tuple[float, int]:
    if equity.empty:
        return 0.0, 0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    in_drawdown = dd < 0
    groups = (in_drawdown != in_drawdown.shift(fill_value=False)).cumsum()
    durations = in_drawdown.groupby(groups).sum()
    return float(dd.min()), int(durations.max() if not durations.empty else 0)


def profit_factor(returns: pd.Series) -> float:
    wins = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return np.inf if wins > 0 else 0.0
    return float(wins / losses)


def monte_carlo_stats(returns: pd.Series, sims: int, seed: int, ruin_drawdown: float) -> dict:
    if returns.empty or sims <= 0:
        return {
            "mc_sims": sims,
            "mc_final_p05": np.nan,
            "mc_final_p50": np.nan,
            "mc_final_p95": np.nan,
            "mc_max_dd_p95": np.nan,
            "mc_prob_negative": np.nan,
            "mc_prob_ruin": np.nan,
        }

    rng = np.random.default_rng(seed)
    values = returns.to_numpy(dtype=float)
    finals = np.empty(sims)
    max_dds = np.empty(sims)
    for idx in range(sims):
        sample = rng.choice(values, size=len(values), replace=True)
        equity = pd.Series(np.cumprod(1.0 + sample))
        max_dds[idx] = drawdown_stats(equity)[0]
        finals[idx] = equity.iloc[-1] - 1.0

    return {
        "mc_sims": sims,
        "mc_final_p05": float(np.percentile(finals, 5)),
        "mc_final_p50": float(np.percentile(finals, 50)),
        "mc_final_p95": float(np.percentile(finals, 95)),
        "mc_max_dd_p95": float(np.percentile(max_dds, 5)),
        "mc_prob_negative": float(np.mean(finals < 0)),
        "mc_prob_ruin": float(np.mean(max_dds <= ruin_drawdown)),
    }


def stress_stats(returns: pd.Series, extra_costs: list[float]) -> dict:
    out: dict[str, float] = {}
    for cost in extra_costs:
        bps = int(round(cost * 10000))
        stressed = returns - cost
        out[f"stress_pf_{bps}bps"] = profit_factor(stressed)
        out[f"stress_expectancy_{bps}bps"] = (
            float(stressed.mean()) if not stressed.empty else np.nan
        )
    return out


def temporal_equity_metrics(df: pd.DataFrame, trades: pd.DataFrame) -> dict:
    if df.empty or trades.empty:
        return {"temporal_max_drawdown": 0.0, "drawdown_duration_bars": 0}

    timeline = df[["date", "close"]].sort_values("date").reset_index(drop=True)
    trade_iter = trades.sort_values("entry_date").reset_index(drop=True)
    dates = pd.to_datetime(timeline["date"]).to_numpy(dtype="datetime64[ms]").astype("int64")
    close = timeline["close"].to_numpy(dtype=float)
    equity_points = np.ones(len(timeline), dtype=float)
    realized_equity = 1.0
    cursor = 0

    def to_ms(value) -> int:
        return int(np.datetime64(pd.Timestamp(value).to_datetime64(), "ms").astype("int64"))

    for trade in trade_iter.itertuples(index=False):
        entry_ms = to_ms(trade.entry_date)
        exit_ms = to_ms(trade.exit_date)
        entry_pos = int(np.searchsorted(dates, entry_ms, side="left"))
        exit_pos = int(np.searchsorted(dates, exit_ms, side="right"))
        entry_pos = max(entry_pos, cursor)
        exit_pos = max(exit_pos, entry_pos)

        if cursor < entry_pos:
            equity_points[cursor:entry_pos] = realized_equity
        if entry_pos < exit_pos:
            entry_price = float(trade.entry_price)
            equity_points[entry_pos:exit_pos] = realized_equity * (
                close[entry_pos:exit_pos] / entry_price
            )
        realized_equity *= 1.0 + float(trade.net_return)
        cursor = exit_pos

    if cursor < len(equity_points):
        equity_points[cursor:] = realized_equity

    dd, duration = drawdown_stats(pd.Series(equity_points))
    return {"temporal_max_drawdown": dd, "drawdown_duration_bars": duration}


def summarize_trades(
    trades: pd.DataFrame,
    df: pd.DataFrame | None = None,
    rules: dict | None = None,
) -> dict:
    rules = rules or load_decision_rules()
    mc_rules = rules.get("monte_carlo", {})
    stress_rules = rules.get("stress", {})
    if trades.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "expectancy": np.nan,
            "profit_factor": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_trade": np.nan,
            "avg_win": np.nan,
            "avg_loss": np.nan,
            "temporal_max_drawdown": 0.0,
            "drawdown_duration_bars": 0,
            **monte_carlo_stats(
                pd.Series(dtype=float),
                int(mc_rules.get("sims", 1000)),
                int(mc_rules.get("seed", 42)),
                float(mc_rules.get("ruin_drawdown", -0.5)),
            ),
        }
    ret = trades["net_return"].astype(float)
    equity = (1.0 + ret).cumprod()
    trade_dd, trade_dd_duration = drawdown_stats(equity)
    temporal = (
        temporal_equity_metrics(df, trades)
        if df is not None
        else {"temporal_max_drawdown": trade_dd, "drawdown_duration_bars": trade_dd_duration}
    )
    mc = monte_carlo_stats(
        ret,
        int(mc_rules.get("sims", 1000)),
        int(mc_rules.get("seed", 42)),
        float(mc_rules.get("ruin_drawdown", -0.5)),
    )
    stress = stress_stats(
        ret, [float(item) for item in stress_rules.get("extra_costs", [0.0002, 0.0005, 0.001])]
    )
    std = ret.std(ddof=0)
    return {
        "trades": int(len(ret)),
        "win_rate": float((ret > 0).mean()),
        "expectancy": float(ret.mean()),
        "profit_factor": profit_factor(ret),
        "total_return": float(equity.iloc[-1] - 1.0),
        "max_drawdown": float(temporal["temporal_max_drawdown"]),
        "trade_max_drawdown": trade_dd,
        "temporal_max_drawdown": float(temporal["temporal_max_drawdown"]),
        "drawdown_duration_bars": int(temporal["drawdown_duration_bars"]),
        "sharpe_trade": float(ret.mean() / std * np.sqrt(len(ret)))
        if std and not np.isnan(std)
        else np.nan,
        "avg_win": float(ret[ret > 0].mean()) if (ret > 0).any() else np.nan,
        "avg_loss": float(ret[ret < 0].mean()) if (ret < 0).any() else np.nan,
        **mc,
        **stress,
    }


def add_scores(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    out = summary.copy()
    pf_score = ((out["profit_factor"].replace(np.inf, np.nan).fillna(0.0) - 1.0) / 0.5).clip(
        0, 1
    ) * 30
    exp_score = (out["expectancy"].fillna(0.0) / 0.005).clip(0, 1) * 20
    dd_score = ((out["max_drawdown"].fillna(-1.0) + 0.50) / 0.50).clip(0, 1) * 20
    mc_score = (
        1.0 - out.get("mc_prob_negative", pd.Series(1.0, index=out.index)).fillna(1.0)
    ).clip(0, 1) * 15
    stress_col = "stress_pf_5bps" if "stress_pf_5bps" in out.columns else None
    stress_score = (
        ((out[stress_col].replace(np.inf, np.nan).fillna(0.0) - 1.0) / 0.3).clip(0, 1) * 15
        if stress_col
        else 0
    )
    out["score"] = (pf_score + exp_score + dd_score + mc_score + stress_score).round(2)
    out.loc[out["trades"].fillna(0) <= 0, "score"] = 0.0
    return out


def add_verdict(summary: pd.DataFrame, rules: dict | None = None) -> pd.DataFrame:
    if summary.empty:
        return summary
    rules = rules or load_decision_rules()
    out = add_scores(summary)
    candidate_rules = rules.get("candidate", {})
    watch_rules = rules.get("watch", {})
    out["verdict"] = "reject"
    candidate = (
        (out["trades"] >= int(candidate_rules.get("min_trades", 80)))
        & (out["profit_factor"] >= float(candidate_rules.get("min_profit_factor", 1.15)))
        & (out["expectancy"] > float(candidate_rules.get("min_expectancy", 0.0)))
        & (out["max_drawdown"] > float(candidate_rules.get("min_max_drawdown", -0.35)))
        & (out["score"] >= float(candidate_rules.get("min_score", 0.0)))
    )
    watch = (
        (out["trades"] >= int(watch_rules.get("min_trades", 50)))
        & (out["profit_factor"] >= float(watch_rules.get("min_profit_factor", 1.0)))
        & (out["expectancy"] >= float(watch_rules.get("min_expectancy", 0.0)))
        & (out["max_drawdown"] > float(watch_rules.get("min_max_drawdown", -0.45)))
    )
    out.loc[watch, "verdict"] = "watch"
    out.loc[candidate, "verdict"] = "candidate"
    out["rules_version"] = rules.get("version", "unknown")
    return out


def edge_masks(df: pd.DataFrame, edge: str) -> tuple[pd.Series, pd.Series]:
    prev_high_20 = df["donchian_high_20"].shift(1)
    prev_low_10 = df["donchian_low_10"].shift(1)
    prev_low_20 = df["donchian_low_20"].shift(1)

    if edge == "donchian_20_10":
        entry = (df["close"] > prev_high_20) & (df["volume_pct_100"] > 0.35)
        exit_ = df["close"] < prev_low_10
        return entry.fillna(False), exit_.fillna(False)

    if edge == "ema_trend_20_50_100":
        entry = (
            (df["close"] > df["ema_20"])
            & (df["ema_20"] > df["ema_50"])
            & (df["ema_50"] > df["ema_100"])
            & (df["ema_50_slope"] > 0)
            & (df["bear_prob"] < 0.35)
        )
        exit_ = (df["close"] < df["ema_50"]) | (df["bear_prob"] > 0.55)
        return entry.fillna(False), exit_.fillna(False)

    if edge == "rsi_mean_reversion_25":
        entry = (
            (df["rsi_14"] < 25)
            & (df["bb_pos_20"] < 0.15)
            & (df["bear_prob"] < 0.45)
            & (df["volume_z_50"] > -0.75)
        )
        exit_ = (df["rsi_14"] > 52) | (df["close"] > df["bb_mid_20"]) | (df["bear_prob"] > 0.60)
        return entry.fillna(False), exit_.fillna(False)

    if edge == "volatility_expansion_20":
        vol_ratio = df["composite_vol"] / df["composite_vol"].rolling(100, min_periods=50).median()
        entry = (
            (df["close"] > prev_high_20)
            & (df["close"] > df["session_vwap"])
            & (vol_ratio > 1.15)
            & (df["volume_pct_100"] > 0.60)
            & (df["bear_prob"] < 0.40)
        )
        exit_ = (df["close"] < df["ema_20"]) | (df["close"] < prev_low_20)
        return entry.fillna(False), exit_.fillna(False)

    raise ValueError(f"Unknown edge: {edge}")


def signal_telemetry(
    df: pd.DataFrame,
    edge: str,
    maker_offset: float,
    maker_timeout_bars: int,
) -> dict:
    entry_mask, exit_mask = edge_masks(df, edge)
    entry_arr = entry_mask.to_numpy(dtype=bool)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    signal_idx = np.flatnonzero(entry_arr)
    expected_maker_fills = 0

    for idx in signal_idx:
        limit_price = close[idx] * (1.0 - maker_offset)
        start = idx + 1
        end = min(idx + 1 + max(maker_timeout_bars, 1), len(low))
        if start < end and np.nanmin(low[start:end]) <= limit_price:
            expected_maker_fills += 1

    signals = int(len(signal_idx))
    fill_rate = expected_maker_fills / signals if signals else np.nan
    return {
        "entry_signals": signals,
        "exit_signals": int(exit_mask.sum()),
        "expected_maker_fills": int(expected_maker_fills),
        "expected_maker_fill_rate": float(fill_rate) if not np.isnan(fill_rate) else np.nan,
        "signal_to_trade_rate": np.nan,
        "activity_state": "inactive_no_signals" if signals == 0 else "active",
    }


def backtest_edge(
    df: pd.DataFrame,
    edge: EdgeSpec,
    fee: float,
    slippage: float,
    stop_mult: float,
    take_profit_mult: float,
) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True)
    entry_mask, exit_mask = edge_masks(df, edge.name)
    dates = df["date"].to_numpy()
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    composite_vol = df["composite_vol"].to_numpy(dtype=float)
    entry_arr = entry_mask.to_numpy(dtype=bool)
    exit_arr = exit_mask.to_numpy(dtype=bool)
    trades = []
    in_trade = False
    entry_i = 0
    entry_price = 0.0
    stop_price = 0.0
    take_profit_price = 0.0
    signal_i = 0
    stop_distance = 0.0
    take_profit_distance = 0.0
    risk = 0.0

    for i in range(1, len(df) - 1):
        if not in_trade and entry_arr[i]:
            signal_i = i
            entry_i = i + 1
            entry_price = open_[i + 1] * (1.0 + slippage)
            risk = composite_vol[i]
            if np.isnan(risk) or risk <= 0:
                risk = 0.01
            stop_distance = float(np.clip(risk * stop_mult, 0.01, 0.12))
            take_profit_distance = float(np.clip(risk * take_profit_mult, 0.015, 0.25))
            stop_price = entry_price * (1.0 - stop_distance)
            take_profit_price = entry_price * (1.0 + take_profit_distance)
            in_trade = True
            continue

        if not in_trade:
            continue

        hold = i - entry_i
        current_low = low[i]
        current_high = high[i]
        signal_exit = exit_arr[i]
        should_exit = signal_exit or hold >= edge.max_hold
        exit_reason = "signal" if signal_exit else "max_hold"
        exit_i = i + 1
        exit_price = open_[i + 1] * (1.0 - slippage)

        if current_low <= stop_price:
            should_exit = True
            exit_reason = "stop"
            exit_i = i
            exit_price = min(stop_price, open_[i]) * (1.0 - slippage)
        elif current_high >= take_profit_price:
            should_exit = True
            exit_reason = "take_profit"
            exit_i = i
            exit_price = take_profit_price * (1.0 - slippage)

        if should_exit:
            gross = exit_price / entry_price - 1.0
            net = gross - (2.0 * fee)
            bars_held = exit_i - entry_i
            trades.append(
                {
                    "signal_date": dates[signal_i],
                    "signal_index": signal_i,
                    "entry_date": dates[entry_i],
                    "entry_index": entry_i,
                    "exit_date": dates[exit_i],
                    "exit_index": exit_i,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross_return": gross,
                    "net_return": net,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                    "stop_price_initial": stop_price,
                    "take_profit_price_initial": take_profit_price,
                    "stop_distance": stop_distance,
                    "take_profit_distance": take_profit_distance,
                    "composite_vol_at_signal": risk,
                    "entry_fee": fee,
                    "exit_fee": fee,
                    "entry_slippage": slippage,
                    "exit_slippage": slippage,
                    "max_hold": edge.max_hold,
                    "edge_version": edge.version,
                    "edge": edge.name,
                }
            )
            in_trade = False

    return pd.DataFrame(trades)


def signal_events(df: pd.DataFrame, edge: EdgeSpec) -> pd.DataFrame:
    ordered = df.sort_values("date").reset_index(drop=True)
    entry_mask, exit_mask = edge_masks(ordered, edge.name)
    entries = ordered.loc[entry_mask, ["date", "close", "composite_vol"]].copy()
    entries["signal_index"] = entries.index.astype(int)
    entries = entries.rename(
        columns={
            "date": "signal_date",
            "close": "signal_close",
            "composite_vol": "composite_vol_at_signal",
        }
    )
    entries["signal_type"] = "entry"
    entries["edge"] = edge.name
    entries["edge_version"] = edge.version
    entries["max_hold"] = edge.max_hold

    exits = ordered.loc[exit_mask, ["date", "close"]].copy()
    exits["signal_index"] = exits.index.astype(int)
    exits = exits.rename(columns={"date": "signal_date", "close": "signal_close"})
    exits["composite_vol_at_signal"] = np.nan
    exits["signal_type"] = "exit"
    exits["edge"] = edge.name
    exits["edge_version"] = edge.version
    exits["max_hold"] = edge.max_hold

    return pd.concat([entries, exits], ignore_index=True, sort=False)


def season_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for keys, group in trades.assign(year=pd.to_datetime(trades["exit_date"]).dt.year).groupby(
        ["edge", "exchange", "pair", "timeframe", "year"], dropna=False
    ):
        edge, exchange, pair, timeframe, year = keys
        summary = summarize_trades(
            group, rules={"monte_carlo": {"sims": 0}, "stress": {"extra_costs": []}}
        )
        summary.update(
            {
                "edge": edge,
                "exchange": exchange,
                "pair": pair,
                "timeframe": timeframe,
                "year": int(year),
            }
        )
        rows.append(summary)
    return add_scores(pd.DataFrame(rows))


def append_run_history(storage: Path, summary: pd.DataFrame, run_id: str) -> None:
    if summary.empty:
        return
    history_path = storage / "results" / "run_history.parquet"
    snapshot = summary.copy()
    snapshot["run_id"] = run_id
    snapshot["snapshot_time"] = pd.Timestamp.now(tz="UTC")
    if history_path.exists():
        current = pd.read_parquet(history_path)
        snapshot = pd.concat([current, snapshot], ignore_index=True)
    write_parquet_atomic(snapshot, history_path)


def run_edges(
    storage: Path,
    exchange: str,
    pairs: list[str],
    timeframes: list[str],
    fee: float,
    slippage: float,
    stop_mult: float,
    take_profit_mult: float,
    rules: dict | None = None,
    run_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rules = rules or load_decision_rules()
    summaries = []
    all_trades = []
    all_signals = []
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tested_combinations = len(pairs) * len(timeframes) * len(EDGES)
    combo_index = 0
    stress_rules = rules.get("stress", {})
    maker_offset = float(stress_rules.get("maker_offset", 0.001))
    maker_timeout_bars = int(stress_rules.get("maker_timeout_bars", 1))

    for pair in pairs:
        for timeframe in timeframes:
            path = feature_path(storage, exchange, pair, timeframe)
            if not path.exists():
                print(f"Skipping missing features: {path}")
                continue
            df = pd.read_parquet(path)
            for edge in EDGES:
                combo_index += 1
                signals = signal_events(df, edge)
                if not signals.empty:
                    signals["exchange"] = exchange
                    signals["pair"] = pair
                    signals["timeframe"] = timeframe
                    signals["run_id"] = run_id
                    signals["description"] = edge.description
                    all_signals.append(signals)
                trades = backtest_edge(df, edge, fee, slippage, stop_mult, take_profit_mult)
                if not trades.empty:
                    trades["exchange"] = exchange
                    trades["pair"] = pair
                    trades["timeframe"] = timeframe
                    trades["description"] = edge.description
                    trades["run_id"] = run_id
                    all_trades.append(trades)
                summary = summarize_trades(trades, df=df, rules=rules)
                telemetry = signal_telemetry(df, edge.name, maker_offset, maker_timeout_bars)
                if telemetry["entry_signals"]:
                    telemetry["signal_to_trade_rate"] = float(
                        len(trades) / telemetry["entry_signals"]
                    )
                if telemetry["entry_signals"] > 0 and len(trades) == 0:
                    telemetry["activity_state"] = "inactive_no_closed_trades"
                summary.update(
                    {
                        "exchange": exchange,
                        "pair": pair,
                        "timeframe": timeframe,
                        "edge": edge.name,
                        "description": edge.description,
                        "fee": fee,
                        "slippage": slippage,
                        "run_id": run_id,
                        "tested_combinations": tested_combinations,
                        "combo_index": combo_index,
                        **telemetry,
                    }
                )
                summaries.append(summary)

    summary_df = add_verdict(pd.DataFrame(summaries), rules=rules)
    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    signals_df = pd.concat(all_signals, ignore_index=True) if all_signals else pd.DataFrame()
    out_dir = storage / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(summary_df, out_dir / "edge_summary.parquet")
    write_parquet_atomic(trades_df, out_dir / "edge_trades.parquet")
    write_parquet_atomic(signals_df, out_dir / "edge_signals.parquet")
    season = season_summary(trades_df)
    if not season.empty:
        write_parquet_atomic(season, out_dir / "season_summary.parquet")
    append_run_history(storage, summary_df, run_id)
    refresh_duckdb(storage)
    return summary_df, trades_df


def walk_forward_folds(
    df: pd.DataFrame,
    train_months: int,
    test_months: int,
    step_months: int,
) -> list[tuple[int, pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    dates = pd.to_datetime(df["date"])
    start = dates.min()
    end = dates.max()
    if pd.isna(start) or pd.isna(end):
        return []

    folds = []
    fold = 1
    train_start = start
    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)
        if test_end > end:
            break
        folds.append((fold, train_start, train_end, train_end, test_end))
        fold += 1
        train_start = train_start + pd.DateOffset(months=step_months)
    return folds


def run_walk_forward(
    storage: Path,
    exchange: str,
    pairs: list[str],
    timeframes: list[str],
    fee: float,
    slippage: float,
    stop_mult: float,
    take_profit_mult: float,
    train_months: int,
    test_months: int,
    step_months: int,
    rules: dict | None = None,
    run_id: str | None = None,
) -> pd.DataFrame:
    rules = rules or load_decision_rules()
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fold_rules = {
        **rules,
        "monte_carlo": {"sims": 0},
    }
    rows = []

    for pair in pairs:
        for timeframe in timeframes:
            path = feature_path(storage, exchange, pair, timeframe)
            if not path.exists():
                print(f"Skipping missing features: {path}")
                continue
            df = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
            folds = walk_forward_folds(df, train_months, test_months, step_months)
            for edge in EDGES:
                for fold, train_start, train_end, test_start, test_end in folds:
                    test_df = df[(df["date"] >= test_start) & (df["date"] < test_end)].copy()
                    trades = backtest_edge(
                        test_df, edge, fee, slippage, stop_mult, take_profit_mult
                    )
                    summary = summarize_trades(trades, df=None, rules=fold_rules)
                    summary.update(
                        {
                            "exchange": exchange,
                            "pair": pair,
                            "timeframe": timeframe,
                            "edge": edge.name,
                            "description": edge.description,
                            "fold": fold,
                            "train_start": train_start,
                            "train_end": train_end,
                            "test_start": test_start,
                            "test_end": test_end,
                            "fee": fee,
                            "slippage": slippage,
                            "run_id": run_id,
                            "validation_type": "temporal_oos_fixed_params",
                        }
                    )
                    rows.append(summary)

    wfo = add_verdict(pd.DataFrame(rows), rules=rules)
    out_dir = storage / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(wfo, out_dir / "walk_forward_summary.parquet")
    refresh_duckdb(storage)
    return wfo


def parse_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run simple edge research on generated features")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--exchange", default="kucoin")
    parser.add_argument("--pairs", default="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT")
    parser.add_argument("--timeframes", default="1h")
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--stop-mult", type=float, default=3.0)
    parser.add_argument("--take-profit-mult", type=float, default=5.0)
    parser.add_argument("--decision-rules", default="research_lab/config/decision_rules.json")
    parser.add_argument(
        "--walk-forward", action="store_true", help="Also run rolling walk-forward test windows"
    )
    parser.add_argument(
        "--only-walk-forward",
        action="store_true",
        help="Skip full edge summary and only refresh temporal OOS folds",
    )
    parser.add_argument("--train-months", type=int, default=18)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument(
        "--run-id",
        default=os.environ.get("RUN_ID"),
        help="Shared reproducibility id for this lab run",
    )
    args = parser.parse_args()

    pairs = parse_csv_arg(args.pairs)
    timeframes = parse_csv_arg(args.timeframes)
    rules = load_decision_rules(args.decision_rules)
    if not args.only_walk_forward:
        summary, _ = run_edges(
            args.storage,
            args.exchange,
            pairs,
            timeframes,
            args.fee,
            args.slippage,
            args.stop_mult,
            args.take_profit_mult,
            rules,
            args.run_id,
        )
        if summary.empty:
            print("No results.")
            return
        cols = [
            "verdict",
            "score",
            "edge",
            "pair",
            "timeframe",
            "trades",
            "profit_factor",
            "expectancy",
            "total_return",
            "max_drawdown",
        ]
        print(
            summary.sort_values(["profit_factor", "expectancy"], ascending=False)[cols].to_string(
                index=False
            )
        )

    if args.walk_forward or args.only_walk_forward:
        wfo = run_walk_forward(
            args.storage,
            args.exchange,
            pairs,
            timeframes,
            args.fee,
            args.slippage,
            args.stop_mult,
            args.take_profit_mult,
            args.train_months,
            args.test_months,
            args.step_months,
            rules,
            args.run_id,
        )
        if not wfo.empty:
            agg = (
                wfo.groupby(["edge", "pair", "timeframe"], as_index=False)
                .agg(
                    folds=("fold", "count"),
                    positive_folds=("expectancy", lambda x: int((x > 0).sum())),
                    median_pf=("profit_factor", "median"),
                    median_expectancy=("expectancy", "median"),
                    total_test_trades=("trades", "sum"),
                )
                .sort_values(["median_pf", "median_expectancy"], ascending=False)
            )
            print("\nWalk-forward summary")
            print(agg.to_string(index=False))


if __name__ == "__main__":
    main()
