#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(pwd)}"
VENV_DIR="${VENV_DIR:-.venv}"
SOURCE_DIR="${SOURCE_DIR:-user_data/data/kucoin}"
STORAGE_DIR="${STORAGE_DIR:-/tmp/adrian_quant_lab_smoke}"
PAIR="${PAIR:-BTC/USDT}"
TIMEFRAME="${TIMEFRAME:-1h}"
export STORAGE_DIR

cd "$ROOT_DIR"
source "$VENV_DIR/bin/activate"

rm -rf "$STORAGE_DIR"
mkdir -p "$STORAGE_DIR"

python -m research_lab.warehouse --storage "$STORAGE_DIR" migrate --source "$SOURCE_DIR"
python -m research_lab.warehouse --storage "$STORAGE_DIR" features --pairs "$PAIR" --timeframes "$TIMEFRAME"
python -m research_lab.edge_engine --storage "$STORAGE_DIR" --pairs "$PAIR" --timeframes "$TIMEFRAME"

test -f "$STORAGE_DIR/ohlcv_manifest.parquet"
test -f "$STORAGE_DIR/features_manifest.parquet"
test -f "$STORAGE_DIR/features_all.parquet"
test -f "$STORAGE_DIR/results/edge_summary.parquet"
test -f "$STORAGE_DIR/lab.duckdb"

python - <<'PY'
from pathlib import Path
import duckdb

storage = Path(__import__("os").environ["STORAGE_DIR"])
con = duckdb.connect(str(storage / "lab.duckdb"))
try:
    checks = {
        "ohlcv_manifest": "select count(*) from ohlcv_manifest",
        "features_manifest": "select count(*) from features_manifest",
        "features": "select count(*) from features",
        "edge_summary": "select count(*) from edge_summary",
    }
    for name, sql in checks.items():
        value = con.execute(sql).fetchone()[0]
        if value <= 0:
            raise SystemExit(f"{name} is empty")
        print(f"{name}: {value}")
finally:
    con.close()
PY

echo "Smoke test OK: $STORAGE_DIR"
