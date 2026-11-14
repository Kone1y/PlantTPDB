#!/bin/bash
#
# End-to-end script for transporter protein prediction (without DeepLoc2)
# Input: original protein FASTA file
# Output: predicted transporter information files
#

set -e  # Exit on error

# ============== Configuration ==============
WORK_DIR="${PTD_WORK_DIR:-/opt/online_pred}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH01_DATA="$SCRIPT_DIR/../../benchmarks/01_transporter_identification/data"

# Database paths
DMND_TC="${PTD_DMND_TC:-$WORK_DIR/tcdb.dmnd}"
DMND_SEED="${PTD_DMND_SEED:-$WORK_DIR/9_all_spe_add_16osa_v1.pep.dmnd}"
DMND_ATH="${PTD_DMND_ATH:-$WORK_DIR/ath.dmnd}"
PFAM_DB="${PTD_PFAM_DB:-$WORK_DIR/Pfam-A.hmm}"

# Tool paths
TMHMM_DIR="${PTD_TMHMM_DIR:-/opt/tmhmm-2.0d}"

# Number of threads
THREADS="${PTD_THREADS:-10}"

# ============== Functions ==============

usage() {
    echo "Usage: $0 <input.fa> <species_name> [output_dir]"
    echo ""
    echo "Arguments:"
    echo "  input.fa      Input protein FASTA file"
    echo "  species_name  Species name (e.g., Oryza_sativa)"
    echo "  output_dir    Output directory (default: current directory)"
    echo ""
    echo "Example:"
    echo "  $0 proteins.fa Oryza_sativa ./output"
    exit 1
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ============== Main Script ==============

# Check arguments
if [ $# -lt 2 ]; then
    usage
fi

INPUT_FA="$1"
SPE="$2"
OUTPUT_DIR="${3:-.}"

# Convert to absolute path
INPUT_FA=$(realpath "$INPUT_FA")
OUTPUT_DIR=$(realpath "$OUTPUT_DIR")

# Check input file
if [ ! -f "$INPUT_FA" ]; then
    echo "Error: Input file '$INPUT_FA' not found!"
    exit 1
fi

log "Starting transporter protein prediction for species: $SPE"
log "Input file: $INPUT_FA"
log "Output directory: $OUTPUT_DIR"

# Create output directory
mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR"

# Create the database working directory (Step 0 writes downloaded/built
# databases here; it is configured independently of the script directory)
mkdir -p "$WORK_DIR"

# Create subdirectories
mkdir -p TC_res Seed_res Seed_ath_res Pfam_res TMHMM2

# ============== Step 0 (automatic, first run only): fetch and build missing databases ==============
# TCDB and Pfam-A are not redistributed (license) and are downloaded as published.
# Seed-library sources default to the curated deduplicated FASTA shipped in
# benchmarks/01_transporter_identification/data/reference_db/.
if [ -z "${PTD_DMND_TC:-}" ] && [ ! -f "$WORK_DIR/tcdb.dmnd" ]; then
    log "Step 0: downloading TCDB sequences and building tcdb.dmnd..."
    curl -sL "https://www.tcdb.org/public/tcdb" -o "$WORK_DIR/tcdb_all.fasta"
    diamond makedb --in "$WORK_DIR/tcdb_all.fasta" -d "$WORK_DIR/tcdb"
fi
if [ -z "${PTD_DMND_SEED:-}" ] && [ ! -f "$WORK_DIR/9_all_spe_add_16osa_v1.pep.dmnd" ]; then
    SEED_FA="${PTD_SEED_FA:-$BENCH01_DATA/reference_db/9_all_spe_add_16osa_v1_dedup.fa}"
    if [ -f "$SEED_FA" ]; then
        log "Step 0: building 9_all_spe_add_16osa_v1.pep.dmnd from seed FASTA..."
        diamond makedb --in "$SEED_FA" -d "$WORK_DIR/9_all_spe_add_16osa_v1.pep"
    fi
fi
if [ -z "${PTD_DMND_ATH:-}" ] && [ ! -f "$WORK_DIR/ath.dmnd" ]; then
    ATH_FA="${PTD_ATH_FA:-$BENCH01_DATA/reference_db/ath_dedup.fa}"
    if [ -f "$ATH_FA" ]; then
        log "Step 0: building ath.dmnd from seed FASTA..."
        diamond makedb --in "$ATH_FA" -d "$WORK_DIR/ath"
    fi
fi
if [ -z "${PTD_PFAM_DB:-}" ] && [ ! -f "$WORK_DIR/Pfam-A.hmm" ]; then
    log "Step 0: downloading Pfam-A and pressing..."
    curl -sL "https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.gz" -o "$WORK_DIR/Pfam-A.hmm.gz"
    gunzip -f "$WORK_DIR/Pfam-A.hmm.gz"
    hmmpress -f "$WORK_DIR/Pfam-A.hmm"
fi
# Seed annotation files: default to the copies shipped in benchmarks/01 when not next to the scripts
if [ -z "${PTD_SEED_INFO_ARA:-}" ] && [ ! -f "$WORK_DIR/candi_seed_info_v1_ara.txt" ] && [ -f "$BENCH01_DATA/seed_info/candi_seed_info_v1_ara.txt" ]; then
    export PTD_SEED_INFO_ARA="$BENCH01_DATA/seed_info/candi_seed_info_v1_ara.txt"
fi
if [ -z "${PTD_SEED_INFO_ATH:-}" ] && [ ! -f "$WORK_DIR/candi_seed_info_v2_ath.txt" ] && [ -f "$BENCH01_DATA/seed_info/candi_seed_info_v2_ath.txt" ]; then
    export PTD_SEED_INFO_ATH="$BENCH01_DATA/seed_info/candi_seed_info_v2_ath.txt"
fi

# ============== Step 1: Diamond BLAST against TC database ==============
log "Step 1/5: Running Diamond BLAST against TC database..."
diamond blastp \
    -d "$DMND_TC" \
    -q "$INPUT_FA" \
    -o "TC_res/${SPE}_diamond_TC.txt" \
    --evalue 1e-10 \
    --outfmt 6 \
    --threads "$THREADS" \
    --max-target-seqs 5 \
    --more-sensitive

# ============== Step 2: Diamond BLAST against Seed database ==============
log "Step 2/5: Running Diamond BLAST against Seed database..."
diamond blastp \
    -d "$DMND_SEED" \
    -q "$INPUT_FA" \
    -o "Seed_res/${SPE}_diamond_Seed_9spe.txt" \
    --evalue 1e-10 \
    --outfmt 6 \
    --threads "$THREADS" \
    --max-target-seqs 20 \
    --more-sensitive

# ============== Step 3: Diamond BLAST against Arabidopsis database ==============
log "Step 3/5: Running Diamond BLAST against Arabidopsis database..."
diamond blastp \
    -d "$DMND_ATH" \
    -q "$INPUT_FA" \
    -o "Seed_ath_res/${SPE}_diamond_ath11.txt" \
    --evalue 1e-10 \
    --outfmt 6 \
    --threads "$THREADS" \
    --max-target-seqs 5 \
    --more-sensitive

# ============== Step 4: Pfam domain prediction ==============
log "Step 4/5: Running Pfam domain prediction..."
hmmscan \
    --cpu "$THREADS" \
    --domtblout "Pfam_res/${SPE}.domtblout" \
    "$PFAM_DB" \
    "$INPUT_FA"

# ============== Step 5: TMHMM prediction ==============
log "Step 5/5: Running TMHMM prediction..."
# Extract protein IDs from FASTA
grep "^>" "$INPUT_FA" | sed 's/^>//; s/\s.*$//' > protein_ids.txt

# Run TMHMM
"${TMHMM_DIR}/bin/tmhmm" "$INPUT_FA" > "TMHMM2/${SPE}.tmhmm.txt" 2>/dev/null || true

# ============== Step 6: Generate transporter information summary ==============
log "Step 6/6: Generating transporter information summary..."

# Transporter Pfam-domain list for the P criterion (shipped with benchmark 01)
if [ -z "${PTD_PFAM_TRANSPORT:-}" ]; then
    if [ -f "$BENCH01_DATA/reference_db/transporter_pfam.tsv" ]; then
        export PTD_PFAM_TRANSPORT="$BENCH01_DATA/reference_db/transporter_pfam.tsv"
    elif [ -f "$WORK_DIR/transporter_pfam.tsv" ]; then
        export PTD_PFAM_TRANSPORT="$WORK_DIR/transporter_pfam.tsv"
    fi
fi

# Run information collection script
# Save current directory (we're in OUTPUT_DIR)
CURRENT_DIR=$(pwd)

# Run evidence integration (three partially overlapping, complementary evidence sources, two-of-three rule).
# Invoke by absolute path: the script lives in SCRIPT_DIR, not in WORK_DIR.
# It reads the search tables from the given output directory and writes
# <SPE>_information_base1.txt / <SPE>_pfam_info.txt into its working
# directory, so run it with the output directory as CWD.
(cd "$OUTPUT_DIR" && python3 "$SCRIPT_DIR/get_infomation_base1_pfam.py" "$SPE" "$OUTPUT_DIR")

log "======================================"
log "Transporter prediction completed!"
log "Results:"
log "  - Summary table: ${CURRENT_DIR}/${SPE}_information_base1.txt"
log "  - Pfam info: ${CURRENT_DIR}/${SPE}_pfam_info.txt"
log "  - TC results: ${CURRENT_DIR}/TC_res/${SPE}_diamond_TC.txt"
log "  - Seed results: ${CURRENT_DIR}/Seed_res/${SPE}_diamond_Seed_9spe.txt"
log "  - Pfam domains: ${CURRENT_DIR}/Pfam_res/${SPE}.domtblout"
log "  - TMHMM results: ${CURRENT_DIR}/TMHMM2/${SPE}.tmhmm.txt"
log "======================================"

# ============== Step 7: Package results into ZIP ==============
log "Step 7/7: Packaging results into results.zip..."

# Remove old zip if exists
rm -f "${CURRENT_DIR}/results.zip"

# Create zip archive with main results
zip -r "${CURRENT_DIR}/results.zip" \
    "${SPE}_information_base1.txt" \
    "${SPE}_pfam_info.txt" \
    "Pfam_res/" \
    "TMHMM2/" \
    "protein_ids.txt" \
    >/dev/null 2>&1

log "Results packaged: ${CURRENT_DIR}/results.zip"
