#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/adrian/freqtrade}"
exec env PIPELINE_MODE=research "$ROOT_DIR/research_lab/scripts/run_pipeline.sh" "$@"
