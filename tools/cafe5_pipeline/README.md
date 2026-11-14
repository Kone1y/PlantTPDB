# Tool — CAFE5 Gene-Family Expansion/Contraction Pipeline

Reproducible example of the gene-family expansion/contraction
analysis used in the PlantTPDB study: **26 plant species, TC-family count
matrix, single-lambda and two-lambda birth–death models** fitted with CAFE5
under a robust multi-restart protocol, with family-level BH-FDR correction.

Pipeline: BUSCO pseudo-single-copy orthologs → MAFFT/trimal/AMAS supermatrix
→ IQ-TREE species tree → MCMCtree dated ultrametric tree (TimeTree secondary calibrations)
→ CAFE5 (two-pass error-model base run; single-rate and two-rate models, each with
30 joint restarts + 20 fixed-error restarts, best by -lnL) → BH-FDR →
figures. The two-rate model assigns the deep lineages *Klebsormidium
nitens*, *Edaphochlamys debaryana*, and *Volvox carteri* a separate
lambda-2 category (configurable via `deep_lambda_taxa`).

## Data availability

The complete input data is bundled under `data/` in this release.
Before running, unpack the compressed input:

```bash
cd tools/cafe5_pipeline/data
tar -xzf busco_odb10.tar.gz            # -> busco_odb10/ (26 species)
```

## Layout

- `run_pipeline.sh` — main entry (steps s00–s13; sentinel-based resume;
  `--tree-mode ml`)
- `scripts/` — helper scripts, numbered by workflow order:
  `01_fetch_timetree.py`, `02_auto_extract_outgroup.py`,
  `03_auto_mcmctree_from_timetree.py`, `04_get_tc_code_number.py`,
  `05_plot_cafe5_cafeplotter.py`, and `06_run_cafe_restarts.sh`
  (CAFE5 robust-restart driver for both rate models; writes `rate_params.tsv`
  with lambda1[, lambda2], epsilon, -lnL)
- `config.yaml` — configuration (defaults to commands in PATH; edit software
  paths only if installed elsewhere)
- `requirements.txt` — Python dependencies (conda/paml installation below)
- `data/` — bundled inputs:
  - `species.list`, `species_alias.tsv`, `timetree_26_species.nwk` — the
    26-species set and the TimeTree topology
  - `busco_odb10.tar.gz` — pruned BUSCO results (lineage
    `viridiplantae_odb10`; sequence files the pipeline reads + run
    summaries); extract to `data/busco_odb10/` before running
  - `cafe_input_matrix_26species.full.tsv` — the 26-species full TC-family
    count matrix in CAFE5 format (Desc \t Family ID \t 26 species columns;
    gene-deduplicated counts, all 156 TC families, no filtering applied);
    step 04 applies the per-species count >= 100 family filter to it
  - `tc_family_descriptions.tsv`, `tc_manuscript_156_families.list`,
    `species_taxid_map.tsv` — family descriptions; 156 TC families;
    species-to-TaxID mapping for MCMCtree calibration.

## Key outputs (`<outdir>/results/`)

| Path | Content |
|---|---|
| `03_ultrametric_tree/r8s.ultrametric.txt` | dated ultrametric tree (MCMCtree) |
| `04_cafe_input/tc.filtered.tsv`, `family_filter_report.tsv` | CAFE5 input matrix; per-family retention/exclusion audit trail |
| `05_cafe5_output/cafe5_{base,k1,1lambda,2lambda}/` | model results + BH-FDR + `rate_params.tsv` (report the `final_fixed_error` row; lambda per 10-Myr) |
| `06_figures/cafe5_*_plot/` | summary and per-family figures |

## Usage

```bash
conda create -n cafe5_pipeline python=3.10 -y && conda activate cafe5_pipeline
conda config --add channels bioconda && conda config --add channels conda-forge
conda config --set channel_priority strict
conda install -y mafft trimal iqtree=3.1.2 newick_utils cafe
# IQ-TREE-compatible PAML fork (do NOT use bioconda paml — it rejects the
# control file written by `iqtree --dating mcmctree`):
git clone https://github.com/iqtree/paml /tmp/paml-iq2mc
(cd /tmp/paml-iq2mc/src && make && cp mcmctree baseml codeml yn00 chi2 evolver "$CONDA_PREFIX/bin/")
pip install -r requirements.txt
AMAS_PATH=$(python -c "import amas, os; print(os.path.join(os.path.dirname(amas.__file__), 'AMAS.py'))")
chmod +x "$AMAS_PATH"
```

```bash
cd tools/cafe5_pipeline
bash run_pipeline.sh run-all \
    --species data/species.list \
    --config config.yaml \
    --outdir rerun_test \
    --matrix-type tc \
    [--tree-mode ml] \
    --force
```

Re-running without `--force` resumes from completed steps.

Requirements: Linux, Bash, Python >= 3.10 (dependencies in
`requirements.txt`), approximately 20 CPU threads, and Internet access for
TimeTree queries. Pairwise divergence times are cached in
`<outdir>/.timetree_pairwise_cache.json`.
