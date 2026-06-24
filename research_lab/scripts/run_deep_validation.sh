#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/adrian/freqtrade}"
VENV_DIR="${VENV_DIR:-.venv}"
STORAGE_DIR="${STORAGE_DIR:-research_lab/storage}"
LOCK_FILE="${LOCK_FILE:-/run/lock/adrian-quant-deep.lock}"
RUN_PROMOTION="${RUN_PROMOTION:-0}"
DEEP_MC_SIMS="${DEEP_MC_SIMS:-5000}"
DEEP_BLOCK_SIZE="${DEEP_BLOCK_SIZE:-20}"
DEEP_SEED="${DEEP_SEED:-17062026}"
INITIAL_CASH="${INITIAL_CASH:-1000.0}"

if [[ "${ADRIAN_DEEP_LOCKED:-0}" != "1" ]]; then
  mkdir -p "$STORAGE_DIR"
  if [[ ! -w "$(dirname "$LOCK_FILE")" ]]; then
    LOCK_FILE="$STORAGE_DIR/deep.lock"
  fi
  exec env ADRIAN_DEEP_LOCKED=1 LOCK_FILE="$LOCK_FILE" flock -n "$LOCK_FILE" "$0" "$@"
fi

cd "$ROOT_DIR"
source "$VENV_DIR/bin/activate"

RUN_ID="${RUN_ID:-deep-$(python -m research_lab.run_manager --root "$ROOT_DIR" --storage "$STORAGE_DIR" new-id)}"
export RUN_ID RUN_PROMOTION

start_ts="$(date +%s)"
PIPELINE_MODE=research "$ROOT_DIR/research_lab/scripts/run_pipeline.sh"
after_research_ts="$(date +%s)"
research_seconds="$((after_research_ts - start_ts))"

python -m research_lab.deep_validation \
  --storage "$STORAGE_DIR" \
  --run-id "$RUN_ID" \
  --sims "$DEEP_MC_SIMS" \
  --block-size "$DEEP_BLOCK_SIZE" \
  --seed "$DEEP_SEED" \
  --initial-cash "$INITIAL_CASH" \
  --research-seconds "$research_seconds" \
  --fail-on-error
