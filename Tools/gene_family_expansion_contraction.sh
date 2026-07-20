#!/bin/bash
###############################################################################
# gene_family_expansion_contraction.sh
#
# Gene Family Expansion & Contraction Analysis Pipeline for PTDB
# Workflow: planttpdb-cafe init → prepare-input → make-cafe-matrix
#
# This script replicates the PTDB Gene Family Expansion & Contraction tool's
# synchronous matrix generation pipeline (run_cafe_matrix_only).
# It generates a gene family count matrix using CAFE5 without running the
# full async expansion/contraction analysis.
#
# For the full analysis pipeline (including species tree, dating, and CAFE5),
# use the --full flag to trigger run_all_v2.sh (requires conda environment).
#
# Usage:
#   # Matrix only (synchronous, ~5 min):
#   bash gene_family_expansion_contraction.sh \
#       --species species1,species2,species3 \
#       --matrix-type tc \
#       --outdir ./cafe_output
#
#   # Full analysis (asynchronous, several hours):
#   bash gene_family_expansion_contraction.sh \
#       --species species1,species2,... \
#       --matrix-type tc \
#       --outdir ./cafe_output \
#       --full \
#       --email user@example.com
#
# Parameters:
#   --species       Comma-separated list of species (required, min 3)
#   --matrix-type   Gene family matrix type: tc or symbol (required)
#   --family-list   Optional: path to a custom gene family list file
#   --outdir        Output directory (default: ./cafe_output)
#   --full          Run the full CAFE5 pipeline (async, includes species tree + dating)
#   --email         Email for notification (required with --full)
#   --label         Custom job label (auto-generated if not provided)
#   --config        Path to config.yaml (default: uses bundled config)
#
# Dependencies:
#   - planttpdb-cafe  (CAFE5 wrapper, installed via conda: planttpdb_cafe env)
#   - BUSCO           (required for --full mode)
#   - IQ-TREE         (required for --full mode)
#   - MCMCtree (PAML) (required for --full mode)
#
# Note: This script uses the same planttpdb-cafe binary and pipeline as the
# PTDB web server. Paths to the binary and software directory are configured
# below. Adjust them to match your server installation.
###############################################################################

set -euo pipefail

# ===========================================================================
# Configuration — adjust these paths to match your server environment
# ===========================================================================
PLANTTPDB_CAFE="/store/dxliu/miniconda3/envs/planttpdb_cafe/bin/planttpdb-cafe"
SOFTWARE_DIR="/store/dxliu/wchuang/protein/cafe/00.planttpdb_expansion_contraction.171"
CONDA_BIN="/store/dxliu/miniconda3/bin"
CONDA_ENV="planttpdb_cafe"
SEND_MAIL_PY="/store/dxliu/html/bna_rnaseq/public/bnir_public/onlineGWAS/send_mail.py"

# ===========================================================================

# --- Default parameters ---
SPECIES=""
MATRIX_TYPE=""
FAMILY_LIST=""
OUTPUT_DIR="./cafe_output"
FULL_PIPELINE=false
EMAIL=""
LABEL=""
CONFIG_YAML=""

# --- Parse arguments ---
usage() {
    cat <<EOF
Usage: $0 --species <sp1,sp2,...> --matrix-type <tc|symbol> [options]

Required:
  --species       Comma-separated list of species (min 3)
  --matrix-type   Gene family type: tc or symbol

Options:
  --family-list   Path to custom gene family list file
  --outdir        Output directory (default: ./cafe_output)
  --full          Run full CAFE5 pipeline (async, requires conda env)
  --email         Email for job notification (required with --full)
  --label         Custom job label (auto-generated if not set)
  --config        Path to config.yaml
  --help          Show this help message
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --species)       SPECIES="$2";       shift 2 ;;
        --matrix-type)   MATRIX_TYPE="$2";   shift 2 ;;
        --family-list)   FAMILY_LIST="$2";   shift 2 ;;
        --outdir)        OUTPUT_DIR="$2";    shift 2 ;;
        --full)          FULL_PIPELINE=true; shift ;;
        --email)         EMAIL="$2";         shift 2 ;;
        --label)         LABEL="$2";         shift 2 ;;
        --config)        CONFIG_YAML="$2";  shift 2 ;;
        --help|-h)       usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# --- Validate required parameters ---
if [[ -z "$SPECIES" ]]; then
    echo "Error: --species is required."
    usage
fi

if [[ -z "$MATRIX_TYPE" ]]; then
    echo "Error: --matrix-type is required."
    usage
fi

if [[ ! "$MATRIX_TYPE" =~ ^(tc|symbol)$ ]]; then
    echo "Error: Invalid matrix type '$MATRIX_TYPE'. Must be tc or symbol."
    exit 1
fi

# Parse and validate species list
IFS=',' read -ra SPECIES_ARR <<< "$SPECIES"
SPECIES_CLEAN=()
for sp in "${SPECIES_ARR[@]}"; do
    sp=$(echo "$sp" | xargs)
    if [[ -n "$sp" ]]; then
        SPECIES_CLEAN+=("$sp")
    fi
done

if [[ ${#SPECIES_CLEAN[@]} -lt 3 ]]; then
    echo "Error: At least 3 species are required (got ${#SPECIES_CLEAN[@]})."
    exit 1
fi

if [[ "$FULL_PIPELINE" == true && -z "$EMAIL" ]]; then
    echo "Error: --email is required when using --full."
    exit 1
fi

# --- Resolve config path ---
if [[ -z "$CONFIG_YAML" ]]; then
    CONFIG_YAML="$SOFTWARE_DIR/configs/config.yaml"
fi

# --- Generate label if not provided ---
if [[ -z "$LABEL" ]]; then
    TIMESTAMP=$(date +%s)
    HASH=$(echo "${SPECIES}${MATRIX_TYPE}" | md5sum | cut -c1-6)
    LABEL="${TIMESTAMP}_${MATRIX_TYPE}_${HASH}"
fi

# --- Resolve family list file ---
if [[ -n "$FAMILY_LIST" ]]; then
    LIST_FILE="$FAMILY_LIST"
elif [[ ! -f "$FAMILY_LIST" ]]; then
    if [[ "$MATRIX_TYPE" == "tc" ]]; then
        LIST_FILE="$SOFTWARE_DIR/07.tc.code_156.list"
    else
        LIST_FILE="$SOFTWARE_DIR/07.symbol.list"
    fi
fi

# --- Create output directories ---
mkdir -p "$OUTPUT_DIR"
WORKDIR="$OUTPUT_DIR/work_${LABEL}"
mkdir -p "$WORKDIR"

echo "========================================"
echo " PTDB Gene Family Expansion & Contraction"
echo "========================================"
echo "Species:    ${SPECIES_CLEAN[*]}"
echo "Matrix:     $MATRIX_TYPE"
echo "Label:      $LABEL"
echo "Outdir:     $WORKDIR"
echo "Full mode:  $FULL_PIPELINE"
echo "========================================"

# --- Write species list file ---
SPECIES_FILE="$WORKDIR/${LABEL}_species.list"
printf '%s\n' "${SPECIES_CLEAN[@]}" > "$SPECIES_FILE"
echo "[Step 1] Species list written to $SPECIES_FILE (${#SPECIES_CLEAN[@]} species)"

# ===========================================================================
# Matrix-only pipeline (synchronous)
# ===========================================================================
if [[ "$FULL_PIPELINE" != true ]]; then

    echo ""
    echo "[Step 2] Running planttpdb-cafe init..."
    $PLANTTPDB_CAFE init \
        --species "$SPECIES_FILE" \
        --config "$CONFIG_YAML" \
        --outdir "$WORKDIR" \
        --force 2>&1 | tee "$WORKDIR/init.log"

    if [[ $? -ne 0 || ! $(grep -q '\[OK\]' "$WORKDIR/init.log" 2>/dev/null) ]]; then
        echo "Error: planttpdb-cafe init failed. See $WORKDIR/init.log"
        exit 1
    fi

    echo ""
    echo "[Step 3] Running planttpdb-cafe prepare-input..."
    $PLANTTPDB_CAFE prepare-input \
        --outdir "$WORKDIR" \
        --force 2>&1 | tee "$WORKDIR/prepare_input.log"

    if [[ $? -ne 0 || ! $(grep -q '\[OK\]' "$WORKDIR/prepare_input.log" 2>/dev/null) ]]; then
        echo "Error: planttpdb-cafe prepare-input failed. See $WORKDIR/prepare_input.log"
        exit 1
    fi

    echo ""
    echo "[Step 4] Running planttpdb-cafe make-cafe-matrix..."
    $PLANTTPDB_CAFE make-cafe-matrix \
        --outdir "$WORKDIR" \
        --matrix-type "$MATRIX_TYPE" \
        --list "$LIST_FILE" \
        --force 2>&1 | tee "$WORKDIR/make_cafe_matrix.log"

    if [[ $? -ne 0 || ! $(grep -q '\[OK\]' "$WORKDIR/make_cafe_matrix.log" 2>/dev/null) ]]; then
        echo "Error: planttpdb-cafe make-cafe-matrix failed. See $WORKDIR/make_cafe_matrix.log"
        exit 1
    fi

    # --- Locate output TSV ---
    TSV_FILENAME="$([[ "$MATRIX_TYPE" == "tc" ]] && echo "tc.filtered.tsv" || echo "symbol.filtered.tsv")"
    TSV_PATH="$WORKDIR/results/04_cafe_input/$TSV_FILENAME"

    if [[ -f "$TSV_PATH" ]]; then
        FAMILY_COUNT=$(tail -n +2 "$TSV_PATH" | wc -l)
        echo ""
        echo "========================================"
        echo " Matrix generation completed!"
        echo "========================================"
        echo "Gene families: $FAMILY_COUNT"
        echo "Species:       ${#SPECIES_CLEAN[@]}"
        echo "TSV matrix:    $TSV_PATH"
        echo "========================================"
    else
        echo ""
        echo "Warning: Expected TSV file not found at $TSV_PATH"
        echo "Check the logs in $WORKDIR for details."
    fi

    exit 0
fi

# ===========================================================================
# Full pipeline (asynchronous, requires conda environment)
# ===========================================================================

echo ""
echo "[Step 2] Launching full CAFE5 pipeline (async)..."
echo "  Pipeline: BUSCO → IQ-TREE → MCMCtree → CAFE5"
echo "  This may take several hours."

# Activate conda environment and run
RUN_CMD="source $CONDA_BIN/activate $CONDA_ENV && cd $SOFTWARE_DIR && bash cfluo/run_all_v2.sh run-all \
    --species $SPECIES_FILE \
    --config $CONFIG_YAML \
    --outdir $WORKDIR \
    --matrix-type $MATRIX_TYPE \
    --list $LIST_FILE \
    --label $LABEL \
    --force"

# Run in background
LOG_FILE="$WORKDIR/pipeline.log"
bash -c "$RUN_CMD" > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$WORKDIR/pipeline.pid"
echo "  Pipeline PID: $PID"

# Send submission email
if [[ -n "$EMAIL" && -f "$SEND_MAIL_PY" ]]; then
    SAFE_EMAIL=$(printf '%q' "$EMAIL")
    python "$SEND_MAIL_PY" "$SAFE_EMAIL" \
        "PlantTPDB-CAFE: Job Successfully Submitted!" \
        "Your Gene Family Expansion & Contraction analysis (ID: $LABEL) has been submitted (PID: $PID). You will receive an email when complete." \
        na 2>/dev/null || true
fi

echo ""
echo "========================================"
echo " Full pipeline submitted!"
echo "========================================"
echo "Job ID:    $LABEL"
echo "PID:       $PID"
echo "Log file:  $LOG_FILE"
echo ""
echo "Monitor progress:"
echo "  tail -f $LOG_FILE"
echo ""
echo "Check status file:"
echo "  cat $WORKDIR/job.status.json"
echo "========================================"
