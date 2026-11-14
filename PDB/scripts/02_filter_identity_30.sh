#!/bin/bash
# 02_filter_identity_30.sh — second-pass filter on the DIAMOND hits: keep sequence identity >= 30%
#
# In : 05.733_pep_blast_diamond/<species>.blast.out  (DIAMOND outfmt 6; column 3 = pident)
# Out: 05.733_pep_blast_diamond_30_identity/<species>.blast.out.30_identity
#
# Usage: bash 02_filter_identity_30.sh [IN_DIR] [OUT_DIR]   (defaults = as-run names)
set -euo pipefail

IN_DIR="${1:-05.733_pep_blast_diamond}"
OUT_DIR="${2:-05.733_pep_blast_diamond_30_identity}"

shopt -s nullglob
files=("$IN_DIR"/*.blast.out)
shopt -u nullglob
if [[ ${#files[@]} -eq 0 ]]; then
    echo "[ERROR] no *.blast.out files found in $IN_DIR" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
for f in "${files[@]}"; do
    awk '$3 >= 30' "$f" > "$OUT_DIR/$(basename "$f").30_identity"
done

echo "[OK] identity>=30% filter done: ${#files[@]} species -> $OUT_DIR"
