from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(
    close: pd.Series, period: int = 20, stds: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(period, min_periods=period).mean()
    width = close.rolling(period, min_periods=period).std(ddof=0) * stds
    return mid - width, mid, mid + width


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    def pct_rank(values: np.ndarray) -> float:
        if len(values) == 0 or np.isnan(values[-1]):
            return np.nan
        return float(np.mean(values <= values[-1]))

    return series.rolling(window, min_periods=max(20, window // 4)).apply(pct_rank, raw=True)


def parkinson_vol(df: pd.DataFrame, window: int = 20) -> pd.Series:
    high_low = np.log(df["high"] / df["low"].replace(0.0, np.nan))
    variance = (high_low**2).rolling(window, min_periods=window).mean() / (4.0 * np.log(2.0))
    return np.sqrt(variance)


def realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    returns = np.log(close / close.shift(1))
    return returns.rolling(window, min_periods=window).std(ddof=0)


def ewma_vol(close: pd.Series, span: int = 20) -> pd.Series:
    returns = np.log(close / close.shift(1))
    return returns.ewm(span=span, adjust=False, min_periods=span).std()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    session = df["date"].dt.floor("D")
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.groupby(session).cumsum()
    cum_vol = df["volume"].groupby(session).cumsum().replace(0.0, np.nan)
    return cum_pv / cum_vol


def add_core_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values("date").reset_index(drop=True)

    close = out["close"]
    out["ret_1"] = close.pct_change()
    for window in [3, 6, 12, 24, 48]:
        out[f"ret_{window}"] = close.pct_change(window)

    for period in [10, 20, 50, 100, 200]:
        out[f"ema_{period}"] = ema(close, period)
        out[f"ema_{period}_dist"] = close / out[f"ema_{period}"] - 1.0

    out["ema_20_slope"] = out["ema_20"].pct_change(5)
    out["ema_50_slope"] = out["ema_50"].pct_change(10)
    out["rsi_14"] = rsi(close, 14)
    out["atr_14"] = atr(out, 14)
    out["atr_pct_14"] = out["atr_14"] / close
    out["realized_vol_20"] = realized_vol(close, 20)
    out["parkinson_vol_20"] = parkinson_vol(out, 20)
    out["ewma_vol_20"] = ewma_vol(close, 20)
    out["composite_vol"] = out[
        ["atr_pct_14", "realized_vol_20", "parkinson_vol_20", "ewma_vol_20"]
    ].median(axis=1)

    bb_lower, bb_mid, bb_upper = bollinger(close, 20, 2.0)
    out["bb_lower_20"] = bb_lower
    out["bb_mid_20"] = bb_mid
    out["bb_upper_20"] = bb_upper
    out["bb_width_20"] = (bb_upper - bb_lower) / bb_mid
    out["bb_pos_20"] = (close - bb_lower) / (bb_upper - bb_lower)

    for high_n, low_n in [(20, 10), (55, 20)]:
        out[f"donchian_high_{high_n}"] = out["high"].rolling(high_n, min_periods=high_n).max()
        out[f"donchian_low_{low_n}"] = out["low"].rolling(low_n, min_periods=low_n).min()

    log_volume = np.log1p(out["volume"])
    volume_mean = log_volume.rolling(50, min_periods=30).mean()
    volume_std = log_volume.rolling(50, min_periods=30).std(ddof=0).replace(0.0, np.nan)
    out["volume_z_50"] = (log_volume - volume_mean) / volume_std
    out["volume_pct_100"] = rolling_percentile(log_volume, 100)
    out["session_vwap"] = session_vwap(out)
    out["session_vwap_dist"] = close / out["session_vwap"] - 1.0

    trend_raw = (out["ema_50_dist"] + out["ema_100_dist"] + out["ema_50_slope"].fillna(0.0)) / out[
        "composite_vol"
    ].replace(0.0, np.nan)
    trend_score = np.tanh(trend_raw.clip(-5, 5) / 2.5)
    range_score = (1.0 - trend_score.abs()).clip(0.0, 1.0)
    bull_raw = trend_score.clip(lower=0.0)
    bear_raw = (-trend_score).clip(lower=0.0)
    normalizer = bull_raw + bear_raw + range_score
    out["bull_prob"] = bull_raw / normalizer.replace(0.0, np.nan)
    out["bear_prob"] = bear_raw / normalizer.replace(0.0, np.nan)
    out["range_prob"] = range_score / normalizer.replace(0.0, np.nan)
    out["regime_score"] = trend_score
    out["vol_rank_200"] = rolling_percentile(out["composite_vol"], 200)

    return out
