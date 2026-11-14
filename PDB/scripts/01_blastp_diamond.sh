#!/bin/bash

# Stage 1: per-species DIAMOND blastp of the 733-plant proteomes against the
# PDB chain-sequence library (submitted via LSF bsub; adapt the queue/module
# lines to your site, or run the diamond command directly).

# 1. Create working directories
mkdir -p logs
mkdir -p 05.733_pep_blast_diamond

# 2. DIAMOND database (built once from the shipped FASTA)
DB="data/pdb_chain_sequences.dmnd"
if [ ! -f "$DB" ]; then
    diamond makedb --in data/pdb_chain_sequences.fasta --db data/pdb_chain_sequences
fi

# 3. Loop over all proteome FASTA files in 04.733_pep/ (not bundled; see README)
for query_file in 04.733_pep/*.fasta; do

    # Species name (strip path and .fasta suffix)
    species=$(basename "$query_file" .fasta)

    output_file="05.733_pep_blast_diamond/${species}.blast.out"

    # Submit one LSF job per species
    bsub <<EOT
#BSUB -J blast_${species}
#BSUB -n 10
#BSUB -R "span[hosts=1]"
#BSUB -o logs/${species}.out
#BSUB -e logs/${species}.err
#BSUB -q normal

module load diamond/0.9.27

diamond blastp \
       --query ${query_file} \
       --db ${DB} \
       --out ${output_file} \
       --evalue 1e-10 \
       --query-cover 60 \
       --threads 10 \
       --max-target-seqs 1 \
       --more-sensitive \
       --outfmt 6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qcovhsp

EOT

    echo "submitted: ${species}"

done
