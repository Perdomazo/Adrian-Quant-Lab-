#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/adrian/freqtrade}"
VENV_DIR="${VENV_DIR:-.venv}"
PIPELINE_MODE="${PIPELINE_MODE:-research}"
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
LOCK_FILE="${LOCK_FILE:-$STORAGE_DIR/pipeline.lock}"
RUN_PROMOTION="${RUN_PROMOTION:-0}"
PROMOTION_RULES="${PROMOTION_RULES:-research_lab/config/promotion_rules.json}"
DECISION_RULES="${DECISION_RULES:-research_lab/config/decision_rules.json}"
ACCOUNT_RULES="${ACCOUNT_RULES:-research_lab/config/account_rules.json}"
PAIR_UNIVERSE="${PAIR_UNIVERSE:-research_lab/config/pair_universe.json}"
ACCOUNT_DECISION_RULES="${ACCOUNT_DECISION_RULES:-research_lab/config/account_decision_rules.json}"
TRAIN_MONTHS="${TRAIN_MONTHS:-18}"
TEST_MONTHS="${TEST_MONTHS:-6}"
STEP_MONTHS="${STEP_MONTHS:-6}"

if [[ "$PIPELINE_MODE" == "ingest" ]]; then
  exec "$ROOT_DIR/research_lab/scripts/run_daily_ingest.sh" "$@"
elif [[ "$PIPELINE_MODE" == "deep" ]]; then
  exec "$ROOT_DIR/research_lab/scripts/run_deep_validation.sh" "$@"
elif [[ "$PIPELINE_MODE" != "research" ]]; then
  echo "Unsupported PIPELINE_MODE=$PIPELINE_MODE" >&2
  exit 2
fi

if [[ "${ADRIAN_PIPELINE_LOCKED:-0}" != "1" ]]; then
  mkdir -p "$STORAGE_DIR"
  if [[ ! -w "$(dirname "$LOCK_FILE")" ]]; then
    LOCK_FILE="$STORAGE_DIR/pipeline.lock"
  fi
  exec env ADRIAN_PIPELINE_LOCKED=1 LOCK_FILE="$LOCK_FILE" flock -n "$LOCK_FILE" "$0" "$@"
fi

cd "$ROOT_DIR"
source "$VENV_DIR/bin/activate"

RUN_ID="${RUN_ID:-$(python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" new-id)}"
export RUN_ID
PIPELINE_START_TS="$(date +%s)"
STAGE_START_TS=0

peak_memory_mb() {
  if [[ -r "/proc/$$/status" ]]; then
    awk '/VmHWM:/ {printf "%.3f", $2 / 1024}' "/proc/$$/status"
  else
    echo "0"
  fi
}

record_metric() {
  local stage="$1"
  local seconds="$2"
  python -m research_lab.pipeline_metrics \
    --manifest "$STORAGE_DIR/results/runs/$RUN_ID/run_manifest.json" \
    --manifest "$STORAGE_DIR/results/latest/run_manifest.json" \
    --stage "$stage" \
    --seconds "$seconds" \
    --peak-memory-mb "$(peak_memory_mb)" || true
}

record_total() {
  local end_ts
  end_ts="$(date +%s)"
  python -m research_lab.pipeline_metrics \
    --manifest "$STORAGE_DIR/results/runs/$RUN_ID/run_manifest.json" \
    --manifest "$STORAGE_DIR/results/latest/run_manifest.json" \
    --total-seconds "$((end_ts - PIPELINE_START_TS))" \
    --peak-memory-mb "$(peak_memory_mb)" || true
}

stage_start() {
  STAGE_START_TS="$(date +%s)"
}

stage_end() {
  local stage="$1"
  local end_ts
  end_ts="$(date +%s)"
  record_metric "$stage" "$((end_ts - STAGE_START_TS))"
}

on_error() {
  local exit_code=$?
  record_total
  python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" complete \
    --status failed \
    --error "pipeline_failed_exit_${exit_code}" || true
  exit "$exit_code"
}
trap on_error ERR

python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" start

stage_start
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
stage_end download

stage_start
shopt -s nullglob
SOURCE_FILES=("$SOURCE_DIR"/*.feather)
if ((${#SOURCE_FILES[@]} > 0)); then
  python -m research_lab.warehouse --storage "$STORAGE_DIR" --exchange kucoin migrate \
    --source "$SOURCE_DIR"
else
  echo "No feather files found in $SOURCE_DIR; using existing Parquet warehouse."
fi
stage_end migration

stage_start
python -m research_lab.warehouse --storage "$STORAGE_DIR" resample \
  --source-timeframe 1h \
  --target-timeframe 4h
stage_end resample

stage_start
python -m research_lab.data_quality \
  --storage "$STORAGE_DIR" \
  --timeframes "$TIMEFRAMES" \
  --run-id "$RUN_ID" \
  --fail-on-error
stage_end data_quality

stage_start
python -m research_lab.warehouse --storage "$STORAGE_DIR" features \
  --timeframes "$TIMEFRAMES"
stage_end features

stage_start
python -m research_lab.edge_engine \
  --storage "$STORAGE_DIR" \
  --pairs "$PAIRS" \
  --timeframes "$TIMEFRAMES" \
  --decision-rules "$DECISION_RULES" \
  --run-id "$RUN_ID"
stage_end edge_engine

stage_start
python -m research_lab.edge_engine \
  --storage "$STORAGE_DIR" \
  --pairs "$PAIRS" \
  --timeframes "$TIMEFRAMES" \
  --decision-rules "$DECISION_RULES" \
  --run-id "$RUN_ID" \
  --only-walk-forward
stage_end walk_forward

stage_start
python -m research_lab.account_simulator \
  --storage "$STORAGE_DIR" \
  --exchange kucoin \
  --pairs "$PAIRS" \
  --timeframes "$TIMEFRAMES" \
  --rules "$ACCOUNT_RULES" \
  --run-id "$RUN_ID"
stage_end account_simulator

stage_start
python -m research_lab.account_validation \
  --storage "$STORAGE_DIR" \
  --rules "$ACCOUNT_RULES" \
  --run-id "$RUN_ID" \
  --fail-on-error
stage_end account_validation

stage_start
python -m research_lab.account_oos \
  --storage "$STORAGE_DIR" \
  --exchange kucoin \
  --pairs "$PAIRS" \
  --timeframes "$TIMEFRAMES" \
  --account-rules "$ACCOUNT_RULES" \
  --pair-universe "$PAIR_UNIVERSE" \
  --train-months "$TRAIN_MONTHS" \
  --test-months "$TEST_MONTHS" \
  --step-months "$STEP_MONTHS" \
  --run-id "$RUN_ID"
stage_end account_oos

stage_start
python -m research_lab.account_oos_validation \
  --storage "$STORAGE_DIR" \
  --run-id "$RUN_ID" \
  --fail-on-error
stage_end account_oos_validation

stage_start
python -m research_lab.account_candidate \
  --root "$ROOT_DIR" \
  --storage "$STORAGE_DIR" \
  --rules "$ACCOUNT_DECISION_RULES" \
  --pair-universe "$PAIR_UNIVERSE" \
  --run-id "$RUN_ID"
stage_end account_candidate

stage_start
python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" snapshot
stage_end snapshot
record_total
python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" --run-id "$RUN_ID" complete \
  --status success
trap - ERR

if [[ "$RUN_PROMOTION" == "1" ]]; then
  python -m research_lab.promotion \
    --root "$ROOT_DIR" \
    --storage "$STORAGE_DIR" \
    --rules "$PROMOTION_RULES" \
    --run-id "$RUN_ID" \
    --execute
fi
