from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import MISSING, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.profile_paths import LEGACY_PROFILE, profile_results_dir, validate_profile
from research_lab.warehouse import feature_path, refresh_duckdb, write_parquet_atomic


ACCOUNT_SIMULATOR_VERSION = "account-sim-v2"
MAX_FINITE_PROFIT_FACTOR = 999.0
ALLOCATION_CAPACITY_BUFFER = 0.999

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
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "pair_priority",
    "allocation_sequence",
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
    "execution_profile",
    "execution_model",
    "allocation_policy",
    "pair_priority",
    "allocation_sequence",
    "cost_model_version",
]


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out[columns + [column for column in out.columns if column not in columns]]


@dataclass(frozen=True)
class AccountConfig:
    initial_cash: float
    risk_per_trade: float
    max_open_positions: int
    max_total_exposure: float
    max_pair_exposure: float
    min_stake: float
    max_stake: float
    fee: float
    entry_slippage: float
    exit_slippage: float
    stop_slippage: float
    stop_mult: float
    take_profit_mult: float
    min_stop_distance: float
    max_stop_distance: float
    min_take_profit_distance: float
    max_take_profit_distance: float
    intrabar_policy: str
    allocation_policy: str
    end_of_data_policy: str
    execution_profile: str = "research"
    execution_model: str = "conservative_intrabar_v1"
    cost_model_version: str = "inline-costs"
    pair_priority_source: str = "pair_universe"
    pair_universe: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AccountConfig:
        fields = cls.__dataclass_fields__
        required = {
            name
            for name, item in fields.items()
            if item.default is MISSING and item.default_factory is MISSING
        }
        allowed = set(fields) | {"version"}
        missing = sorted(required - set(payload))
        extra = sorted(set(payload) - allowed)
        if missing:
            raise ValueError(f"Missing account rule fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"Unknown account rule fields: {', '.join(extra)}")
        values = {
            field_name: payload[field_name]
            for field_name in fields
            if field_name in payload and field_name != "pair_universe"
        }
        if "pair_universe" in payload:
            values["pair_universe"] = tuple(str(pair) for pair in payload["pair_universe"])
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:  # noqa: C901
        numeric_fields = [
            "initial_cash",
            "risk_per_trade",
            "max_total_exposure",
            "max_pair_exposure",
            "min_stake",
            "max_stake",
            "fee",
            "entry_slippage",
            "exit_slippage",
            "stop_slippage",
            "stop_mult",
            "take_profit_mult",
            "min_stop_distance",
            "max_stop_distance",
            "min_take_profit_distance",
            "max_take_profit_distance",
        ]
        for name in numeric_fields:
            value = float(getattr(self, name))
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be at least 1")
        if not 0 < self.max_total_exposure <= 1:
            raise ValueError("max_total_exposure must be in (0, 1]")
        if not 0 < self.max_pair_exposure <= 1:
            raise ValueError("max_pair_exposure must be in (0, 1]")
        if self.max_pair_exposure > self.max_total_exposure:
            raise ValueError("max_pair_exposure cannot exceed max_total_exposure")
        if self.min_stake <= 0 or self.max_stake < self.min_stake:
            raise ValueError("stake bounds are invalid")
        if min(self.fee, self.entry_slippage, self.exit_slippage, self.stop_slippage) < 0:
            raise ValueError("fees and slippage must be non-negative")
        if self.min_stop_distance <= 0 or self.max_stop_distance < self.min_stop_distance:
            raise ValueError("stop distance bounds are invalid")
        if (
            self.min_take_profit_distance <= 0
            or self.max_take_profit_distance < self.min_take_profit_distance
        ):
            raise ValueError("take-profit distance bounds are invalid")
        if self.stop_mult <= 0 or self.take_profit_mult <= 0:
            raise ValueError("stop_mult and take_profit_mult must be positive")
        if validate_profile(self.execution_profile) != self.execution_profile:
            raise ValueError("execution_profile is invalid")
        if self.intrabar_policy != "stop_first":
            raise ValueError("Only intrabar_policy=stop_first is supported")
        valid_allocation = {"pro_rata", "pairlist_sequential"}
        if self.allocation_policy not in valid_allocation:
            raise ValueError("allocation_policy must be pro_rata or pairlist_sequential")
        if self.execution_profile == "research":
            if self.allocation_policy != "pro_rata":
                raise ValueError("research profile requires allocation_policy=pro_rata")
            if self.execution_model != "conservative_intrabar_v1":
                raise ValueError(
                    "research profile requires execution_model=conservative_intrabar_v1"
                )
        if self.execution_profile == "freqtrade":
            if self.allocation_policy != "pairlist_sequential":
                raise ValueError("freqtrade profile requires allocation_policy=pairlist_sequential")
            if self.execution_model != "freqtrade_backtest_v1":
                raise ValueError("freqtrade profile requires execution_model=freqtrade_backtest_v1")
            if max(self.entry_slippage, self.exit_slippage, self.stop_slippage) != 0:
                raise ValueError("freqtrade profile requires zero slippage")
        if self.end_of_data_policy != "mark_to_market":
            raise ValueError("Only end_of_data_policy=mark_to_market is supported")


@dataclass
class Position:
    position_id: str
    edge: str
    edge_version: str
    pair: str
    timeframe: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_index: int
    quantity: float
    stake: float
    entry_price: float
    entry_fee: float
    stop_price: float
    take_profit_price: float
    stop_distance: float
    take_profit_distance: float
    max_hold: int
    entry_stake_exposure: float
    entry_pair_stake_exposure: float
    allocation_sequence: int
    pair_priority: int
    execution_profile: str
    execution_model: str
    allocation_policy: str
    cost_model_version: str
    bars_held: int = 0


@dataclass
class AccountState:
    initial_cash: float
    cash: float
    realized_pnl: float
    fees_paid: float
    positions: dict[str, Position]


@dataclass
class PendingEntry:
    edge: str
    edge_version: str
    pair: str
    timeframe: str
    signal_date: pd.Timestamp
    execute_date: pd.Timestamp
    signal_index: int
    risk: float
    max_hold: int
    allocation_sequence: int = -1
    pair_priority: int = 999999
    execution_profile: str = "research"
    execution_model: str = "conservative_intrabar_v1"
    allocation_policy: str = "pro_rata"
    cost_model_version: str = "inline-costs"


@dataclass
class SimulationResult:
    summary: dict[str, Any]
    trades: pd.DataFrame
    equity: pd.DataFrame
    rejections: pd.DataFrame


def load_account_config(path: Path) -> tuple[AccountConfig, str, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        AccountConfig.from_dict(payload),
        str(payload.get("version", "unknown")),
        sha256_file(path),
    )


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_pair_universe(path: Path | None) -> tuple[str, list[str]]:
    if path is None or not path.exists():
        return "unknown", []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("version", "unknown")), [str(pair) for pair in payload.get("pairs", [])]


def with_pair_universe(config: AccountConfig, pairs: list[str]) -> AccountConfig:
    return AccountConfig(**{**config.__dict__, "pair_universe": tuple(pairs)})


def validate_cli_profile(config: AccountConfig, profile: str) -> str:
    requested = validate_profile(profile)
    if config.execution_profile != requested:
        raise ValueError(
            f"CLI profile {requested} does not match config execution_profile "
            f"{config.execution_profile}"
        )
    return requested


def pair_priority_map(config: AccountConfig, pairs: list[str]) -> dict[str, int]:
    source = list(config.pair_universe) or list(pairs)
    return {pair: idx for idx, pair in enumerate(source)}


def position_key(edge: str, pair: str, timeframe: str) -> str:
    return f"{edge}|{pair}|{timeframe}"


def pending_priority(pending: PendingEntry) -> str:
    payload = "|".join(
        [
            pending.edge,
            pending.timeframe,
            pending.execute_date.isoformat(),
            pending.signal_date.isoformat(),
            pending.pair,
            str(pending.signal_index),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pending_sort_key(pending: PendingEntry, config: AccountConfig) -> tuple[Any, ...]:
    if config.allocation_policy == "pairlist_sequential":
        return (pending.execute_date, pending.pair_priority, pending.signal_date, pending.pair)
    return (pending_priority(pending),)


def prepare_features(features: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    prepared: dict[str, pd.DataFrame] = {}
    for pair, df in features.items():
        if df.empty:
            continue
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"], utc=True)
        out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        out["bar_index"] = out.index.astype(int)
        out["_date_ns"] = out["date"].map(lambda value: pd.Timestamp(value).value).astype("int64")
        prepared[pair] = out
    return prepared


def build_timeline(features: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    dates: list[pd.Timestamp] = []
    for df in features.values():
        dates.extend(pd.to_datetime(df["date"], utc=True).tolist())
    return sorted(set(dates))


def row_maps(features: dict[str, pd.DataFrame]) -> dict[str, dict[pd.Timestamp, Any]]:
    maps: dict[str, dict[pd.Timestamp, Any]] = {}
    for pair, df in features.items():
        maps[pair] = {row.date: row for row in df.itertuples(index=False)}
    return maps


def next_bar_date(
    features: dict[str, pd.DataFrame], pair: str, signal_date: pd.Timestamp
) -> pd.Timestamp | None:
    df = features.get(pair)
    if df is None or df.empty:
        return None
    timestamp = pd.Timestamp(signal_date)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    date_values = df["_date_ns"].to_numpy(dtype=np.int64)
    pos = int(np.searchsorted(date_values, timestamp.value, side="right"))
    if pos >= len(df):
        return None
    return pd.Timestamp(df["date"].iloc[pos])


def drawdown_stats(equity: pd.Series) -> tuple[float, int]:
    if equity.empty:
        return 0.0, 0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    in_drawdown = dd < 0
    groups = (in_drawdown != in_drawdown.shift(fill_value=False)).cumsum()
    durations = in_drawdown.groupby(groups).sum()
    return float(dd.min()), int(durations.max() if not durations.empty else 0)


def profit_factor_from_pnl(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    gross_profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gross_loss = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    if gross_loss == 0:
        return MAX_FINITE_PROFIT_FACTOR if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def normalize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals.copy()
    out = signals.copy()
    out["signal_date"] = pd.to_datetime(out["signal_date"], utc=True)
    out = out.sort_values(["signal_date", "pair", "signal_type"]).reset_index(drop=True)
    return out


def normalize_optional_timestamp(value: pd.Timestamp | str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def in_simulation_window(
    date: pd.Timestamp,
    start_date: pd.Timestamp | None,
    end_date: pd.Timestamp | None,
) -> bool:
    timestamp = normalize_optional_timestamp(date)
    if timestamp is None:
        return False
    if start_date is not None and timestamp < start_date:
        return False
    if end_date is not None and timestamp >= end_date:
        return False
    return True


def market_value(
    positions: dict[str, Position],
    rows_by_pair: dict[str, dict[pd.Timestamp, Any]],
    last_close: dict[str, float],
    date: pd.Timestamp,
    price_field: str = "close",
) -> float:
    value = 0.0
    for position in positions.values():
        row = rows_by_pair.get(position.pair, {}).get(date)
        price = (
            getattr(row, price_field)
            if row is not None
            else last_close.get(position.pair, position.entry_price)
        )
        value += position.quantity * float(price)
    return float(value)


def pair_market_value(
    pair: str,
    positions: dict[str, Position],
    rows_by_pair: dict[str, dict[pd.Timestamp, Any]],
    last_close: dict[str, float],
    date: pd.Timestamp,
) -> float:
    value = 0.0
    for position in positions.values():
        if position.pair != pair:
            continue
        row = rows_by_pair.get(pair, {}).get(date)
        price = row.open if row is not None else last_close.get(pair, position.entry_price)
        value += position.quantity * float(price)
    return float(value)


def pair_market_values(
    positions: dict[str, Position],
    rows_by_pair: dict[str, dict[pd.Timestamp, Any]],
    last_close: dict[str, float],
    date: pd.Timestamp,
    price_field: str = "close",
) -> dict[str, float]:
    values: dict[str, float] = {}
    for position in positions.values():
        row = rows_by_pair.get(position.pair, {}).get(date)
        price = (
            getattr(row, price_field)
            if row is not None
            else last_close.get(position.pair, position.entry_price)
        )
        values[position.pair] = values.get(position.pair, 0.0) + position.quantity * float(price)
    return values


def pair_stake_values(positions: dict[str, Position]) -> dict[str, float]:
    values: dict[str, float] = {}
    for position in positions.values():
        values[position.pair] = values.get(position.pair, 0.0) + position.stake
    return values


def total_stake(positions: dict[str, Position]) -> float:
    return sum(position.stake for position in positions.values())


def pair_stake(pair: str, positions: dict[str, Position]) -> float:
    return sum(position.stake for position in positions.values() if position.pair == pair)


def stake_capacity(capital: float, allocated: float, limit: float, fee: float) -> float:
    numerator = limit * capital - allocated
    if numerator <= 0:
        return 0.0
    return (numerator / (1.0 + limit * fee)) * ALLOCATION_CAPACITY_BUFFER


def record_rejection(
    rejections: list[dict[str, Any]],
    pending: PendingEntry,
    reason: str,
    desired_stake: float,
    state: AccountState,
    current_exposure: float,
    date: pd.Timestamp | None = None,
) -> None:
    rejections.append(
        {
            "run_id": None,
            "edge": pending.edge,
            "pair": pending.pair,
            "timeframe": pending.timeframe,
            "signal_date": pending.signal_date,
            "execution_date": date or pending.execute_date,
            "reason": reason,
            "desired_stake": float(desired_stake) if np.isfinite(desired_stake) else 0.0,
            "available_cash": float(state.cash),
            "current_exposure": float(current_exposure),
            "open_positions": len(state.positions),
            "execution_profile": pending.execution_profile,
            "execution_model": pending.execution_model,
            "allocation_policy": pending.allocation_policy,
            "pair_priority": pending.pair_priority,
            "allocation_sequence": pending.allocation_sequence,
            "cost_model_version": pending.cost_model_version,
        }
    )


def close_position_with_config(
    state: AccountState,
    position_slot: str,
    exit_date: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    trades: list[dict[str, Any]],
    run_id: str,
    config: AccountConfig,
) -> None:
    position = state.positions.pop(position_slot)
    gross_proceeds = position.quantity * exit_price
    exit_fee = gross_proceeds * config.fee
    net_proceeds = gross_proceeds - exit_fee
    pnl = net_proceeds - position.stake - position.entry_fee
    state.cash += net_proceeds
    state.realized_pnl += pnl
    state.fees_paid += exit_fee
    trades.append(
        {
            "trade_id": position.position_id,
            "position_id": position.position_id,
            "run_id": run_id,
            "edge": position.edge,
            "edge_version": position.edge_version,
            "pair": position.pair,
            "timeframe": position.timeframe,
            "signal_date": position.signal_date,
            "entry_date": position.entry_date,
            "exit_date": exit_date,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "quantity": position.quantity,
            "stake": position.stake,
            "entry_fee": position.entry_fee,
            "exit_fee": exit_fee,
            "fees_total": position.entry_fee + exit_fee,
            "stop_price": position.stop_price,
            "take_profit_price": position.take_profit_price,
            "entry_stake_exposure": position.entry_stake_exposure,
            "entry_pair_stake_exposure": position.entry_pair_stake_exposure,
            "bars_held": position.bars_held,
            "exit_reason": exit_reason,
            "pnl": pnl,
            "return_on_stake": pnl / position.stake if position.stake else np.nan,
            "execution_profile": position.execution_profile,
            "execution_model": position.execution_model,
            "allocation_policy": position.allocation_policy,
            "pair_priority": position.pair_priority,
            "allocation_sequence": position.allocation_sequence,
            "cost_model_version": position.cost_model_version,
        }
    )


def desired_entry(
    pending: PendingEntry,
    row: Any,
    state: AccountState,
    rows_by_pair: dict[str, dict[pd.Timestamp, Any]],
    last_close: dict[str, float],
    date: pd.Timestamp,
    config: AccountConfig,
) -> tuple[float, dict[str, float], str | None]:
    risk = float(pending.risk)
    if not np.isfinite(risk) or risk <= 0:
        return 0.0, {}, "invalid_risk"

    stop_distance = float(
        np.clip(risk * config.stop_mult, config.min_stop_distance, config.max_stop_distance)
    )
    take_profit_distance = float(
        np.clip(
            risk * config.take_profit_mult,
            config.min_take_profit_distance,
            config.max_take_profit_distance,
        )
    )
    current_exposure = market_value(state.positions, rows_by_pair, last_close, date, "open")
    equity = state.cash + current_exposure
    allocated_stake = total_stake(state.positions)
    allocation_capital = state.cash + allocated_stake
    if equity <= 0:
        return 0.0, {}, "insufficient_cash"

    total_capacity = stake_capacity(
        allocation_capital,
        allocated_stake,
        config.max_total_exposure,
        config.fee,
    )
    pair_capacity = stake_capacity(
        allocation_capital,
        pair_stake(pending.pair, state.positions),
        config.max_pair_exposure,
        config.fee,
    )
    cash_capacity = state.cash / (1.0 + config.fee)
    if total_capacity <= 0:
        return 0.0, {}, "max_total_exposure"
    if pair_capacity <= 0:
        return 0.0, {}, "max_pair_exposure"
    if cash_capacity <= 0:
        return 0.0, {}, "insufficient_cash"

    risk_cash = equity * config.risk_per_trade
    stake_by_risk = risk_cash / stop_distance
    desired_stake = min(
        stake_by_risk, config.max_stake, pair_capacity, total_capacity, cash_capacity
    )
    entry_price = float(row.open) * (1.0 + config.entry_slippage)
    return (
        desired_stake,
        {
            "stop_distance": stop_distance,
            "take_profit_distance": take_profit_distance,
            "entry_price": entry_price,
            "current_exposure": current_exposure,
            "equity": equity,
        },
        None,
    )


def simulate_account(  # noqa: C901
    signals: pd.DataFrame,
    features_by_pair: dict[str, pd.DataFrame],
    config: AccountConfig,
    run_id: str,
    edge: str,
    timeframe: str,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
) -> SimulationResult:
    start = normalize_optional_timestamp(start_date)
    end = normalize_optional_timestamp(end_date)
    features = prepare_features(features_by_pair)
    rows_by_pair = row_maps(features)
    timeline = [date for date in build_timeline(features) if in_simulation_window(date, start, end)]
    signals = normalize_signals(signals)
    if not signals.empty and (start is not None or end is not None):
        signal_dates = pd.to_datetime(signals["signal_date"], utc=True)
        mask = pd.Series(True, index=signals.index)
        if start is not None:
            mask &= signal_dates >= start
        if end is not None:
            mask &= signal_dates < end
        signals = signals[mask].copy().reset_index(drop=True)
    edge_version = (
        str(signals["edge_version"].dropna().iloc[0])
        if not signals.empty and "edge_version" in signals
        else "unknown"
    )
    state = AccountState(config.initial_cash, config.initial_cash, 0.0, 0.0, {})
    pending_entries: dict[str, PendingEntry] = {}
    pending_exits: dict[str, tuple[pd.Timestamp, str]] = {}
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    entries_opened = 0
    max_concurrent_positions = 0
    next_position_id = 1
    next_allocation_sequence = 1
    priorities = pair_priority_map(config, list(features.keys()))

    entry_signals = (
        signals[signals.get("signal_type", "") == "entry"] if not signals.empty else pd.DataFrame()
    )
    signals_by_date = (
        {date: group for date, group in signals.groupby("signal_date")} if not signals.empty else {}
    )

    for date in timeline:
        current_rows = {
            pair: rows_by_pair[pair][date] for pair in rows_by_pair if date in rows_by_pair[pair]
        }

        due_exit_ids = [
            pid for pid, (execute_date, _) in pending_exits.items() if execute_date <= date
        ]
        for position_id in sorted(due_exit_ids):
            if position_id not in state.positions:
                pending_exits.pop(position_id, None)
                continue
            position = state.positions[position_id]
            row = current_rows.get(position.pair)
            if row is None:
                _, exit_reason = pending_exits[position_id]
                next_date = next_bar_date(features, position.pair, date)
                if next_date is not None and in_simulation_window(next_date, start, end):
                    pending_exits[position_id] = (next_date, exit_reason)
                else:
                    pending_exits.pop(position_id, None)
                continue
            _, exit_reason = pending_exits[position_id]
            exit_price = float(row.open) * (1.0 - config.exit_slippage)
            close_position_with_config(
                state, position_id, date, exit_price, exit_reason, trades, run_id, config
            )
            pending_exits.pop(position_id, None)

        due_entries = [
            pending for pending in pending_entries.values() if pending.execute_date <= date
        ]
        candidates: list[tuple[PendingEntry, float, dict[str, float], Any]] = []
        current_exposure = market_value(state.positions, rows_by_pair, last_close, date, "open")
        for pending in sorted(due_entries, key=lambda item: pending_sort_key(item, config)):
            row = current_rows.get(pending.pair)
            if row is None:
                next_date = next_bar_date(features, pending.pair, date)
                if next_date is not None and in_simulation_window(next_date, start, end):
                    pending_entries[pending.pair] = PendingEntry(
                        edge=pending.edge,
                        edge_version=pending.edge_version,
                        pair=pending.pair,
                        timeframe=pending.timeframe,
                        signal_date=pending.signal_date,
                        execute_date=next_date,
                        signal_index=pending.signal_index,
                        risk=pending.risk,
                        max_hold=pending.max_hold,
                        allocation_sequence=pending.allocation_sequence,
                        pair_priority=pending.pair_priority,
                        execution_profile=pending.execution_profile,
                        execution_model=pending.execution_model,
                        allocation_policy=pending.allocation_policy,
                        cost_model_version=pending.cost_model_version,
                    )
                    continue
                record_rejection(
                    rejections, pending, "no_next_bar", 0.0, state, current_exposure, date
                )
                pending_entries.pop(pending.pair, None)
                continue
            if position_key(pending.edge, pending.pair, pending.timeframe) in state.positions:
                record_rejection(
                    rejections, pending, "already_open", 0.0, state, current_exposure, date
                )
                pending_entries.pop(pending.pair, None)
                continue
            desired_stake, details, reason = desired_entry(
                pending, row, state, rows_by_pair, last_close, date, config
            )
            if reason:
                record_rejection(
                    rejections, pending, reason, desired_stake, state, current_exposure, date
                )
                pending_entries.pop(pending.pair, None)
                continue
            candidates.append((pending, desired_stake, details, row))

        if candidates and config.allocation_policy == "pairlist_sequential":
            candidates = sorted(candidates, key=lambda item: pending_sort_key(item[0], config))
            for pending, _, _, row in candidates:
                current_exposure = market_value(
                    state.positions, rows_by_pair, last_close, date, "open"
                )
                if len(state.positions) >= config.max_open_positions:
                    record_rejection(
                        rejections,
                        pending,
                        "max_positions",
                        0.0,
                        state,
                        current_exposure,
                        date,
                    )
                    pending_entries.pop(pending.pair, None)
                    continue
                position_slot = position_key(pending.edge, pending.pair, pending.timeframe)
                if position_slot in state.positions:
                    record_rejection(
                        rejections, pending, "already_open", 0.0, state, current_exposure, date
                    )
                    pending_entries.pop(pending.pair, None)
                    continue
                desired_stake, details, reason = desired_entry(
                    pending, row, state, rows_by_pair, last_close, date, config
                )
                if reason:
                    record_rejection(
                        rejections, pending, reason, desired_stake, state, current_exposure, date
                    )
                    pending_entries.pop(pending.pair, None)
                    continue
                stake = desired_stake
                if stake < config.min_stake:
                    record_rejection(
                        rejections,
                        pending,
                        "below_min_stake",
                        stake,
                        state,
                        current_exposure,
                        date,
                    )
                    pending_entries.pop(pending.pair, None)
                    continue
                entry_price = details["entry_price"]
                entry_fee = stake * config.fee
                quantity = stake / entry_price
                stop_price = entry_price * (1.0 - details["stop_distance"])
                take_profit_price = entry_price * (1.0 + details["take_profit_distance"])
                post_entry_capital = state.cash + total_stake(state.positions) - entry_fee
                post_entry_stake = total_stake(state.positions) + stake
                post_entry_pair_stake = pair_stake(pending.pair, state.positions) + stake
                entry_stake_exposure = (
                    post_entry_stake / post_entry_capital if post_entry_capital > 0 else 0.0
                )
                entry_pair_stake_exposure = (
                    post_entry_pair_stake / post_entry_capital if post_entry_capital > 0 else 0.0
                )
                unique_position_id = f"{position_slot}|{next_position_id:06d}"
                next_position_id += 1
                sequence = next_allocation_sequence
                next_allocation_sequence += 1
                state.cash -= stake + entry_fee
                if state.cash < -0.000001:
                    raise RuntimeError("cash became negative after entry")
                state.fees_paid += entry_fee
                state.positions[position_slot] = Position(
                    position_id=unique_position_id,
                    edge=pending.edge,
                    edge_version=pending.edge_version,
                    pair=pending.pair,
                    timeframe=pending.timeframe,
                    signal_date=pending.signal_date,
                    entry_date=date,
                    entry_index=int(row.bar_index),
                    quantity=quantity,
                    stake=stake,
                    entry_price=entry_price,
                    entry_fee=entry_fee,
                    stop_price=stop_price,
                    take_profit_price=take_profit_price,
                    stop_distance=details["stop_distance"],
                    take_profit_distance=details["take_profit_distance"],
                    max_hold=pending.max_hold,
                    entry_stake_exposure=entry_stake_exposure,
                    entry_pair_stake_exposure=entry_pair_stake_exposure,
                    allocation_sequence=sequence,
                    pair_priority=pending.pair_priority,
                    execution_profile=config.execution_profile,
                    execution_model=config.execution_model,
                    allocation_policy=config.allocation_policy,
                    cost_model_version=config.cost_model_version,
                )
                entries_opened += 1
                max_concurrent_positions = max(max_concurrent_positions, len(state.positions))
                pending_entries.pop(pending.pair, None)
            candidates = []

        if candidates:
            slots = config.max_open_positions - len(state.positions)
            if slots <= 0:
                for pending, desired_stake, _, _ in candidates:
                    record_rejection(
                        rejections,
                        pending,
                        "max_positions",
                        desired_stake,
                        state,
                        current_exposure,
                        date,
                    )
                    pending_entries.pop(pending.pair, None)
                candidates = []
            elif len(candidates) > slots:
                candidates = sorted(candidates, key=lambda item: pending_sort_key(item[0], config))
                rejected = candidates[slots:]
                candidates = candidates[:slots]
                for pending, desired_stake, _, _ in rejected:
                    record_rejection(
                        rejections,
                        pending,
                        "max_positions",
                        desired_stake,
                        state,
                        current_exposure,
                        date,
                    )
                    pending_entries.pop(pending.pair, None)

        if candidates:
            total_desired = sum(item[1] for item in candidates)
            allocated_stake = total_stake(state.positions)
            allocation_capital = state.cash + allocated_stake
            total_capacity = stake_capacity(
                allocation_capital,
                allocated_stake,
                config.max_total_exposure,
                config.fee,
            )
            available = min(
                state.cash / (1.0 + config.fee),
                total_capacity,
            )
            factor = min(1.0, available / total_desired) if total_desired > 0 else 0.0

            for pending, desired_stake, details, row in candidates:
                stake = desired_stake * factor
                if stake < config.min_stake:
                    record_rejection(
                        rejections,
                        pending,
                        "below_min_stake",
                        stake,
                        state,
                        market_value(state.positions, rows_by_pair, last_close, date, "open"),
                        date,
                    )
                    pending_entries.pop(pending.pair, None)
                    continue
                entry_price = details["entry_price"]
                entry_fee = stake * config.fee
                quantity = stake / entry_price
                stop_price = entry_price * (1.0 - details["stop_distance"])
                take_profit_price = entry_price * (1.0 + details["take_profit_distance"])
                post_entry_capital = state.cash + total_stake(state.positions) - entry_fee
                post_entry_stake = total_stake(state.positions) + stake
                post_entry_pair_stake = pair_stake(pending.pair, state.positions) + stake
                entry_stake_exposure = (
                    post_entry_stake / post_entry_capital if post_entry_capital > 0 else 0.0
                )
                entry_pair_stake_exposure = (
                    post_entry_pair_stake / post_entry_capital if post_entry_capital > 0 else 0.0
                )
                position_slot = position_key(pending.edge, pending.pair, pending.timeframe)
                unique_position_id = f"{position_slot}|{next_position_id:06d}"
                next_position_id += 1
                sequence = next_allocation_sequence
                next_allocation_sequence += 1
                state.cash -= stake + entry_fee
                if state.cash < -0.000001:
                    raise RuntimeError("cash became negative after entry")
                state.fees_paid += entry_fee
                state.positions[position_slot] = Position(
                    position_id=unique_position_id,
                    edge=pending.edge,
                    edge_version=pending.edge_version,
                    pair=pending.pair,
                    timeframe=pending.timeframe,
                    signal_date=pending.signal_date,
                    entry_date=date,
                    entry_index=int(row.bar_index),
                    quantity=quantity,
                    stake=stake,
                    entry_price=entry_price,
                    entry_fee=entry_fee,
                    stop_price=stop_price,
                    take_profit_price=take_profit_price,
                    stop_distance=details["stop_distance"],
                    take_profit_distance=details["take_profit_distance"],
                    max_hold=pending.max_hold,
                    entry_stake_exposure=entry_stake_exposure,
                    entry_pair_stake_exposure=entry_pair_stake_exposure,
                    allocation_sequence=sequence,
                    pair_priority=pending.pair_priority,
                    execution_profile=config.execution_profile,
                    execution_model=config.execution_model,
                    allocation_policy=config.allocation_policy,
                    cost_model_version=config.cost_model_version,
                )
                entries_opened += 1
                max_concurrent_positions = max(max_concurrent_positions, len(state.positions))
                pending_entries.pop(pending.pair, None)

        for position_id, position in list(state.positions.items()):
            row = current_rows.get(position.pair)
            if row is None:
                continue
            stop_hit = float(row.low) <= position.stop_price
            tp_hit = float(row.high) >= position.take_profit_price
            if stop_hit and (config.intrabar_policy == "stop_first" or not tp_hit):
                base_stop_fill = min(position.stop_price, float(row.open))
                exit_price = base_stop_fill * (1.0 - config.stop_slippage)
                close_position_with_config(
                    state, position_id, date, exit_price, "stop", trades, run_id, config
                )
                pending_exits.pop(position_id, None)
                continue
            if tp_hit:
                exit_price = position.take_profit_price * (1.0 - config.exit_slippage)
                close_position_with_config(
                    state, position_id, date, exit_price, "take_profit", trades, run_id, config
                )
                pending_exits.pop(position_id, None)
                continue

        for position_id, position in list(state.positions.items()):
            if position.pair not in current_rows:
                continue
            position.bars_held += 1
            if position.bars_held >= position.max_hold and position_id not in pending_exits:
                next_date = next_bar_date(features, position.pair, date)
                if next_date is not None and in_simulation_window(next_date, start, end):
                    pending_exits[position_id] = (next_date, "max_hold")

        current_signals = signals_by_date.get(date)
        if current_signals is not None:
            signal_rows = list(current_signals.itertuples(index=False))
            exit_pairs = {signal.pair for signal in signal_rows if signal.signal_type == "exit"}
            signal_rows = sorted(
                signal_rows,
                key=lambda signal: (0 if signal.signal_type == "exit" else 1, signal.pair),
            )
            for signal in signal_rows:
                signal_type = signal.signal_type
                pair = signal.pair
                key = position_key(edge, pair, timeframe)
                if signal_type == "exit":
                    if key in state.positions and key not in pending_exits:
                        execute_date = next_bar_date(features, pair, date)
                        if execute_date is not None and in_simulation_window(
                            execute_date, start, end
                        ):
                            pending_exits[key] = (execute_date, "signal")
                    continue
                if signal_type != "entry":
                    continue
                pending_stub = PendingEntry(
                    edge=edge,
                    edge_version=getattr(signal, "edge_version", edge_version),
                    pair=pair,
                    timeframe=timeframe,
                    signal_date=date,
                    execute_date=date,
                    signal_index=int(getattr(signal, "signal_index", -1)),
                    risk=float(getattr(signal, "composite_vol_at_signal", np.nan)),
                    max_hold=int(signal.max_hold),
                    pair_priority=priorities.get(pair, 999999),
                    execution_profile=config.execution_profile,
                    execution_model=config.execution_model,
                    allocation_policy=config.allocation_policy,
                    cost_model_version=config.cost_model_version,
                )
                current_exposure = market_value(
                    state.positions, rows_by_pair, last_close, date, "close"
                )
                if pair in exit_pairs:
                    record_rejection(
                        rejections,
                        pending_stub,
                        "exit_priority",
                        0.0,
                        state,
                        current_exposure,
                        date,
                    )
                    continue
                if key in state.positions or pair in pending_entries:
                    record_rejection(
                        rejections, pending_stub, "already_open", 0.0, state, current_exposure, date
                    )
                    continue
                execute_date = next_bar_date(features, pair, date)
                if execute_date is None or not in_simulation_window(execute_date, start, end):
                    record_rejection(
                        rejections, pending_stub, "no_next_bar", 0.0, state, current_exposure, date
                    )
                    continue
                risk = float(getattr(signal, "composite_vol_at_signal", np.nan))
                if not np.isfinite(risk) or risk <= 0:
                    record_rejection(
                        rejections,
                        pending_stub,
                        "invalid_risk",
                        0.0,
                        state,
                        current_exposure,
                        execute_date,
                    )
                    continue
                pending_entries[pair] = PendingEntry(
                    edge=edge,
                    edge_version=getattr(signal, "edge_version", edge_version),
                    pair=pair,
                    timeframe=timeframe,
                    signal_date=date,
                    execute_date=execute_date,
                    signal_index=int(getattr(signal, "signal_index", -1)),
                    risk=risk,
                    max_hold=int(signal.max_hold),
                    pair_priority=priorities.get(pair, 999999),
                    execution_profile=config.execution_profile,
                    execution_model=config.execution_model,
                    allocation_policy=config.allocation_policy,
                    cost_model_version=config.cost_model_version,
                )

        for pair, row in current_rows.items():
            last_close[pair] = float(row.close)

        mv = market_value(state.positions, rows_by_pair, last_close, date, "close")
        equity = state.cash + mv
        unrealized_pnl = sum(
            position.quantity * last_close.get(position.pair, position.entry_price)
            - position.stake
            - position.entry_fee
            for position in state.positions.values()
        )
        exposure = mv / equity if equity > 0 else 0.0
        pair_values = pair_market_values(state.positions, rows_by_pair, last_close, date, "close")
        pair_exposures = {
            pair: (value / equity if equity > 0 else 0.0) for pair, value in pair_values.items()
        }
        max_pair_exposure = max(pair_exposures.values(), default=0.0)
        allocated_stake = total_stake(state.positions)
        allocated_capital = state.cash + allocated_stake
        stake_exposure = allocated_stake / allocated_capital if allocated_capital > 0 else 0.0
        pair_stakes = pair_stake_values(state.positions)
        pair_stake_exposures = {
            pair: (value / allocated_capital if allocated_capital > 0 else 0.0)
            for pair, value in pair_stakes.items()
        }
        max_pair_stake_exposure = max(pair_stake_exposures.values(), default=0.0)
        max_entry_stake_exposure = max(
            (position.entry_stake_exposure for position in state.positions.values()), default=0.0
        )
        max_entry_pair_stake_exposure = max(
            (position.entry_pair_stake_exposure for position in state.positions.values()),
            default=0.0,
        )
        equity_rows.append(
            {
                "run_id": run_id,
                "edge": edge,
                "timeframe": timeframe,
                "date": date,
                "cash": state.cash,
                "market_value": mv,
                "equity": equity,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "fees_paid": state.fees_paid,
                "exposure": exposure,
                "max_pair_exposure": max_pair_exposure,
                "pair_exposures": json.dumps(pair_exposures, sort_keys=True),
                "allocated_stake": allocated_stake,
                "stake_exposure": stake_exposure,
                "max_pair_stake_exposure": max_pair_stake_exposure,
                "pair_stake_exposures": json.dumps(pair_stake_exposures, sort_keys=True),
                "max_entry_stake_exposure": max_entry_stake_exposure,
                "max_entry_pair_stake_exposure": max_entry_pair_stake_exposure,
                "open_positions": len(state.positions),
                "execution_profile": config.execution_profile,
                "execution_model": config.execution_model,
                "allocation_policy": config.allocation_policy,
                "cost_model_version": config.cost_model_version,
            }
        )

    equity_df = pd.DataFrame(equity_rows)
    if not equity_df.empty:
        peak = equity_df["equity"].cummax()
        equity_df["drawdown"] = equity_df["equity"] / peak - 1.0
    else:
        equity_df = pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "edge": edge,
                    "timeframe": timeframe,
                    "date": pd.NaT,
                    "cash": state.cash,
                    "market_value": 0.0,
                    "equity": state.cash,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "fees_paid": 0.0,
                    "exposure": 0.0,
                    "max_pair_exposure": 0.0,
                    "pair_exposures": "{}",
                    "allocated_stake": 0.0,
                    "stake_exposure": 0.0,
                    "max_pair_stake_exposure": 0.0,
                    "pair_stake_exposures": "{}",
                    "max_entry_stake_exposure": 0.0,
                    "max_entry_pair_stake_exposure": 0.0,
                    "open_positions": 0,
                    "drawdown": 0.0,
                    "execution_profile": config.execution_profile,
                    "execution_model": config.execution_model,
                    "allocation_policy": config.allocation_policy,
                    "cost_model_version": config.cost_model_version,
                }
            ]
        )

    trades_df = ensure_columns(pd.DataFrame(trades), TRADE_COLUMNS)
    rejections_df = ensure_columns(pd.DataFrame(rejections), REJECTION_COLUMNS)
    if not rejections_df.empty:
        rejections_df["run_id"] = run_id
    closed = trades_df
    final_equity = float(equity_df["equity"].iloc[-1])
    total_return = final_equity / config.initial_cash - 1.0
    max_dd, dd_duration = drawdown_stats(equity_df["equity"])
    wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
    summary = {
        "run_id": run_id,
        "edge": edge,
        "edge_version": edge_version,
        "timeframe": timeframe,
        "initial_cash": config.initial_cash,
        "final_equity": final_equity,
        "total_return": total_return,
        "trades": len(closed),
        "win_rate": float(wins / len(closed)) if len(closed) else 0.0,
        "profit_factor": profit_factor_from_pnl(closed),
        "max_drawdown": max_dd,
        "drawdown_duration": dd_duration,
        "fees_paid": state.fees_paid,
        "max_exposure": float(equity_df["exposure"].max()) if not equity_df.empty else 0.0,
        "max_pair_exposure": (
            float(equity_df["max_pair_exposure"].max()) if not equity_df.empty else 0.0
        ),
        "max_stake_exposure": (
            float(equity_df["stake_exposure"].max()) if not equity_df.empty else 0.0
        ),
        "max_pair_stake_exposure": (
            float(equity_df["max_pair_stake_exposure"].max()) if not equity_df.empty else 0.0
        ),
        "max_entry_stake_exposure": (
            float(equity_df["max_entry_stake_exposure"].max()) if not equity_df.empty else 0.0
        ),
        "max_entry_pair_stake_exposure": (
            float(equity_df["max_entry_pair_stake_exposure"].max()) if not equity_df.empty else 0.0
        ),
        "average_exposure": float(equity_df["exposure"].mean()) if not equity_df.empty else 0.0,
        "max_concurrent_positions": int(
            max(max_concurrent_positions, equity_df["open_positions"].max())
        ),
        "signals_seen": len(entry_signals),
        "entries_opened": int(entries_opened),
        "signals_rejected": len(rejections_df),
        "open_positions_end": len(state.positions),
        "account_simulator_version": ACCOUNT_SIMULATOR_VERSION,
        "execution_profile": config.execution_profile,
        "execution_model": config.execution_model,
        "allocation_policy": config.allocation_policy,
        "cost_model_version": config.cost_model_version,
    }
    return SimulationResult(
        summary=summary,
        trades=trades_df,
        equity=equity_df,
        rejections=rejections_df,
    )


def empty_outputs(
    run_id: str, edge: str, timeframe: str, config: AccountConfig
) -> SimulationResult:
    return simulate_account(pd.DataFrame(), {}, config, run_id, edge, timeframe)


def load_features(
    storage: Path, exchange: str, pairs: list[str], timeframe: str
) -> dict[str, pd.DataFrame]:
    out = {}
    for pair in pairs:
        path = feature_path(storage, exchange, pair, timeframe)
        if path.exists():
            out[pair] = pd.read_parquet(path)
    return out


def run_account_simulator(
    storage: Path,
    exchange: str,
    pairs: list[str],
    timeframes: list[str],
    config: AccountConfig,
    run_id: str,
    profile: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    active_profile = validate_cli_profile(config, profile or config.execution_profile)
    signals_path = storage / "results" / "edge_signals.parquet"
    if not signals_path.exists():
        raise FileNotFoundError(f"Missing {signals_path}. Run edge_engine first.")
    signals = pd.read_parquet(signals_path)
    if run_id and "run_id" in signals.columns:
        signals = signals[signals["run_id"] == run_id]
    signals = normalize_signals(signals)

    all_trades = []
    all_equity = []
    all_summary = []
    all_rejections = []

    for timeframe in timeframes:
        tf_signals = (
            signals[signals["timeframe"] == timeframe].copy()
            if not signals.empty
            else pd.DataFrame()
        )
        edges = (
            sorted(tf_signals["edge"].dropna().unique().tolist()) if not tf_signals.empty else []
        )
        features = load_features(storage, exchange, pairs, timeframe)
        for edge in edges:
            edge_signals = tf_signals[tf_signals["edge"] == edge].copy()
            result = simulate_account(edge_signals, features, config, run_id, edge, timeframe)
            all_trades.append(result.trades)
            all_equity.append(result.equity)
            all_summary.append(pd.DataFrame([result.summary]))
            all_rejections.append(result.rejections)

    trades_df = ensure_columns(
        pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(),
        TRADE_COLUMNS,
    )
    equity_df = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    summary_df = pd.concat(all_summary, ignore_index=True) if all_summary else pd.DataFrame()
    rejections_df = ensure_columns(
        pd.concat(all_rejections, ignore_index=True) if all_rejections else pd.DataFrame(),
        REJECTION_COLUMNS,
    )

    out_dir = profile_results_dir(storage, active_profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "account_trades.parquet": trades_df,
        "account_equity.parquet": equity_df,
        "account_summary.parquet": summary_df,
        "account_rejections.parquet": rejections_df,
    }
    for name, frame in outputs.items():
        write_parquet_atomic(frame, out_dir / name)
    if active_profile == LEGACY_PROFILE:
        legacy_dir = storage / "results"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        for name in outputs:
            shutil.copy2(out_dir / name, legacy_dir / name)
    refresh_duckdb(storage)
    return trades_df, equity_df, summary_df, rejections_df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate account-level execution from edge signals"
    )
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--exchange", default="kucoin")
    parser.add_argument("--pairs", default="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT")
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--rules", default="research_lab/config/account_rules.json", type=Path)
    parser.add_argument("--profile", default=None, choices=["research", "freqtrade"])
    parser.add_argument(
        "--pair-universe",
        default="research_lab/config/pair_universe.json",
        type=Path,
    )
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID"))
    args = parser.parse_args()

    config, _, _ = load_account_config(args.rules)
    _, universe_pairs = load_pair_universe(args.pair_universe)
    if universe_pairs:
        config = with_pair_universe(config, universe_pairs)
    profile = validate_cli_profile(config, args.profile or config.execution_profile)
    run_id = args.run_id or "manual"
    _, _, summary, _ = run_account_simulator(
        args.storage,
        args.exchange,
        parse_csv(args.pairs),
        parse_csv(args.timeframes),
        config,
        run_id,
        profile,
    )
    if summary.empty:
        print("No account simulation results.")
        return
    out = summary[
        ["edge", "timeframe", "trades", "total_return", "profit_factor", "max_drawdown"]
    ].copy()
    out["total_return"] = out["total_return"].map(lambda x: f"{x:.2%}")
    out["max_drawdown"] = out["max_drawdown"].map(lambda x: f"{x:.2%}")
    print(out.sort_values(["edge", "timeframe"]).to_string(index=False))


if __name__ == "__main__":
    main()
