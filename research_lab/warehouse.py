from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import duckdb
import pandas as pd

from research_lab.indicators import add_core_features


PAIR_RE = re.compile(
    r"(?P<base>[A-Z0-9]+)_(?P<quote>[A-Z0-9]+)-(?P<timeframe>[0-9]+[mhd])\.feather$"
)


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write Parquet through a temp file so readers never see partial results."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    df.to_parquet(tmp_path, index=False)
    with tmp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def pair_to_file(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def file_to_pair(token: str) -> str:
    parts = token.split("_")
    if len(parts) < 2:
        return token
    return f"{parts[0]}/{parts[1]}"


def discover_feather_files(source: Path) -> list[tuple[Path, str, str]]:
    files: list[tuple[Path, str, str]] = []
    for path in sorted(source.glob("*.feather")):
        match = PAIR_RE.match(path.name)
        if not match:
            continue
        pair = f"{match.group('base')}/{match.group('quote')}"
        files.append((path, pair, match.group("timeframe")))
    return files


def migrate_ohlcv(source: Path, storage: Path, exchange: str) -> pd.DataFrame:
    rows = []
    out_root = storage / "ohlcv" / exchange
    out_root.mkdir(parents=True, exist_ok=True)

    for path, pair, timeframe in discover_feather_files(source):
        df = pd.read_feather(path)
        df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        df["exchange"] = exchange
        df["pair"] = pair
        df["timeframe"] = timeframe
        out_dir = out_root / pair_to_file(pair)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{timeframe}.parquet"
        write_parquet_atomic(df, out_path)
        rows.append(
            {
                "exchange": exchange,
                "pair": pair,
                "timeframe": timeframe,
                "rows": len(df),
                "start": df["date"].min(),
                "end": df["date"].max(),
                "path": str(out_path),
            }
        )

    manifest = pd.DataFrame(rows)
    if not manifest.empty:
        write_parquet_atomic(manifest, storage / "ohlcv_manifest.parquet")
    return manifest


def feature_path(storage: Path, exchange: str, pair: str, timeframe: str) -> Path:
    return storage / "features" / exchange / pair_to_file(pair) / f"{timeframe}.parquet"


def ohlcv_path(storage: Path, exchange: str, pair: str, timeframe: str) -> Path:
    return storage / "ohlcv" / exchange / pair_to_file(pair) / f"{timeframe}.parquet"


def expected_source_bars(source_timeframe: str, target_timeframe: str) -> int | None:
    source_delta = pd.Timedelta(source_timeframe)
    target_delta = pd.Timedelta(target_timeframe)
    if source_delta <= pd.Timedelta(0) or target_delta <= source_delta:
        return None
    ratio = target_delta / source_delta
    if ratio != int(ratio):
        return None
    return int(ratio)


def update_manifest(storage: Path, new_rows: pd.DataFrame, manifest_name: str) -> pd.DataFrame:
    manifest_path = storage / manifest_name
    if manifest_path.exists():
        current = pd.read_parquet(manifest_path)
        combined = pd.concat([current, new_rows], ignore_index=True)
    else:
        combined = new_rows.copy()

    if combined.empty:
        return combined

    combined = combined.drop_duplicates(["exchange", "pair", "timeframe"], keep="last")
    combined = combined.sort_values(["exchange", "pair", "timeframe"]).reset_index(drop=True)
    write_parquet_atomic(combined, manifest_path)
    return combined


def resample_ohlcv(
    storage: Path,
    exchange: str,
    source_timeframe: str,
    target_timeframe: str,
    pairs: list[str] | None,
) -> pd.DataFrame:
    manifest_path = storage / "ohlcv_manifest.parquet"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Run migrate first.")

    manifest = pd.read_parquet(manifest_path)
    manifest = manifest[manifest["timeframe"] == source_timeframe]
    if pairs:
        manifest = manifest[manifest["pair"].isin(pairs)]

    rows = []
    required_source_bars = expected_source_bars(source_timeframe, target_timeframe)
    for row in manifest.itertuples(index=False):
        source_path = Path(row.path)
        df = pd.read_parquet(source_path).sort_values("date")
        if df.empty:
            continue
        resampled = (
            df.set_index("date")
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
            resampled = resampled[resampled["source_bars"] == required_source_bars].copy()
        resampled = resampled.drop(columns=["source_bars"])
        resampled["exchange"] = exchange
        resampled["pair"] = row.pair
        resampled["timeframe"] = target_timeframe

        out_path = ohlcv_path(storage, exchange, row.pair, target_timeframe)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(resampled, out_path)
        rows.append(
            {
                "exchange": exchange,
                "pair": row.pair,
                "timeframe": target_timeframe,
                "rows": len(resampled),
                "start": resampled["date"].min(),
                "end": resampled["date"].max(),
                "path": str(out_path),
            }
        )

    new_manifest = pd.DataFrame(rows)
    if not new_manifest.empty:
        update_manifest(storage, new_manifest, "ohlcv_manifest.parquet")
    refresh_duckdb(storage)
    return new_manifest


def generate_features(
    storage: Path, exchange: str, pairs: list[str] | None, timeframes: list[str] | None
) -> pd.DataFrame:
    manifest_path = storage / "ohlcv_manifest.parquet"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Run migrate first.")

    manifest = pd.read_parquet(manifest_path)
    if pairs:
        manifest = manifest[manifest["pair"].isin(pairs)]
    if timeframes:
        manifest = manifest[manifest["timeframe"].isin(timeframes)]

    rows = []
    for row in manifest.itertuples(index=False):
        in_path = Path(row.path)
        df = pd.read_parquet(in_path)
        feat = add_core_features(df)
        feat["exchange"] = exchange
        feat["pair"] = row.pair
        feat["timeframe"] = row.timeframe
        out_path = feature_path(storage, exchange, row.pair, row.timeframe)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(feat, out_path)
        rows.append(
            {
                "exchange": exchange,
                "pair": row.pair,
                "timeframe": row.timeframe,
                "rows": len(feat),
                "start": feat["date"].min(),
                "end": feat["date"].max(),
                "path": str(out_path),
            }
        )

    features_manifest = pd.DataFrame(rows)
    if not features_manifest.empty:
        update_manifest(storage, features_manifest, "features_manifest.parquet")
        features_manifest = pd.read_parquet(storage / "features_manifest.parquet")
        combined = []
        for item in features_manifest.itertuples(index=False):
            combined.append(pd.read_parquet(item.path))
        write_parquet_atomic(
            pd.concat(combined, ignore_index=True), storage / "features_all.parquet"
        )
    refresh_duckdb(storage)
    return features_manifest


def refresh_duckdb(storage: Path) -> None:
    db_path = storage / "lab.duckdb"
    con = duckdb.connect(str(db_path))

    def sql_path(path: Path) -> str:
        return str(path).replace("'", "''")

    try:
        con.execute(
            f"CREATE OR REPLACE VIEW ohlcv_manifest AS "
            f"SELECT * FROM read_parquet('{sql_path(storage / 'ohlcv_manifest.parquet')}')"
        )
        features_manifest = storage / "features_manifest.parquet"
        if features_manifest.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW features_manifest AS "
                f"SELECT * FROM read_parquet('{sql_path(features_manifest)}')"
            )
        features_all = storage / "features_all.parquet"
        if features_all.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW features AS "
                f"SELECT * FROM read_parquet('{sql_path(features_all)}')"
            )
        results = storage / "results" / "edge_summary.parquet"
        if results.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW edge_summary AS "
                f"SELECT * FROM read_parquet('{sql_path(results)}')"
            )
        trades = storage / "results" / "edge_trades.parquet"
        if trades.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW edge_trades AS "
                f"SELECT * FROM read_parquet('{sql_path(trades)}')"
            )
        signals = storage / "results" / "edge_signals.parquet"
        if signals.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW edge_signals AS "
                f"SELECT * FROM read_parquet('{sql_path(signals)}')"
            )
        wfo = storage / "results" / "walk_forward_summary.parquet"
        if wfo.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW walk_forward_summary AS "
                f"SELECT * FROM read_parquet('{sql_path(wfo)}')"
            )
        season = storage / "results" / "season_summary.parquet"
        if season.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW season_summary AS "
                f"SELECT * FROM read_parquet('{sql_path(season)}')"
            )
        history = storage / "results" / "run_history.parquet"
        if history.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW run_history AS "
                f"SELECT * FROM read_parquet('{sql_path(history)}')"
            )
        account_summary = storage / "results" / "account_summary.parquet"
        if account_summary.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW account_summary AS "
                f"SELECT * FROM read_parquet('{sql_path(account_summary)}')"
            )
        account_trades = storage / "results" / "account_trades.parquet"
        if account_trades.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW account_trades AS "
                f"SELECT * FROM read_parquet('{sql_path(account_trades)}')"
            )
        account_equity = storage / "results" / "account_equity.parquet"
        if account_equity.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW account_equity AS "
                f"SELECT * FROM read_parquet('{sql_path(account_equity)}')"
            )
        account_rejections = storage / "results" / "account_rejections.parquet"
        if account_rejections.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW account_rejections AS "
                f"SELECT * FROM read_parquet('{sql_path(account_rejections)}')"
            )
    finally:
        con.close()


def parse_csv_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Research lab data warehouse")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--exchange", default="kucoin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    migrate = sub.add_parser("migrate", help="Convert Freqtrade feather OHLCV to Parquet warehouse")
    migrate.add_argument("--source", default="user_data/data/kucoin", type=Path)

    resample = sub.add_parser("resample", help="Resample warehouse OHLCV, for example 1h to 4h")
    resample.add_argument("--source-timeframe", default="1h")
    resample.add_argument("--target-timeframe", default="4h")
    resample.add_argument("--pairs", default=None, help="Comma-separated pairs")

    features = sub.add_parser("features", help="Generate reusable feature parquet files")
    features.add_argument(
        "--pairs", default=None, help="Comma-separated pairs, example BTC/USDT,ETH/USDT"
    )
    features.add_argument(
        "--timeframes", default=None, help="Comma-separated timeframes, example 1h,5m"
    )

    sub.add_parser("index", help="Refresh DuckDB views")

    args = parser.parse_args()
    args.storage.mkdir(parents=True, exist_ok=True)

    if args.cmd == "migrate":
        manifest = migrate_ohlcv(args.source, args.storage, args.exchange)
        print(manifest.to_string(index=False))
    elif args.cmd == "resample":
        manifest = resample_ohlcv(
            args.storage,
            args.exchange,
            args.source_timeframe,
            args.target_timeframe,
            parse_csv_arg(args.pairs),
        )
        print(manifest.to_string(index=False))
    elif args.cmd == "features":
        manifest = generate_features(
            args.storage,
            args.exchange,
            parse_csv_arg(args.pairs),
            parse_csv_arg(args.timeframes),
        )
        print(manifest.to_string(index=False))
    elif args.cmd == "index":
        refresh_duckdb(args.storage)
        print(f"Refreshed {args.storage / 'lab.duckdb'}")


if __name__ == "__main__":
    main()
