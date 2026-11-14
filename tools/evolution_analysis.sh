#!/bin/bash
###############################################################################
# evolution_analysis.sh
#
# Evolution / Sequence Identity Analysis Pipeline for PTDB
# Workflow: Protein sequence extraction → MAFFT alignment → CLUSTAL output
#
# This script replicates the PTDB Evolution tool's backend pipeline:
#   1. Accepts a multi-sequence FASTA file as input
#   2. Runs MAFFT for multiple sequence alignment
#   3. Outputs CLUSTAL-format alignment and parsed sequence data
#
# Usage:
#   bash evolution_analysis.sh -i <input.fasta> -o <output_dir>
#
# Parameters:
#   -i  Input FASTA file containing protein sequences (required)
#   -o  Output directory (default: ./evolution_output)
#
# Dependencies:
#   - MAFFT     (https://mafft.cbrc.jp/alignment/software/)
###############################################################################

set -euo pipefail

# --- Default parameters ---
OUTPUT_DIR="./evolution_output"
INPUT_FASTA=""

# --- Parse arguments ---
usage() {
    echo "Usage: $0 -i <input.fasta> [-o <output_dir>]"
    echo ""
    echo "Options:"
    echo "  -i  Input FASTA file (required)"
    echo "  -o  Output directory (default: ./evolution_output)"
    exit 1
}

while getopts "i:o:" opt; do
    case $opt in
        i) INPUT_FASTA="$OPTARG" ;;
        o) OUTPUT_DIR="$OPTARG" ;;
        *) usage ;;
    esac
done

if [[ -z "$INPUT_FASTA" ]]; then
    echo "Error: Input FASTA file is required (-i)."
    usage
fi

if [[ ! -f "$INPUT_FASTA" ]]; then
    echo "Error: Input file '$INPUT_FASTA' not found."
    exit 1
fi

# --- Create output directory ---
mkdir -p "$OUTPUT_DIR"

WORKDIR="${OUTPUT_DIR}/work_$(date +%s)"
mkdir -p "$WORKDIR"

echo "========================================"
echo " PTDB Evolution Analysis Pipeline"
echo "========================================"
echo "Input:     $INPUT_FASTA"
echo "Work dir:  $WORKDIR"
echo "========================================"

# --- Step 1: Copy input FASTA ---
cp "$INPUT_FASTA" "$WORKDIR/input.fasta"
SEQ_COUNT=$(grep -c "^>" "$WORKDIR/input.fasta")
echo "[Step 1] Input FASTA: $SEQ_COUNT sequences loaded."

# --- Step 2: MAFFT alignment (CLUSTAL output) ---
echo "[Step 2] Running MAFFT alignment (--auto --clustalout)..."
mafft --auto --clustalout "$WORKDIR/input.fasta" > "$WORKDIR/alignment.clustal" 2>"$WORKDIR/mafft.log"

if [[ $? -ne 0 ]]; then
    echo "Error: MAFFT alignment failed. See $WORKDIR/mafft.log"
    exit 1
fi

echo "  CLUSTAL alignment generated."

# --- Step 3: Parse CLUSTAL output into individual sequences ---
echo "[Step 3] Parsing CLUSTAL alignment..."

PARSED_DIR="$WORKDIR/parsed_sequences"
mkdir -p "$PARSED_DIR"

# Parse sequences from CLUSTAL format: skip header/conservation lines,
# concatenate multi-line blocks per sequence ID
awk '
    /^[A-Za-z0-9_.-]/ && !/^CLUSTAL/ {
        match($0, /^([A-Za-z0-9_.|-]+)\s+(.*)/, m)
        if (m[1] != "" && m[2] != "") {
            seq[m[1]] = seq[m[1]] m[2]
            if (!(m[1] in seen)) {
                order[++n] = m[1]
                seen[m[1]] = 1
            }
        }
    }
    END {
        for (i = 1; i <= n; i++) {
            id = order[i]
            s = seq[id]
            gsub(/[^A-Za-z\-\.]/, "", s)
            print ">" id
            print s
        }
    }
' "$WORKDIR/alignment.clustal" > "$WORKDIR/parsed_all.fasta"

PARSED_COUNT=$(grep -c "^>" "$WORKDIR/parsed_all.fasta")
echo "  Parsed $PARSED_COUNT sequences."

# --- Step 4: Copy results to output directory ---
cp "$WORKDIR/alignment.clustal"  "$OUTPUT_DIR/alignment.clustal"
cp "$WORKDIR/parsed_all.fasta"  "$OUTPUT_DIR/parsed_sequences.fasta"

echo ""
echo "========================================"
echo " Pipeline completed successfully!"
echo "========================================"
echo "Results:"
echo "  CLUSTAL alignment:   $OUTPUT_DIR/alignment.clustal"
echo "  Parsed sequences:    $OUTPUT_DIR/parsed_sequences.fasta"
echo "  Working directory:   $WORKDIR"
echo "========================================"
