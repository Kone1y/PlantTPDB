#!/bin/bash
# run_weekly_mapper.sh — cron entry point for the symbol mapping stage (Wednesdays 08:30).
#
# Data flow: runs right after auto_plant_transporter_pipeline.py (Wednesdays 08:00,
#            ~3 minutes) and processes only the current run's filtered.tsv;
#            outputs are written back into that run directory:
#              results/run_YYYYMMDD/symbol_literature_map.json / .tsv
#              results/run_YYYYMMDD/symbol_family_map.tsv
#            LLM extraction cache is shared across runs
#            (results/llm_symbol_cache.json; repeated articles cost zero API calls).
#
# Usage:
#   ./run_weekly_mapper.sh              # process today's run (cron usage)
#   ./run_weekly_mapper.sh run_20260909 # explicit run (for testing / backfill)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/../results"
RUN="${1:-run_$(date +%Y%m%d)}"

# LLM configuration (cron has no user shell environment, so load explicitly;
# the mapper also falls back to its parent directory for pipeline.env).
# pipeline.env holds credentials and is NOT distributed with this release:
# create it from LLM_API_CONFIG.example, or export the variables directly.
if [ -f "$SCRIPT_DIR/pipeline.env" ]; then
    . "$SCRIPT_DIR/pipeline.env"
fi
if [ -z "${LLM_API_KEY:-}" ]; then
    echo "[error] LLM_API_KEY is not set (copy LLM_API_CONFIG.example to pipeline.env and fill in your key, or export it)" >&2
    exit 1
fi

# The run directory must exist (fail loudly when the pipeline did not run or
# produced no screening output; never silently emit empty results).
if [ ! -f "$RESULTS_DIR/$RUN/filtered.tsv" ]; then
    echo "[error] $RESULTS_DIR/$RUN/filtered.tsv does not exist (pipeline not run or no screening results)" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
exec "${PYTHON:-python3}" symbol_literature_mapper.py \
    --results-dir "$RESULTS_DIR" \
    --runs "$RUN" \
    --tsv-only \
    --use-llm \
    --output-dir "$RESULTS_DIR/$RUN" \
    >> "$SCRIPT_DIR/symbol_mapper.log" 2>&1
