#!/usr/bin/env bash
# In-image entrypoint: validate SPECIES, run the prediction pipeline, clean TMHMM scratch.
set -euo pipefail

SPECIES="${SPECIES:?SPECIES env var required, e.g. -e SPECIES=Oryza_sativa}"
# SPECIES becomes a filename component; reject anything unsafe.
[[ "$SPECIES" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "Bad SPECIES '$SPECIES' (allowed: A-Z a-z 0-9 _ . -)" >&2; exit 1; }

[ -f /in/input.fa ] || { echo "Mount your FASTA to /in/input.fa" >&2; exit 1; }
mkdir -p /out

/opt/online_pred/run_transporter_prediction_no_deeploc.sh /in/input.fa "$SPECIES" /out

# TMHMM wrapper leaves a TMHMM_<pid>/ scratch dir in cwd (/out); remove it.
rm -rf /out/TMHMM_*
