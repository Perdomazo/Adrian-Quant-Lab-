#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/adrian/freqtrade}"
VENV_DIR="${VENV_DIR:-.venv}"
SOURCE_DIR="${SOURCE_DIR:-user_data/data/kucoin}"
STORAGE_DIR="${STORAGE_DIR:-research_lab/storage}"
TIMEFRAMES="${TIMEFRAMES:-1h,4h}"
PAIRS="${PAIRS:-BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT}"
DOWNLOAD_DATA="${DOWNLOAD_DATA:-1}"
DOWNLOAD_METHOD="${DOWNLOAD_METHOD:-auto}"
DOWNLOAD_CONFIG="${DOWNLOAD_CONFIG:-config.kucoin.example.json}"
DOWNLOAD_TIMEFRAMES="${DOWNLOAD_TIMEFRAMES:-1h}"
NEW_PAIRS_DAYS="${NEW_PAIRS_DAYS:-1200}"
DOWNLOAD_DOCKER_IMAGE="${DOWNLOAD_DOCKER_IMAGE:-freqtradeorg/freqtrade:stable}"
LOCK_FILE="${LOCK_FILE:-/run/lock/adrian-quant-ingest.lock}"

if [[ "${ADRIAN_INGEST_LOCKED:-0}" != "1" ]]; then
  mkdir -p "$STORAGE_DIR"
  if [[ ! -w "$(dirname "$LOCK_FILE")" ]]; then
    LOCK_FILE="$STORAGE_DIR/ingest.lock"
  fi
  exec env ADRIAN_INGEST_LOCKED=1 LOCK_FILE="$LOCK_FILE" flock -n "$LOCK_FILE" "$0" "$@"
fi

cd "$ROOT_DIR"
source "$VENV_DIR/bin/activate"

RUN_ID="${RUN_ID:-ingest-$(python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" new-id)}"
export RUN_ID

on_error() {
  local exit_code=$?
  python -m research_lab.ingest_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" \
    --status failed \
    --error "ingest_failed_exit_${exit_code}" || true
  exit "$exit_code"
}
trap on_error ERR

python -m research_lab.ingest_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" \
  --status running

if [[ "$DOWNLOAD_DATA" == "1" ]]; then
  IFS=',' read -r -a PAIR_LIST <<< "$PAIRS"
  IFS=',' read -r -a DOWNLOAD_TIMEFRAME_LIST <<< "$DOWNLOAD_TIMEFRAMES"
  if [[ "$DOWNLOAD_METHOD" == "auto" ]]; then
    if command -v freqtrade >/dev/null 2>&1; then
      DOWNLOAD_METHOD="venv"
    else
      DOWNLOAD_METHOD="docker"
    fi
  fi

  if [[ "$DOWNLOAD_METHOD" == "venv" ]]; then
    freqtrade download-data \
      --config "$DOWNLOAD_CONFIG" \
      --pairs "${PAIR_LIST[@]}" \
      --timeframes "${DOWNLOAD_TIMEFRAME_LIST[@]}" \
      --new-pairs-days "$NEW_PAIRS_DAYS"
  elif [[ "$DOWNLOAD_METHOD" == "docker" ]]; then
    docker run --rm \
      -v "$ROOT_DIR/user_data:/freqtrade/user_data" \
      -v "$ROOT_DIR/$DOWNLOAD_CONFIG:/freqtrade/$DOWNLOAD_CONFIG:ro" \
      "$DOWNLOAD_DOCKER_IMAGE" \
      download-data \
      --config "/freqtrade/$DOWNLOAD_CONFIG" \
      --pairs "${PAIR_LIST[@]}" \
      --timeframes "${DOWNLOAD_TIMEFRAME_LIST[@]}" \
      --new-pairs-days "$NEW_PAIRS_DAYS"
  else
    echo "Unsupported DOWNLOAD_METHOD=$DOWNLOAD_METHOD" >&2
    exit 2
  fi
else
  echo "DOWNLOAD_DATA=0; skipping freqtrade download-data."
fi

shopt -s nullglob
SOURCE_FILES=("$SOURCE_DIR"/*.feather)
if ((${#SOURCE_FILES[@]} > 0)); then
  python -m research_lab.warehouse --storage "$STORAGE_DIR" --exchange kucoin migrate \
    --source "$SOURCE_DIR"
else
  echo "No feather files found in $SOURCE_DIR; using existing Parquet warehouse."
fi

python -m research_lab.warehouse --storage "$STORAGE_DIR" resample \
  --source-timeframe 1h \
  --target-timeframe 4h

python -m research_lab.data_quality \
  --storage "$STORAGE_DIR" \
  --timeframes "$TIMEFRAMES" \
  --run-id "$RUN_ID" \
  --fail-on-error

python -m research_lab.ingest_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" \
  --status success
trap - ERR
