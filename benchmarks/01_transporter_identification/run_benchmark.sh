#!/bin/bash
# ============================================================================
# Benchmark evaluation of the transporter identification pipeline
#
# Runs the full evaluation into eval_output/ for comparison with the
# archived reference outputs in results/ (strategy k=2 is the published
# pipeline). TCDB and Pfam-A are fetched as published at run time; metrics
# that depend on the T/P criteria may differ if those live databases have
# changed — see README.md (reproducibility note).
#
# Requirements: python3, DIAMOND >= 2.0, HMMER 3.x (hmmscan/hmmpress),
#               matplotlib (optional, for PR/ROC figures)
#
# Usage:  bash run_benchmark.sh          (THREADS=10 by default)
#         THREADS=20 bash run_benchmark.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

THREADS=${THREADS:-10}
DB=data/reference_db

# ----------------------------------------------------------------------------
# Step 0 (automatic, first run only): build the search databases.
#
# Seed reference libraries are shipped as deduplicated FASTA (entries sharing
# >=95% identity at >=90% coverage with a positive benchmark sequence removed;
# removed-entry lists in dedup_provenance/). They are converted to DIAMOND
# databases here. TCDB and Pfam-A are NOT redistributed (license); they are
# downloaded as published.
# ----------------------------------------------------------------------------
if [ ! -f "$DB/9_all_spe_add_16osa_v1_dedup.dmnd" ]; then
    diamond makedb --in "$DB/9_all_spe_add_16osa_v1_dedup.fa" -d "$DB/9_all_spe_add_16osa_v1_dedup"
fi

if [ ! -f "$DB/ath_dedup.dmnd" ]; then
    diamond makedb --in "$DB/ath_dedup.fa" -d "$DB/ath_dedup"
fi

if [ ! -f "$DB/tcdb.dmnd" ]; then
    curl -sL "https://www.tcdb.org/public/tcdb" -o "$DB/tcdb_all.fasta"
    diamond makedb --in "$DB/tcdb_all.fasta" -d "$DB/tcdb"
fi

if [ ! -f "$DB/Pfam-A.hmm.h3i" ]; then
    curl -sL "https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.gz" -o "$DB/Pfam-A.hmm.gz"
    gunzip -f "$DB/Pfam-A.hmm.gz"
    hmmpress -f "$DB/Pfam-A.hmm"
fi

# ----------------------------------------------------------------------------
# Step 1: run the benchmark evaluation (all three strategies)
#
# P criterion: standalone transport-domain check — query domains intersect the
# shipped transporter_pfam.tsv (1316 domains; domain i-Evalue < 1e-5), with no
# reference to the TC hit and its family.
# ----------------------------------------------------------------------------
python3 code/evaluate_identification_strategies.py \
    --evalue_strict_k1 1e-20 \
    --evalue_strict_k2 1e-20 \
    --evalue_strict_k3 1e-20 \
    --include_neg_family \
    --positive_fa data/benchmark_set/benchmark_positive.fasta \
    --negative_fa data/benchmark_set/benchmark_negative.fasta \
    --strategy all \
    --db_tc "$DB/tcdb.dmnd" \
    --db_seed "$DB/9_all_spe_add_16osa_v1_dedup.dmnd" \
    --db_seed_ath "$DB/ath_dedup.dmnd" \
    --db_pfam "$DB/Pfam-A.hmm" \
    --pfam_transport "$DB/transporter_pfam.tsv" \
    --evalue_pfam 1e-5 \
    --seed_info data/seed_info/candi_seed_info_v1_ara.txt \
    --seed_info data/seed_info/candi_seed_info_v2_ath.txt \
    --output_dir ./eval_output \
    --threads "$THREADS"

# ----------------------------------------------------------------------------
# Step 2: verify against the reference outputs
# ----------------------------------------------------------------------------
echo ""
echo "Done. Compare your results with results/:"
echo "  diff eval_output/eval_metrics_summary.tsv results/eval_metrics_summary.tsv"
echo "Note: TCDB/Pfam-A were downloaded as published at run time; T/P-dependent"
echo "numbers may differ if the live databases changed (see README, reproducibility note)."
