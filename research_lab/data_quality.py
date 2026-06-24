from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research_lab.run_manager import atomic_write_json


TF_TO_FREQ = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def parse_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def expected_last_closed_open(
    timeframe: str, now: pd.Timestamp | None = None
) -> pd.Timestamp | None:
    freq = TF_TO_FREQ.get(timeframe)
    if not freq:
        return None
    now = now or pd.Timestamp.now(tz="UTC")
    floored = now.floor(freq)
    return floored - pd.Timedelta(freq)


def timeframe_delta(timeframe: str) -> pd.Timedelta | None:
    freq = TF_TO_FREQ.get(timeframe)
    return pd.Timedelta(freq) if freq else None


def expected_source_bars(source_timeframe: str, target_timeframe: str) -> int | None:
    source_delta = timeframe_delta(source_timeframe)
    target_delta = timeframe_delta(target_timeframe)
    if source_delta is None or target_delta is None or target_delta <= source_delta:
        return None
    ratio = target_delta / source_delta
    if ratio != int(ratio):
        return None
    return int(ratio)


def validate_ohlcv_file(
    path: Path, pair: str, timeframe: str, min_rows: int, max_lag_bars: int
) -> dict:
    df = pd.read_parquet(path)
    errors: list[str] = []
    warnings: list[str] = []
    freq_delta = timeframe_delta(timeframe)
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        errors.append("missing_required_columns")
        return {
            "pair": pair,
            "timeframe": timeframe,
            "path": str(path),
            "rows": int(len(df)),
            "status": "failed",
            "errors": errors,
            "warnings": warnings,
            "missing_columns": missing_columns,
        }

    if df.empty:
        errors.append("empty_file")
        return {
            "pair": pair,
            "timeframe": timeframe,
            "path": str(path),
            "rows": 0,
            "status": "failed",
            "errors": errors,
            "warnings": warnings,
        }

    raw_date = pd.to_datetime(df["date"], utc=True, errors="coerce")
    invalid_dates = int(raw_date.isna().sum())
    if invalid_dates:
        errors.append("invalid_dates")

    if not raw_date.dropna().is_monotonic_increasing:
        errors.append("dates_not_sorted")

    numeric = df[OHLCV_COLUMNS].apply(pd.to_numeric, errors="coerce")
    nonfinite_ohlcv = int((~np.isfinite(numeric.to_numpy(dtype=float))).any(axis=1).sum())
    if nonfinite_ohlcv:
        errors.append("nonfinite_ohlcv")

    work = df.copy()
    work["_date"] = raw_date
    work[OHLCV_COLUMNS] = numeric
    work = work.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)
    date = work["_date"]
    duplicates = int(date.duplicated().sum())
    if duplicates:
        errors.append("duplicate_dates")

    now = pd.Timestamp.now(tz="UTC")
    future_rows = int((date > now + pd.Timedelta(minutes=1)).sum())
    if future_rows:
        errors.append("future_dates")

    incomplete_rows = 0
    last_complete = expected_last_closed_open(timeframe, now)
    if last_complete is not None:
        incomplete_rows = int((date > last_complete).sum())
        if incomplete_rows:
            errors.append("incomplete_candles_present")

    invalid_ohlc = int(
        (
            (work["high"] < work["open"])
            | (work["high"] < work["close"])
            | (work["low"] > work["open"])
            | (work["low"] > work["close"])
            | (work["high"] < work["low"])
        ).sum()
    )
    if invalid_ohlc:
        errors.append("invalid_ohlc")

    nonpositive_prices = int(((work[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum())
    if nonpositive_prices:
        errors.append("nonpositive_prices")

    negative_volume = int((work["volume"] < 0).sum())
    if negative_volume:
        errors.append("negative_volume")

    if len(work) < min_rows:
        errors.append("too_few_rows")

    missing_candles = 0
    max_gap = None
    if freq_delta:
        diffs = date.diff().dropna()
        gaps = diffs[diffs > freq_delta * 1.5]
        missing_candles = int(sum(max(int(round(gap / freq_delta)) - 1, 0) for gap in gaps))
        max_gap = str(gaps.max()) if not gaps.empty else None
        if missing_candles:
            errors.append("missing_candles")

    actual_last = date.max()
    stale_hours = float((now - actual_last) / pd.Timedelta(hours=1))
    lag_bars = None
    if last_complete is not None and freq_delta is not None:
        lag_delta = last_complete - actual_last
        lag_bars = max(int(np.floor(lag_delta / freq_delta)), 0)
        if lag_bars > max_lag_bars:
            errors.append("stale_data")

    return {
        "pair": pair,
        "timeframe": timeframe,
        "path": str(path),
        "rows": int(len(work)),
        "start": date.min().isoformat(),
        "end": actual_last.isoformat(),
        "expected_last_complete_candle": last_complete.isoformat()
        if last_complete is not None
        else None,
        "actual_last_available_candle": actual_last.isoformat(),
        "last_complete_candle": actual_last.isoformat(),
        "lag_bars": lag_bars,
        "max_lag_bars": max_lag_bars,
        "invalid_dates": invalid_dates,
        "duplicates": duplicates,
        "future_rows": future_rows,
        "incomplete_rows": incomplete_rows,
        "possible_incomplete_rows": incomplete_rows,
        "nonfinite_ohlcv": nonfinite_ohlcv,
        "invalid_ohlc": invalid_ohlc,
        "nonpositive_prices": nonpositive_prices,
        "negative_volume": negative_volume,
        "missing_candles": missing_candles,
        "max_gap": max_gap,
        "stale_hours": stale_hours,
        "status": "failed" if errors else "passed",
        "errors": errors,
        "warnings": warnings,
    }


def validate_resample(
    storage: Path, exchange: str, source_timeframe: str, target_timeframe: str
) -> list[dict]:
    manifest_path = storage / "ohlcv_manifest.parquet"
    if not manifest_path.exists():
        return []
    manifest = pd.read_parquet(manifest_path)
    checks = []
    source_rows = manifest[manifest["timeframe"] == source_timeframe]
    required_source_bars = expected_source_bars(source_timeframe, target_timeframe)
    for source in source_rows.itertuples(index=False):
        target = manifest[
            (manifest["pair"] == source.pair) & (manifest["timeframe"] == target_timeframe)
        ]
        if target.empty:
            checks.append(
                {"pair": source.pair, "status": "failed", "errors": ["missing_target_resample"]}
            )
            continue
        src = pd.read_parquet(source.path).sort_values("date")
        tgt = pd.read_parquet(target.iloc[0]["path"]).sort_values("date")
        src["date"] = pd.to_datetime(src["date"], utc=True)
        tgt["date"] = pd.to_datetime(tgt["date"], utc=True)
        expected = (
            src.set_index("date")
            .resample(target_timeframe, label="left", closed="left")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
                source_bars=("close", "count"),
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )
        if required_source_bars is not None:
            expected = expected[expected["source_bars"] == required_source_bars].copy()
        expected = expected.drop(columns=["source_bars"])
        errors = []
        tgt = tgt[["date", "open", "high", "low", "close", "volume"]]
        if len(expected) != len(tgt):
            errors.append("row_count_mismatch")
        if expected.empty or tgt.empty:
            errors.append("empty_resample_side")
        merged = expected.merge(
            tgt,
            on="date",
            how="outer",
            suffixes=("_expected", "_actual"),
            indicator=True,
        )
        if not merged.empty and not (merged["_merge"] == "both").all():
            errors.append("date_coverage_mismatch")
        both = merged[merged["_merge"] == "both"]
        if both.empty:
            errors.append("no_common_dates")
        else:
            for col in ["open", "high", "low", "close", "volume"]:
                if not np.allclose(
                    both[f"{col}_expected"], both[f"{col}_actual"], rtol=1e-9, atol=1e-9
                ):
                    errors.append(f"{col}_mismatch")
        checks.append(
            {
                "pair": source.pair,
                "source_timeframe": source_timeframe,
                "target_timeframe": target_timeframe,
                "expected_rows": int(len(expected)),
                "actual_rows": int(len(tgt)),
                "rows_checked": int(len(both)),
                "status": "failed" if errors else "passed",
                "errors": errors,
            }
        )
    return checks


def run_quality(
    storage: Path,
    exchange: str,
    timeframes: list[str] | None,
    run_id: str | None,
    min_rows: int,
    max_lag_bars: int,
    source_timeframe: str,
    target_timeframe: str,
) -> dict:
    manifest_path = storage / "ohlcv_manifest.parquet"
    if not manifest_path.exists():
        report = {"status": "failed", "errors": ["missing_ohlcv_manifest"], "run_id": run_id}
        atomic_write_json(storage / "results" / "data_quality_report.json", report)
        return report

    manifest = pd.read_parquet(manifest_path)
    if timeframes:
        manifest = manifest[manifest["timeframe"].isin(timeframes)]

    file_reports = [
        validate_ohlcv_file(Path(row.path), row.pair, row.timeframe, min_rows, max_lag_bars)
        for row in manifest.itertuples(index=False)
    ]
    resample_checks = validate_resample(storage, exchange, source_timeframe, target_timeframe)
    failed_files = [item for item in file_reports if item["status"] != "passed"]
    failed_resample = [item for item in resample_checks if item["status"] != "passed"]
    warnings = sum(len(item["warnings"]) for item in file_reports)
    missing_candles = sum(item.get("missing_candles", 0) for item in file_reports)
    duplicates = sum(item.get("duplicates", 0) for item in file_reports)
    invalid_ohlc = sum(item.get("invalid_ohlc", 0) for item in file_reports)
    nonfinite_ohlcv = sum(item.get("nonfinite_ohlcv", 0) for item in file_reports)
    incomplete_rows = sum(item.get("incomplete_rows", 0) for item in file_reports)
    stale_pairs = [
        f"{item['pair']}:{item['timeframe']}"
        for item in file_reports
        if "stale_data" in item.get("errors", [])
    ]
    incomplete_pairs = [
        f"{item['pair']}:{item['timeframe']}"
        for item in file_reports
        if "incomplete_candles_present" in item.get("errors", [])
    ]
    expected_last_values = [
        item.get("expected_last_complete_candle")
        for item in file_reports
        if item.get("expected_last_complete_candle")
    ]
    actual_last_values = [
        item.get("actual_last_available_candle")
        for item in file_reports
        if item.get("actual_last_available_candle")
    ]
    lag_values = [item.get("lag_bars") for item in file_reports if item.get("lag_bars") is not None]
    actual_last_available_candle = min(actual_last_values) if actual_last_values else None
    report = {
        "run_id": run_id,
        "status": "failed" if failed_files or failed_resample else "passed",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "pairs_checked": int(manifest["pair"].nunique()) if not manifest.empty else 0,
        "files_checked": int(len(file_reports)),
        "missing_candles": int(missing_candles),
        "duplicates": int(duplicates),
        "invalid_ohlc": int(invalid_ohlc),
        "nonfinite_ohlcv": int(nonfinite_ohlcv),
        "incomplete_rows": int(incomplete_rows),
        "warnings": int(warnings),
        "stale_pairs": stale_pairs,
        "incomplete_pairs": incomplete_pairs,
        "expected_last_complete_candle": min(expected_last_values)
        if expected_last_values
        else None,
        "actual_last_available_candle": actual_last_available_candle,
        "last_complete_candle": actual_last_available_candle,
        "max_lag_bars": max(lag_values) if lag_values else None,
        "allowed_lag_bars": max_lag_bars,
        "failed_files": failed_files,
        "failed_resample": failed_resample,
        "file_reports": file_reports,
        "resample_checks": resample_checks,
    }
    atomic_write_json(storage / "results" / "data_quality_report.json", report)
    if run_id:
        atomic_write_json(
            storage / "results" / "runs" / run_id / "data_quality_report.json", report
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate OHLCV warehouse quality")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--exchange", default="kucoin")
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID"))
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--max-lag-bars", type=int, default=1)
    parser.add_argument(
        "--max-stale-hours", type=float, default=None, help="Deprecated; use --max-lag-bars."
    )
    parser.add_argument("--source-timeframe", default="1h")
    parser.add_argument("--target-timeframe", default="4h")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    report = run_quality(
        args.storage,
        args.exchange,
        parse_csv(args.timeframes),
        args.run_id,
        args.min_rows,
        args.max_lag_bars,
        args.source_timeframe,
        args.target_timeframe,
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"file_reports", "resample_checks"}},
            indent=2,
        )
    )
    if args.fail_on_error and report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
