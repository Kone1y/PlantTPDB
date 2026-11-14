#!/bin/bash

# AF3 stage-2 batch inference driver.
# Installation paths are read from environment variables with
# placeholder defaults (see AF3/README.md "Run record").
#
# Usage: bash run_inference.sh <species_name> <list_file>
#   $1: species name (used to create the output subdirectory, e.g. Zea_mays)
#   $2: list file with one stage-1 *_data.json path per line
#       (e.g. /path/to/Zea_mays_data_json.list)

# ================= user configuration =================
SPECIES=$1
INPUT_LIST=$2
CONCURRENCY="${CONCURRENCY:-2}"    # parallel jobs within this script

# Path configuration (EDIT THESE or export before running)
MODEL_PATH="${MODEL_PATH:-/path/to/alphafold3-v3.0.1/models}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/path/to/af3_inference_output}"
DB_PATH="${DB_PATH:-/path/to/alphafold3/database}"
RUN_AF_PY="${RUN_AF_PY:-/path/to/alphafold3-v3.0.1/run_alphafold.py}"

# Environment
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.45
export TF_CPP_MIN_LOG_LEVEL=2
# ======================================================

# 1. Basic checks
if [ -z "$SPECIES" ] || [ -z "$INPUT_LIST" ]; then
    echo "[ERROR] Usage: bash $0 <species_name> <list_file>"
    echo "Example: bash $0 Zea_mays /path/to/Zea_mays_data_json.list"
    exit 1
fi

if [ ! -f "$INPUT_LIST" ]; then
    echo "[ERROR] List file not found: $INPUT_LIST"
    exit 1
fi

# 2. Create the per-species output directory
SPE_OUTPUT_DIR="${OUTPUT_ROOT}/${SPECIES}"
mkdir -p "$SPE_OUTPUT_DIR"

echo "[*] Starting | species: $SPECIES"
echo "[*] Reading list: $(basename "$INPUT_LIST")"
echo "[*] Output directory: $SPE_OUTPUT_DIR"
echo "------------------------------------------------"

# 3. Inference function
do_inference() {
    local full_json_path=$1

    # Extract ID (accepts *_data.json or *.json)
    local filename=$(basename "$full_json_path")
    local pid="${filename%_data.json}"
    pid="${pid%.json}"

    # Preprocessing: force the data-JSON version field (v4 -> v3),
    # which this pinned run_alphafold.py expects
    sed -i 's/"version": 4/"version": 3/g' "$full_json_path"

    echo "[RUN] [$(date +%H:%M:%S)] inferring: $pid"

    python "$RUN_AF_PY" \
        --json_path="$full_json_path" \
        --model_dir="$MODEL_PATH" \
        --output_dir="$SPE_OUTPUT_DIR" \
        --run_data_pipeline=false \
        --db_dir="$DB_PATH" \
        --flash_attention_implementation=xla \
        --run_inference=true > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "[SUCCESS] $pid 3D structure generated."
    else
        echo "[FAILED] $pid inference failed."
    fi
}

# 4. Loop over the list
count=0
while IFS= read -r line_path; do
    [ -z "$line_path" ] && continue

    do_inference "$line_path" &

    ((count++))

    if (( count % CONCURRENCY == 0 )); then
        wait
        echo "[INFO] Batch done, submitted so far: $count"
    fi
done < "$INPUT_LIST"

wait
echo "------------------------------------------------"
echo "[DONE] All jobs in this list finished."
