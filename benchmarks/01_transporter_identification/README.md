# Benchmark 01 — Transporter Identification

Evaluation of the transporter identification pipeline with three
partially overlapping and complementary homology-based evidence
sources: **T** (DIAMOND BLASTP against TCDB, best-hit E-value <= 1e-20), **P**
(HMMER hmmscan against Pfam-A, domain i-Evalue < 1e-5), and **S**
(DIAMOND BLASTP against a curated seed library, matching an annotated
transporter). A protein is classified as a transporter when at least k
of the three criteria are met.
**k = 2 is the default decision rule** of the pipeline (accuracy 0.982, F1 0.982; see
`results/eval_metrics_summary.tsv`).

**Development-set caveat.** The 1,000-protein set is the development set on
which the decision rule (k = 2 of 3) and the evidence thresholds were
selected; the reported metrics are therefore development-set (in-sample)
performance conditional on this benchmark, not a held-out estimate of
generalization.

The per-species production pipeline implementing this decision rule is
archived under `pipelines/transporter_identification/`.

## Layout

- `data/benchmark_set/` — benchmark_positive.fasta (500 transporters,
  Swiss-Prot + RAP-DB), benchmark_negative.fasta (500 non-transporter
  membrane proteins, Swiss-Prot reviewed)
- `data/reference_db/` — curated seed libraries, deduplicated FASTA
  (9-species library and A. thaliana library)
- `data/seed_info/` — seed annotations: locus, isoform, TC family,
  Pfam, a numeric evidence annotation (identity;e-value in the
  A. thaliana file), and description.
- `data/dedup_provenance/` — removed-entry lists (entries sharing
  >=95% sequence identity with a positive benchmark sequence at
  >=90% coverage).
- `code/evaluate_identification_strategies.py` — evaluation engine
- `results/` — reference outputs (predictions k1/k2/k3, metrics summary, family metrics)
- `run_benchmark.sh` — one-command run of the full evaluation

## Third-party databases (not bundled — license)

TCDB and Pfam-A are downloaded automatically on first run:

| Database | URL |
|---|---|
| TCDB sequences (FASTA) | https://www.tcdb.org/public/tcdb |
| Pfam-A HMM library | https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.gz |

**Reproducibility note.** Both URLs serve the databases as published at
access time (the Pfam URL points to the live `current_release`). The
exact TCDB and Pfam-A snapshots used for the archived reference outputs
are not redistributed (license) and their versions are not recorded, so
the T and P criteria — and metrics that depend on them — may differ if
the live databases have changed since. The shipped artifacts are
byte-stable (benchmark set, deduplicated seed libraries, the
`transporter_pfam.tsv` domain list, and the reference outputs), and the
evaluation engine, thresholds, and decision rules are pinned in
`run_benchmark.sh`; only the two license-restricted external databases
are fetched as published.

## Usage

```bash
bash run_benchmark.sh                 # THREADS=20 bash run_benchmark.sh
```

Requirements: Python 3 (matplotlib optional), DIAMOND >= 2.0, HMMER 3.
