#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=df.index)
        .ewm(alpha=1 / period, adjust=False, min_periods=period)
        .mean()
        / atr_
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=df.index)
        .ewm(alpha=1 / period, adjust=False, min_periods=period)
        .mean()
        / atr_
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(close: pd.Series, period: int = 20, stds: float = 2.0):
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return mid - stds * sd, mid, mid + stds * sd


def last_percentile(window: np.ndarray) -> float:
    last = window[-1]
    valid = window[~np.isnan(window)]
    if len(valid) == 0 or np.isnan(last):
        return np.nan
    return float(np.mean(valid <= last))


def softmax_frame(logits: pd.DataFrame) -> pd.DataFrame:
    logits = logits.clip(-20, 20)
    exp_logits = np.exp(logits.sub(logits.max(axis=1), axis=0))
    return exp_logits.div(exp_logits.sum(axis=1), axis=0).fillna(1.0 / 3.0)


def regime_features(df: pd.DataFrame, trend_ema: int = 100) -> pd.DataFrame:
    ema_trend = ema(df["close"], trend_ema)
    ema_slope = ema_trend.diff()
    adx_v = adx(df, 14)
    atr_pct = atr(df, 14) / df["close"]
    atr_rank = atr_pct.rolling(200, min_periods=50).apply(last_percentile, raw=True)
    distance = ((df["close"] / ema_trend) - 1.0).replace([np.inf, -np.inf], np.nan)
    slope_pct = (ema_slope / ema_trend).replace([np.inf, -np.inf], np.nan)
    trend_strength = (adx_v / 35.0).clip(0.0, 1.0)

    probs = softmax_frame(
        pd.concat(
            [
                (
                    distance * 25.0
                    + slope_pct * 900.0
                    + trend_strength * 0.80
                    - atr_rank.fillna(0.50) * 0.60
                ).rename("bull"),
                (
                    1.0
                    - trend_strength
                    - distance.abs().fillna(0.0) * 18.0
                    - atr_rank.fillna(0.50) * 0.20
                ).rename("range"),
                (
                    -distance * 25.0
                    - slope_pct * 900.0
                    + trend_strength * 0.80
                    + atr_rank.fillna(0.50) * 0.40
                ).rename("bear"),
            ],
            axis=1,
        )
    )
    out = pd.DataFrame(index=df.index)
    out["bull_prob"] = probs["bull"]
    out["range_prob"] = probs["range"]
    out["bear_prob"] = probs["bear"]
    out["regime_score"] = probs["bull"] + 0.5 * probs["range"] - probs["bear"]
    out["atr_rank"] = atr_rank
    return out


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".feather":
        df = pd.read_feather(path)
    else:
        df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("date", "time", "timestamp") if c in df.columns), None)
    if tcol:
        if pd.api.types.is_numeric_dtype(df[tcol]):
            unit = "ms" if df[tcol].iloc[0] > 1e11 else "s"
            df["date"] = pd.to_datetime(df[tcol], unit=unit, errors="coerce", utc=True)
        else:
            df["date"] = pd.to_datetime(df[tcol], errors="coerce", utc=True)
    required = ["date", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in {path}")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=required).sort_values("date").reset_index(drop=True)


def data_path(data_dir: Path, pair: str, timeframe: str) -> Path:
    return data_dir / f"{pair.replace('/', '_')}-{timeframe}.feather"


def align_features(
    base: pd.DataFrame, inf: pd.DataFrame, cols: list[str], suffix: str
) -> pd.DataFrame:
    tmp = inf[["date", *cols]].copy()
    tmp = tmp.rename(columns={col: f"{suffix}_{col}" for col in cols})
    base_pos = base[["date"]].copy()
    base_pos["_pos"] = np.arange(len(base_pos))
    merged = pd.merge_asof(
        base_pos.sort_values("date"), tmp.sort_values("date"), on="date", direction="backward"
    )
    return merged.sort_values("_pos").drop(columns=["_pos"]).reset_index(drop=True)


def build_features(
    pair: str,
    data_dir: Path,
    rsi_thr: float,
    wick_ratio: float,
    trend_ema: int,
    include_market: bool = True,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    df = load_ohlcv(data_path(data_dir, pair, "5m"))
    htf = load_ohlcv(data_path(data_dir, pair, "1h"))

    df["rsi"] = rsi(df["close"], 14)
    bb_low, bb_mid, bb_up = bollinger(df["close"], 20, 2.0)
    df["bb_lower"] = bb_low
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_up

    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["lower_wick_ratio"] = ((df[["open", "close"]].min(axis=1) - df["low"]) / rng).fillna(0.0)
    df["bull_candle"] = df["close"] > df["open"]
    df["band_penetration"] = (
        ((df["bb_lower"] - df["low"]) / df["close"]).clip(lower=0.0).fillna(0.0)
    )

    log_volume = np.log1p(df["volume"])
    volume_mean = log_volume.rolling(288, min_periods=50).mean()
    volume_std = log_volume.rolling(288, min_periods=50).std().replace(0, np.nan)
    df["volume_z"] = ((log_volume - volume_mean) / volume_std).fillna(0.0)
    df["volume_pct"] = (
        log_volume.rolling(288, min_periods=50).apply(last_percentile, raw=True).fillna(0.5)
    )

    rmf = df["close"].rolling(12).max()
    rms = df["close"].rolling(48).max()
    df["crash_block"] = (
        ((df["close"] - rmf) / rmf <= -0.06) | ((df["close"] - rms) / rms <= -0.08)
    ).fillna(False)

    reg = regime_features(htf, trend_ema)
    htf = pd.concat([htf, reg], axis=1)
    aligned = align_features(
        df, htf, ["bull_prob", "range_prob", "bear_prob", "regime_score", "atr_rank"], "asset"
    )
    df = pd.concat([df, aligned.drop(columns=["date"])], axis=1)

    if include_market:
        for market in ("BTC/USDT", "ETH/USDT"):
            mhtf = load_ohlcv(data_path(data_dir, market, "1h"))
            mreg = regime_features(mhtf, trend_ema)
            mhtf = pd.concat([mhtf, mreg], axis=1)
            suffix = market.split("/")[0].lower()
            aligned = align_features(df, mhtf, ["bear_prob", "regime_score"], suffix)
            df = pd.concat([df, aligned.drop(columns=["date"])], axis=1)

    cond = {}
    cond["valid"] = df[["rsi", "bb_lower", "asset_regime_score"]].notna().all(axis=1)
    cond["asset_regime"] = (df["asset_regime_score"] > 0.25) & (df["asset_bear_prob"] < 0.45)
    if include_market:
        cond["market_regime"] = (
            (df["btc_regime_score"] > 0.10)
            & (df["eth_regime_score"] > 0.10)
            & (df["btc_bear_prob"] < 0.50)
            & (df["eth_bear_prob"] < 0.50)
        )
    else:
        cond["market_regime"] = pd.Series(True, index=df.index)
    cond["rsi"] = df["rsi"] < rsi_thr
    cond["bb_touch"] = df["low"] <= df["bb_lower"]
    cond["bull_candle"] = df["bull_candle"]
    cond["wick"] = df["lower_wick_ratio"] >= wick_ratio
    cond["volume"] = (df["volume_pct"] > 0.35) & (df["volume_z"] > -0.50) & (df["volume"] > 0)
    cond["crash"] = ~df["crash_block"]
    return df, cond


def combine_conditions(cond: dict[str, pd.Series], exclude: set[str] | None = None) -> pd.Series:
    exclude = exclude or set()
    mask = None
    for name, value in cond.items():
        if name in exclude:
            continue
        mask = value.copy() if mask is None else mask & value
    return mask.fillna(False)


def forward_stats(
    df: pd.DataFrame,
    mask: pd.Series,
    horizon: int,
    target: float,
    mode: str,
    maker_offset: float,
    maker_timeout: int,
    cost: float,
    taker_slippage: float,
) -> dict:
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    idx = np.where(mask.to_numpy())[0]
    n_signals = len(idx)

    mfe, mae, ret = [], [], []
    filled = 0
    for i in idx:
        if i + 1 >= len(df):
            continue
        if mode == "maker":
            entry = close[i] * (1.0 - maker_offset)
            first = i + 1
            last = min(i + max(maker_timeout, 1), len(df) - 1)
            fill_window = low[first : last + 1]
            if len(fill_window) == 0 or np.nanmin(fill_window) > entry:
                continue
            fill_i = first + int(np.argmax(fill_window <= entry))
            net_cost = cost
        else:
            fill_i = i + 1
            entry = open_[fill_i] * (1.0 + taker_slippage)
            net_cost = cost + taker_slippage
        end = min(fill_i + horizon, len(df) - 1)
        if end <= fill_i or entry <= 0:
            continue
        filled += 1
        window_hi = high[fill_i + 1 : end + 1]
        window_lo = low[fill_i + 1 : end + 1]
        if len(window_hi) == 0:
            continue
        mfe.append((np.nanmax(window_hi) - entry) / entry)
        mae.append((np.nanmin(window_lo) - entry) / entry)
        ret.append((close[end] - entry) / entry - net_cost)

    mfe = np.asarray(mfe, dtype=float)
    mae = np.asarray(mae, dtype=float)
    ret = np.asarray(ret, dtype=float)
    if len(ret) == 0:
        return {
            "signals": n_signals,
            "fills": 0,
            "fill_rate": 0.0,
            "p_target": np.nan,
            "expectancy": np.nan,
            "mae_med": np.nan,
            "mfe_med": np.nan,
            "profit_factor": np.nan,
        }
    wins = ret[ret > 0]
    losses = ret[ret < 0]
    gross_loss = -losses.sum()
    return {
        "signals": n_signals,
        "fills": len(ret),
        "fill_rate": len(ret) / max(n_signals, 1),
        "p_target": float((mfe >= target).mean()),
        "expectancy": float(ret.mean()),
        "mae_med": float(np.nanmedian(mae)),
        "mfe_med": float(np.nanmedian(mfe)),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else np.inf,
    }


def print_stats(label: str, stats: dict):
    print(
        f"{label:22s} sig={stats['signals']:6d} fills={stats['fills']:6d} "
        f"fill={stats['fill_rate']:6.2%} pT={stats['p_target']:6.2%} "
        f"E={stats['expectancy']:+.4%} PF={stats['profit_factor']:.2f} "
        f"MAE_med={stats['mae_med']:+.3%} MFE_med={stats['mfe_med']:+.3%}"
    )


def run_pair(args, pair: str):
    df, cond = build_features(
        pair, args.data_dir, args.rsi, args.wick, args.trend_ema, not args.no_market
    )
    base = combine_conditions(cond)
    context = combine_conditions(cond, exclude={"rsi", "bb_touch", "bull_candle", "wick"})
    naive = combine_conditions(cond, exclude={"bull_candle", "wick"})

    print("=" * 120)
    print(
        f"PAIR {pair} | target={args.target:.2%} horizon={args.horizon} | maker_offset={args.maker_offset:.2%}"
    )
    print("=" * 120)
    for label, mask in [
        ("baseline_context", context),
        ("naive_no_pinbar", naive),
        ("pinbar_base", base),
    ]:
        for mode in ["maker", "taker"]:
            stats = forward_stats(
                df,
                mask,
                args.horizon,
                args.target,
                mode,
                args.maker_offset,
                args.maker_timeout,
                args.cost,
                args.taker_slippage,
            )
            print_stats(f"{label}_{mode}", stats)

    print("-" * 120)
    print("ABLATION vs pinbar_base maker")
    base_stats = forward_stats(
        df,
        base,
        args.horizon,
        args.target,
        "maker",
        args.maker_offset,
        args.maker_timeout,
        args.cost,
        args.taker_slippage,
    )
    print_stats("base", base_stats)
    for feature in [
        "rsi",
        "bb_touch",
        "bull_candle",
        "wick",
        "volume",
        "asset_regime",
        "market_regime",
        "crash",
    ]:
        mask = combine_conditions(cond, exclude={feature})
        stats = forward_stats(
            df,
            mask,
            args.horizon,
            args.target,
            "maker",
            args.maker_offset,
            args.maker_timeout,
            args.cost,
            args.taker_slippage,
        )
        delta = stats["expectancy"] - base_stats["expectancy"]
        print_stats(f"without_{feature}", stats)
        print(f"  delta_expectancy_vs_base={delta:+.4%}")

    if args.stability:
        print("-" * 120)
        print("STABILITY E[net] maker by RSI/WICK")
        rows = []
        for rsi_thr in range(args.rsi_min, args.rsi_max + 1, args.rsi_step):
            for wick in np.round(np.arange(args.wick_min, args.wick_max + 1e-9, args.wick_step), 2):
                _, cond2 = build_features(
                    pair, args.data_dir, rsi_thr, float(wick), args.trend_ema, not args.no_market
                )
                mask2 = combine_conditions(cond2)
                stats = forward_stats(
                    df,
                    mask2,
                    args.horizon,
                    args.target,
                    "maker",
                    args.maker_offset,
                    args.maker_timeout,
                    args.cost,
                    args.taker_slippage,
                )
                rows.append(
                    {
                        "rsi": rsi_thr,
                        "wick": float(wick),
                        "expectancy": stats["expectancy"],
                        "fills": stats["fills"],
                    }
                )
        sdf = pd.DataFrame(rows)
        print(
            sdf.pivot(index="rsi", columns="wick", values="expectancy").to_string(
                float_format=lambda x: f"{x:+.3%}"
            )
        )
        print("Fills:")
        print(sdf.pivot(index="rsi", columns="wick", values="fills").to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("user_data/data/kucoin"))
    ap.add_argument("--pairs", nargs="+", default=["SOL/USDT", "XRP/USDT", "DOGE/USDT"])
    ap.add_argument("--target", type=float, default=0.015)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--rsi", type=float, default=32.0)
    ap.add_argument("--wick", type=float, default=0.60)
    ap.add_argument("--trend-ema", type=int, default=100)
    ap.add_argument("--cost", type=float, default=0.002)
    ap.add_argument("--maker-offset", type=float, default=0.001)
    ap.add_argument("--maker-timeout", type=int, default=1)
    ap.add_argument("--taker-slippage", type=float, default=0.0007)
    ap.add_argument("--no-market", action="store_true")
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--rsi-min", type=int, default=26)
    ap.add_argument("--rsi-max", type=int, default=38)
    ap.add_argument("--rsi-step", type=int, default=2)
    ap.add_argument("--wick-min", type=float, default=0.55)
    ap.add_argument("--wick-max", type=float, default=0.75)
    ap.add_argument("--wick-step", type=float, default=0.05)
    args = ap.parse_args()

    for pair in args.pairs:
        run_pair(args, pair)
    return 0


if __name__ == "__main__":
    sys.exit(main())
