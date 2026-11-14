#!/bin/bash

# Stage-1 (MSA) batch driver.
# Submits one SLURM job per species input directory.
# Paths are environment-overridable; the defaults below are placeholders.

RERUN_INPUT_ROOT="${RERUN_INPUT_ROOT:-/path/to/input_json_per_species}"
RES_V2_ROOT="${RES_V2_ROOT:-/path/to/msa_output_root}"
LOG_DIR="${LOG_DIR:-logs_MSA}"

mkdir -p "$LOG_DIR"

for spe_dir in "${RERUN_INPUT_ROOT}"/*; do
    if [[ ! -d "$spe_dir" ]]; then continue; fi

    species=$(basename "$spe_dir")

    # Count the per-species JSON jobs
    shopt -s nullglob
    files=("${spe_dir}"/*.json)
    if [[ ${#files[@]} -eq 0 ]]; then
        echo "[SKIP] ${species}: nothing to run"
        continue
    fi
    shopt -u nullglob

    jobname="AF3_v2_${species}"
    out_dir="${RES_V2_ROOT}/${species}"

    # Submit: $1 = input dir, $2 = output dir
    sbatch -o "${LOG_DIR}/${species}.o" -e "${LOG_DIR}/${species}.e" \
           -J "${jobname}" \
           run_af3_pipeline.slurm "$spe_dir" "$out_dir"

    echo "[SUBMIT] ${species} | jobs: ${#files[@]}"
done
