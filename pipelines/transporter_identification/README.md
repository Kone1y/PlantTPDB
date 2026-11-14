# Transporter Identification Pipeline

Per-species proteome annotation pipeline implementing the PlantTPDB
transporter identification decision rule — the k = 2 of 3 evidence rule
(TCDB homology **T**, Pfam transporter domain **P**, curated-seed
homology **S**) evaluated on a labeled benchmark set in
`benchmarks/01_transporter_identification/`. The decision rule follows that benchmark — three partially overlapping
and complementary homology-based evidence sources: **T** = TCDB alignment assigns a TC number (best-hit E-value
<= 1e-20); **P** = the protein contains a transporter-characteristic
Pfam domain (domain i-Evalue < 1e-5; the domain list is
`benchmarks/01_transporter_identification/data/reference_db/transporter_pfam.tsv`);
**S** = DIAMOND BLASTP against a curated seed library matching an
annotated transporter (best hit for the *A. thaliana* library, top-5
rank window for the multi-species library; hit E-value <= 1e-20). A
protein is called transporter when at least two of the three criteria
are met. The module takes a protein FASTA
plus a species name, runs the searches, integrates the evidence, and
writes the per-protein transporter information tables.

A containerized variant of this pipeline is available as the Docker
image `paulfire/transporter-pred:v1.0.0` (digest in the root
`README.md`), which bundles the search databases and TMHMM.

## Layout

- `run_transporter_prediction_no_deeploc.sh` — end-to-end runner
  (database bootstrap -> searches -> evidence integration -> merged
  summary -> `results.zip`)
- `get_infomation_base1_pfam.py` — evidence integration; reads the
  search tables from the given output directory and combines the TCDB,
  multi-species-seed, *A. thaliana*-seed, and Pfam evidence into the
  per-protein summary tables
- `entrypoint.sh` — container entrypoint (validates `SPECIES`, runs
  the pipeline, cleans TMHMM scratch)

## Requirements

Python 3, DIAMOND >= 2.0, HMMER 3 (`hmmscan`), `curl`, `zip`, and
TMHMM 2.0 (license-restricted academic software; obtain it separately
and point `PTD_TMHMM_DIR` at its directory).

## Databases

The runner bootstraps all search databases automatically on first run
(Step 0) and skips anything already present or provided via an
environment override:

| Database | How it is obtained |
|---|---|
| TCDB sequences | downloaded from https://www.tcdb.org/public/tcdb and `diamond makedb`-ed (license: not redistributed here) |
| Pfam-A HMM library | downloaded from https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.gz and `hmmpress`-ed (license: not redistributed here) |
| Seed libraries | `diamond makedb` from the curated deduplicated FASTA shipped in `benchmarks/01_transporter_identification/data/reference_db/` (override the source with `PTD_SEED_FA` / `PTD_ATH_FA`; the deduplication removed entries >=95% identical to a benchmark positive — lists in `data/dedup_provenance/`) |
| Seed annotations | the copies shipped in `benchmarks/01_transporter_identification/data/seed_info/` are picked up automatically (or set `PTD_SEED_INFO_ARA` / `PTD_SEED_INFO_ATH`) |

## Usage

```bash
bash run_transporter_prediction_no_deeploc.sh proteins.fa Oryza_sativa ./output
```

Environment overrides: `PTD_WORK_DIR`, `PTD_DMND_TC`, `PTD_DMND_SEED`,
`PTD_DMND_ATH`, `PTD_PFAM_DB`, `PTD_PFAM_TRANSPORT`, `PTD_SEED_FA`,
`PTD_ATH_FA`, `PTD_SEED_INFO_ARA`, `PTD_SEED_INFO_ATH`,
`PTD_TMHMM_DIR`, `PTD_THREADS`. With no overrides and the archive tree
intact, the only external requirement is a TMHMM 2.0 installation.

Outputs (in the output directory): `<SPE>_information_base1.txt`
(per-protein evidence summary: TC hit, Pfam domains, seed hits, TMHMM
topology), `<SPE>_pfam_info.txt` (domain-level Pfam records), the raw
search tables (`TC_res/`, `Seed_res/`, `Seed_ath_res/`, `Pfam_res/`,
`TMHMM2/`), and `results.zip`.
