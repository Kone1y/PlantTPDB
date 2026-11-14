# PDB-Mapping Pipeline (Sequence Search + Structure Alignment)

The PlantTPDB PDB-mapping workflow: for each plant transporter protein,
the best PDB mapping is found in two stages —
**(1)** sequence search of the 733-plant proteomes against a PDB
polymer-entity sequence library with DIAMOND blastp (e-value 1e-10, query
coverage 60%, best hit only), followed by a sequence-identity >= 30% filter; then
**(2)** structure alignment (USalign) of the AlphaFold 3 model of each
candidate against its corresponding PDB-derived single-chain reference
structure, scored by TM-score and by the structural
alignment rate **Lali/L2**.

Note on scope: the sequence search was run once for all 733 plants; the
structure-alignment stage was run for the 15 species with AF3 models, and
the shipped pair list (`data/af3_pdb_pairs.list`, 19,297
AF3-model/PDB-chain pairs) is the curated 15-species selection from the
filtered sequence hits. The 19,297 mapping pairs involve 293 distinct PDB
polymer entities. For the structural comparison reported in Supplementary
Table 17, one AlphaFold 3 model was selected for each PDB polymer entity
based on the highest sequence-alignment ranking score (column 8 of
`af3_pdb_pairs.list`). The script numbers are
sequential for the release.

## Layout

- `scripts/` — workflow scripts (numbered by workflow order; run from the
  module root as shown under Usage):
  - `01_blastp_diamond.sh` — stage 1: per-species DIAMOND blastp vs the
    PDB sequence library (submitted via LSF `bsub`;
    `--evalue 1e-10 --query-cover 60 --max-target-seqs 1 --more-sensitive`,
    outfmt 6)
  - `02_filter_identity_30.sh` — stage 1 filter: keep hits with sequence
    identity >= 30% (column 3 of the BLAST tabular output)
  - `03_run_usalign.py` — stage 2: parallel USalign driver — reads a
    `species  protein  pdb_entity_id` list, locates `<protein>_model.cif`
    (AF3 model) and `<pdb_entity_id>.cif` (the corresponding PDB-derived
    single-chain reference structure), runs `USalign -mm 1 -ter 1
    -outfmt 2`
  - `04_compute_structure_alignment_rate.py` — computes the structural
    alignment rate **Lali/L2** (aligned residues over PDB-chain length)
    from the USalign tabular output; restores Species/Protein/PDB columns
    when absent; optional `--min-rate` filter.
- `data/` — shipped run data:
  - `pdb_chain_sequences.fasta` — the PDB polymer-entity sequence library
    used as the DIAMOND search target (18,260 PDB polymer-entity sequences
    derived from the RCSB PDB SEQRES database). Build the
    DIAMOND database with:
    `diamond makedb --in data/pdb_chain_sequences.fasta --db data/pdb_chain_sequences`
    (DIAMOND v0.9.27 as used in the original run)
  - `af3_pdb_pairs.list` — actual input to stage 2: 19,297 curated
    AF3-model/PDB-polymer-entity mapping pairs for the 15 AF3 species,
    covering 293 distinct PDB polymer entities, with per-pair
    sequence-search metrics and a reference-category annotation:
    `direct reference (high confidence)` 2,507 / `functional reference
    (medium confidence)` 8,235 / `fold reference (low confidence)` 6,421 /
    `remote reference (hidden by default)` 2,134)
  - `usalign_results.txt` — reference output of stage 2 (19,297 USalign
    rows + header + one trailing blank line; comment lines removed with
    `grep -v "#"`); input to `04_compute_structure_alignment_rate.py`

Dependencies: Bash, Python 3 (standard library only), DIAMOND (tested
0.9.27), USalign (tested v20241108), LSF (`bsub`); no third-party Python
packages are required.

## Pipeline

```bash
# 0) build the DIAMOND database from the shipped FASTA:
diamond makedb --in data/pdb_chain_sequences.fasta --db data/pdb_chain_sequences

# 1) sequence search (one job per species proteome in 04.733_pep/)
bash scripts/01_blastp_diamond.sh

# 2) identity >= 30% filter
bash scripts/02_filter_identity_30.sh

# curation (not scripted): select the 15 AF3-species pairs
#   -> data/af3_pdb_pairs.list

# 3) structure alignment (AF3 model vs PDB single chain)
python scripts/03_run_usalign.py -i data/af3_pdb_pairs.list -p pdb_single_chains_0-1 -t 20 -o usalign_results_run.txt

# 4) structural alignment rate Lali/L2
python scripts/04_compute_structure_alignment_rate.py -i usalign_results_run.txt -o structure_alignment_rate_run.txt
```

Rerun outputs are written to the working directory; the shipped files under
`data/` are read-only reference inputs.

On the shipped reference output this yields 19,297 valid rows with
Lali/L2 min 0.0598 / median 0.9617 / mean 0.8801 / max 1.0000 (93.2% of
pairs >= 0.5).

## Not included (size / third-party)

- AlphaFold 3 models for 15 species (CIF) — external archives:
  [10.5281/zenodo.21637784](https://zenodo.org/records/21637784)
  (59,494 locally generated models) and [10.5281/zenodo.23949550](https://zenodo.org/records/23949550)
  (278 AlphaFold Server supplements; 59,772 models in total)
- Species proteomes (`04.733_pep/`) and the PDB single-chain CIF library
  (`pdb_single_chains_0-1/`) — reproducible from the PDB
  (https://www.rcsb.org) and the PlantTPDB protein sets; the PDB
  polymer-entity sequence library itself is bundled under `data/`
- Binaries: DIAMOND, USalign, LSF; stage 1 was run on an LSF cluster,
  stage 2 on a 20-core node
