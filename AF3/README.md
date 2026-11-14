# AlphaFold 3 Structure-Prediction Pipeline (MSA + Inference Stages)

Two-stage AlphaFold 3 (v3.0.1) pipeline used to predict 3D structures of
PlantTPDB transporter proteins. Stage 1 builds the MSAs (AF3 data pipeline)
on a CPU partition; stage 2 runs AF3 inference on the stage-1 data JSONs.
The two stages ran on different machines; the stage each script belongs to
is documented below and in the script headers.

The local predictions used a DCU/ROCm-adapted implementation based on
AlphaFold 3 v3.0.1. The archived wrapper scripts include
implementation-specific command-line options and are not directly
compatible with the unmodified upstream v3.0.1 release. The adapted
runtime image is not distributed with this repository. These scripts
document the workflow used; rerunning them requires the corresponding
adapted implementation and computing environment.

## Layout

- `scripts/` — workflow scripts (run from this directory):
  - `af3_env.sh` — environment + `run_af3_pipeline()` driver: runs the AF3
    Singularity container (DCU/ROCm-adapted image) with the
    jackhmmer/nhmmer database paths (BFD, MGNify, UniProt, UniRef90 +
    MMSEQS databases); supports MODE 0 = full pipeline, 1 = data pipeline
    (MSA) only, 2 = inference only
  - `run_af3_pipeline.slurm` — SLURM entry script (RUN_MODE selectable,
    sources `af3_env.sh`)
  - `submit_msa_jobs.sh` — stage-1 batch driver: submits one SLURM job per
    species input directory (`<species>/*.json` input protein JSONs)
  - `run_inference.sh` — stage-2 batch inference driver: reads a
    per-species list of stage-1 `*_data.json` files, runs AF3 inference
    with bounded concurrency (2), one output directory per species.
    Includes a `sed` rewrite of `"version": 4` to `"version": 3` in the
    data JSONs, which this pinned `run_alphafold.py` expects
The upstream AlphaFold 3 v3.0.1 source tree (CC BY-NC-SA 4.0) is **not
bundled**; download it from https://github.com/google-deepmind/alphafold3
(tag v3.0.1) and place it at `AF3/alphafold3-v3.0.1/`. Model parameters
(`af3.bin`) are likewise **not** redistributed (see below).

Dependencies: Bash, SLURM + Singularity >= 3.7 (stage 1), a Python
environment with the upstream AF3 source tree's `requirements.txt` (stage
2); no additional Python packages are required by the wrapper scripts
themselves.

## Not included (license or size)

| Item | How to obtain |
|---|---|
| AF3 model parameters `af3.bin` | download from https://github.com/google-deepmind/alphafold3 after accepting the Model Parameters Terms of Use; place at `alphafold3-v3.0.1/models/af3.bin` (sha256 used in our runs: `c96e76f0…976`, see the upstream OBTAIN_WEIGHTS.md) |
| Genetic databases (BFD, MGNify, UniProt, UniRef90, MMSEQS DBs) | upstream `fetch_databases.sh` in the AF3 repo |
| DCU-adapted Singularity image (`ai4s-af3_dtk25042.sif`) | site-specific; not redistributed |

## Usage

```bash
# 0) obtain model parameters (see above) and genetic databases
# 1) stage 1 — per-species MSA generation (as run, on a SLURM cluster):
cd scripts
bash submit_msa_jobs.sh            # submits run_af3_pipeline.slurm per species
#    (or directly: sbatch run_af3_pipeline.slurm <input_json_dir> <output_dir>)
# 2) stage 2 — inference on the stage-1 data JSONs:
bash run_inference.sh <species> <list_of_data_json_paths>
```

## Run record

The scripts read their installation paths from environment variables
with placeholder defaults (export `AF3_ROOT` / `AF3_IMAGE` / `DB_DIRS` /
`MMSEQS_DB_DIRS` / `BFD_PATH` / `MGY_PATH` / `UNIPROT_PATH` /
`UNIREF90_PATH` for the MSA stage, and `MODEL_PATH` / `OUTPUT_ROOT` /
`DB_PATH` / `RUN_AF_PY` for inference). All run-alphafold flags,
database z-values, the version-3 data-JSON rewrite, and concurrency
settings are as used in the runs. Outputs per protein: ranked model CIF + per-seed/per-sample CIFs,
confidence JSONs and `scores.json` under the output directory.
