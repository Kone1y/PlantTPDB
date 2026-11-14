#!/bin/bash

# =================================================================
# AF3 pipeline environment
#
# All installation paths are read from environment variables with
# placeholder defaults. Export the variables below (or edit the
# defaults) to match your installation before running.
# =================================================================

# --- 1. Environment and paths (EDIT THESE) -------------------------------
export AF3_ROOT="${AF3_ROOT:-/path/to/af3_singularity_installation}"
export AF3_IMAGE="${AF3_IMAGE:-${AF3_ROOT}/app/ai4s-af3_dtk25042.sif}"

# Load Singularity (site module system; adapt or comment out)
# module load singularity/3.7.3

# Genetic database paths (Stage 1 / MSA only)
export DB_DIRS="${DB_DIRS:-/path/to/alphafold3/database}"
export MMSEQS_DB_DIRS="${MMSEQS_DB_DIRS:-/path/to/mmseqsDB}"
export BFD_PATH="${BFD_PATH:-/path/to/bfd-first_non_consensus_sequences.fasta@64}"
export MGY_PATH="${MGY_PATH:-/path/to/mgy_clusters_2022_05.fa@512}"
export UNIPROT_PATH="${UNIPROT_PATH:-/path/to/uniprot_cluster_annot_2021_04.fa@256}"
export UNIREF90_PATH="${UNIREF90_PATH:-/path/to/uniref90_2022_05.fa@128}"

# Container initialization (DCU/ROCm-adapted image; adapt to your stack,
# e.g. empty string for the upstream NVIDIA Docker image)
CONTAINER_INIT="${CONTAINER_INIT:-source /opt/dtk/env.sh && source /opt/dtk/.hyhal/hydm/hydmi_env.sh && source /opt/dtk/cuda/env.sh}"

# Bind mounts: bind the AF3 installation into the container by default; add
# any site-specific binds (e.g. host GPU-stack runtime libraries) via the
# BIND_OPTS environment variable.
BIND_OPTS="${BIND_OPTS:--B ${AF3_ROOT}}"

# =================================================================
# 2. Core driver function
# =================================================================
# Usage: run_af3_pipeline "input_dir" "output_dir" "model_dir" "mode(0/1/2)"
run_af3_pipeline() {
    local INPUT_DIR="$1"
    local OUTPUT_DIR="$2"
    local MODEL_DIR="$3"
    local MODE="$4"  # 0=full pipeline, 1=data pipeline (MSA) only, 2=inference only

    if [ ! -d "$INPUT_DIR" ]; then echo "Error: Input dir $INPUT_DIR not found"; return 1; fi

    find "${INPUT_DIR}" -type f -name "*.json" -print0 | while IFS= read -r -d '' FILE; do
        FILENAME=$(basename "$FILE" .json)
        echo "=================================================="
        echo "Processing: $FILENAME | Mode: $MODE"
        echo "=================================================="

        # --- Stage 1 command (data pipeline / MSA) ---
        CMD_STAGE1="python run_alphafold.py \
            --json_path='${FILE}' \
            --model_dir='${MODEL_DIR}' \
            --output_dir='${OUTPUT_DIR}' \
            --run_data_pipeline=true \
            --run_inference=false \
            --flash_attention_implementation=xla \
            --db_dir='${DB_DIRS}' \
            --mmseqs_db_dir='${MMSEQS_DB_DIRS}' \
            --use_mmseqs=false \
            --use_mmseqs_gpu=false \
            --small_bfd_database_path='${BFD_PATH}' \
            --small_bfd_z_value=65928866 \
            --mgnify_database_path='${MGY_PATH}' \
            --mgnify_z_value=623796864 \
            --uniprot_cluster_annot_database_path='${UNIPROT_PATH}' \
            --uniprot_cluster_annot_z_value=225619586 \
            --uniref90_database_path='${UNIREF90_PATH}' \
            --uniref90_z_value=153742194 \
            --jackhmmer_n_cpu=2 \
            --jackhmmer_max_parallel_shards=2 \
            --nhmmer_n_cpu=2 \
            --nhmmer_max_parallel_shards=2"

        # --- Stage 2 command (inference) ---
        CMD_STAGE2="python run_alphafold.py \
            --json_path='${FILE}' \
            --model_dir='${MODEL_DIR}' \
            --output_dir='${OUTPUT_DIR}' \
            --run_data_pipeline=false \
            --run_inference=true \
            --flash_attention_implementation=triton"

        CMD_STAGE01="python run_alphafold.py \
            --json_path='${FILE}' \
            --model_dir='${MODEL_DIR}' \
            --output_dir='${OUTPUT_DIR}_s1' \
            --run_data_pipeline=true \
            --run_inference=false \
            --flash_attention_implementation=xla \
            --db_dir='${DB_DIRS}' \
            --mmseqs_db_dir='${MMSEQS_DB_DIRS}' \
            --use_mmseqs=false \
            --use_mmseqs_gpu=false \
            --small_bfd_database_path='${BFD_PATH}' \
            --small_bfd_z_value=65928866 \
            --mgnify_database_path='${MGY_PATH}' \
            --mgnify_z_value=623796864 \
            --uniprot_cluster_annot_database_path='${UNIPROT_PATH}' \
            --uniprot_cluster_annot_z_value=225619586 \
            --uniref90_database_path='${UNIREF90_PATH}' \
            --uniref90_z_value=153742194 \
            --jackhmmer_n_cpu=2 \
            --jackhmmer_max_parallel_shards=2 \
            --nhmmer_n_cpu=2 \
            --nhmmer_max_parallel_shards=2"

        CMD_STAGE02="python run_alphafold.py \
            --json_path='${OUTPUT_DIR}_s1'/${FILENAME}/${FILENAME}_data.json  \
            --model_dir='${MODEL_DIR}' \
            --output_dir='${OUTPUT_DIR}_s2' \
            --run_data_pipeline=false \
            --run_inference=true \
            --flash_attention_implementation=triton"

        # --- Execution ---
        # GPU memory control and device binding
        EXPORT_VARS="export XLA_CLIENT_MEM_FRACTION=0.95"

        if [ "$MODE" -eq 1 ] ; then
            mkdir -p "${OUTPUT_DIR}"
            echo "--> Running Stage 1 (MSA)..."
            singularity exec ${BIND_OPTS} "${AF3_IMAGE}" bash -c \
                "${CONTAINER_INIT} && ${EXPORT_VARS} && { time ${CMD_STAGE1} > '${OUTPUT_DIR}/pipeline_${FILENAME}_s1.log' 2>&1 ; } 2>> '${OUTPUT_DIR}/time_${FILENAME}_s1.log'"
        fi

        if [ "$MODE" -eq 2 ] ; then
            mkdir -p "${OUTPUT_DIR}"
            echo "--> Running Stage 2 (Inference)..."
            singularity exec ${BIND_OPTS} "${AF3_IMAGE}" bash -c \
                "${CONTAINER_INIT} && ${EXPORT_VARS} && { time ${CMD_STAGE2} > '${OUTPUT_DIR}/pipeline_${FILENAME}_s2.log' 2>&1 ; } 2>> '${OUTPUT_DIR}/time_${FILENAME}_s2.log'"
        fi

        if [ "$MODE" -eq 0 ]; then
            mkdir -p "${OUTPUT_DIR}_s1" "${OUTPUT_DIR}_s2"
            echo "--> Running Stage 1 (MSA)..."
            singularity exec ${BIND_OPTS} "${AF3_IMAGE}" bash -c \
                "${CONTAINER_INIT} && ${EXPORT_VARS} && { time ${CMD_STAGE01} > '${OUTPUT_DIR}_s1/pipeline_${FILENAME}_s1.log' 2>&1 ; } 2>> '${OUTPUT_DIR}_s1/time_${FILENAME}_s1.log'"

            echo "--> Running Stage 2 (Inference)..."
            singularity exec ${BIND_OPTS} "${AF3_IMAGE}" bash -c \
                "${CONTAINER_INIT} && ${EXPORT_VARS} && { time ${CMD_STAGE02} > '${OUTPUT_DIR}_s2/pipeline_${FILENAME}_s2.log' 2>&1 ; } 2>> '${OUTPUT_DIR}_s2/time_${FILENAME}_s2.log'"
        fi

    done
}
