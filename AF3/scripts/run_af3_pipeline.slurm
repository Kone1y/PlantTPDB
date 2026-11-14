#!/bin/bash
#SBATCH -J alphafold3_job
#SBATCH -N 1
#SBATCH --ntasks-per-node=32
##SBATCH --gres=dcu:1
#SBATCH -p REPLACE_WITH_YOUR_PARTITION

# ================= user configuration =================
# Run mode: 0=full pipeline, 1=data pipeline (MSA) only, 2=inference only
RUN_MODE=1

# Input directory (per-species *.json protein inputs)
INPUT_PATH=$1

# Model directory (AF3 model parameters af3.bin; see AF3/README.md)
MODEL_PATH="./models"

# Output directory
OUTPUT_PATH=$2
# ======================================================

source "$(dirname "$0")/af3_env.sh"

# One-call pipeline
# Argument order: input_dir output_dir model_dir run_mode
run_af3_pipeline "$INPUT_PATH" "$OUTPUT_PATH" "$MODEL_PATH" "$RUN_MODE"
if [ "$?" -eq 0  ];then
    echo "All jobs finished."
fi
