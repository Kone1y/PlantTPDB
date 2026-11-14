#!/bin/bash
###############################################################################
# phylogenetic_analysis.sh
#
# Phylogenetic Analysis Pipeline for PTDB
# Workflow: Protein sequence extraction → MAFFT alignment → FastTree ML tree
#
# Usage:
#   bash phylogenetic_analysis.sh -i <input.fasta> -t <jtt|wag|lg> -r <cat|gamma> -o <output_dir>
#
# Parameters:
#   -i  Input FASTA file containing protein sequences (required)
#   -t  Substitution model: jtt (default), wag, or lg
#   -r  Rate heterogeneity: cat (default) or gamma
#   -o  Output directory (default: ./phylogenetic_output)
#
# Dependencies:
#   - MAFFT     (https://mafft.cbrc.jp/alignment/software/)
#   - FastTree  (http://www.microbesonline.org/fasttree/)
###############################################################################

set -euo pipefail

# --- Default parameters ---
MODEL_TYPE="jtt"
MODEL_RATE="cat"
OUTPUT_DIR="./phylogenetic_output"
INPUT_FASTA=""

# --- Parse arguments ---
usage() {
    echo "Usage: $0 -i <input.fasta> [-t jtt|wag|lg] [-r cat|gamma] [-o <output_dir>]"
    echo ""
    echo "Options:"
    echo "  -i  Input FASTA file (required)"
    echo "  -t  Substitution model: jtt (default), wag, lg"
    echo "  -r  Rate heterogeneity: cat (default), gamma"
    echo "  -o  Output directory (default: ./phylogenetic_output)"
    exit 1
}

while getopts "i:t:r:o:" opt; do
    case $opt in
        i) INPUT_FASTA="$OPTARG" ;;
        t) MODEL_TYPE="$OPTARG" ;;
        r) MODEL_RATE="$OPTARG" ;;
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

if [[ ! "$MODEL_TYPE" =~ ^(jtt|wag|lg)$ ]]; then
    echo "Error: Invalid substitution model '$MODEL_TYPE'. Must be jtt, wag, or lg."
    exit 1
fi

if [[ ! "$MODEL_RATE" =~ ^(cat|gamma)$ ]]; then
    echo "Error: Invalid rate heterogeneity '$MODEL_RATE'. Must be cat or gamma."
    exit 1
fi

# --- Create output directory ---
mkdir -p "$OUTPUT_DIR"

WORKDIR="${OUTPUT_DIR}/work_$(date +%s)"
mkdir -p "$WORKDIR"

echo "========================================"
echo " PTDB Phylogenetic Analysis Pipeline"
echo "========================================"
echo "Input:     $INPUT_FASTA"
echo "Model:     $MODEL_TYPE"
echo "Rate:      $MODEL_RATE"
echo "Work dir:  $WORKDIR"
echo "========================================"

# --- Step 1: Copy input FASTA ---
cp "$INPUT_FASTA" "$WORKDIR/input.fa"
SEQ_COUNT=$(grep -c "^>" "$WORKDIR/input.fa")
echo "[Step 1] Input FASTA: $SEQ_COUNT sequences loaded."

# --- Step 2: MAFFT alignment ---
echo "[Step 2] Running MAFFT alignment (--quiet --maxiterate 1000)..."
mafft --quiet --maxiterate 1000 "$WORKDIR/input.fa" > "$WORKDIR/input_mafft.fa" 2>"$WORKDIR/mafft.log"

if [[ $? -ne 0 ]]; then
    echo "Error: MAFFT alignment failed. See $WORKDIR/mafft.log"
    exit 1
fi

ALIGNED_LEN=$(grep -v "^>" "$WORKDIR/input_mafft.fa" | tr -d '\n' | wc -c)
echo "  Alignment length: $ALIGNED_LEN bp"

# --- Step 3: FastTree ML tree inference ---
echo "[Step 3] Running FastTree (model=$MODEL_TYPE, rate=$MODEL_RATE)..."

FASTTREE_OPTS=""
if [[ "$MODEL_TYPE" == "wag" ]]; then
    FASTTREE_OPTS="-wag"
elif [[ "$MODEL_TYPE" == "lg" ]]; then
    FASTTREE_OPTS="-lg"
fi
# jtt: no extra flag (FastTree default)

if [[ "$MODEL_RATE" == "gamma" ]]; then
    FASTTREE_OPTS="$FASTTREE_OPTS -gamma"
fi
# cat: no extra flag (FastTree default)

FastTree $FASTTREE_OPTS "$WORKDIR/input_mafft.fa" > "$WORKDIR/tree.nwk" 2>"$WORKDIR/fasttree.log"

if [[ $? -ne 0 ]]; then
    echo "Error: FastTree failed. See $WORKDIR/fasttree.log"
    exit 1
fi

TREE_SIZE=$(wc -c < "$WORKDIR/tree.nwk")
echo "  Tree file size: $TREE_SIZE bytes"

# --- Step 4: Copy results to output directory ---
cp "$WORKDIR/input_mafft.fa" "$OUTPUT_DIR/aligned_sequences.fa"
cp "$WORKDIR/tree.nwk"       "$OUTPUT_DIR/phylogenetic_tree.nwk"

echo ""
echo "========================================"
echo " Pipeline completed successfully!"
echo "========================================"
echo "Results:"
echo "  Aligned sequences: $OUTPUT_DIR/aligned_sequences.fa"
echo "  Phylogenetic tree:   $OUTPUT_DIR/phylogenetic_tree.nwk"
echo "  Working directory:   $WORKDIR"
echo "========================================"
