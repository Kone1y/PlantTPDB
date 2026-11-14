# PlantTPDB Release

## Overview

This repository provides benchmark datasets, reference annotations, model
predictions, evaluation results, analysis pipelines, and database snapshots
associated with the PlantTPDB study — a plant transporter database combining
literature screening, knowledge-graph extraction, transporter identification,
and a retrieval-augmented question-answering agent.

The archive contains inputs, expert-curated reference data, archived
predictions, evaluation code, and reported results for each scientific
evaluation task under `benchmarks/`. Source code for the analysis pipelines
used to generate predictions is located under `pipelines/`.
Structure-prediction and PDB-mapping workflows are provided under `AF3/`
and `PDB/`, respectively, while exploratory CAFE5 analysis files are located
under `tools/cafe5_pipeline/`.

## Repository Contents

| Component | Directory | Content |
|---|---|---|
| Benchmarks | `benchmarks/01_transporter_identification/` | Three-evidence transporter identification benchmark (1,000 proteins) |
| | `benchmarks/02_literature_screening/` | 1,000-paper relevance-screening benchmark |
| | `benchmarks/03_entity_relation_extraction/` | 200-paper entity and relation extraction benchmark |
| | `benchmarks/04_agent_qa/` | 240-question QA benchmark and a separate 60-question abstention challenge: retrieval, answer quality, claim-level grounding, abstention |
| Pipelines | `pipelines/knowledge_graph/` | LLM triplet extractor (source of benchmark 03 predictions) |
| | `pipelines/rag_agent/` | PlantTP Agent QA service (source of benchmark 04 outputs) |
| | `pipelines/literature_screening/` | Production full-pool literature screening (source of the screened corpus) |
| | `pipelines/literature_update/` | Weekly literature monitoring & screening agent (OpenAlex/PubMed/Crossref + keyword/LLM filters + symbol mapping; offline unit tests included) |
| Structure pipelines | `AF3/` | AlphaFold 3 v3.0.1 workflow scripts (MSA + inference stages) |
| | `PDB/` | PDB-mapping pipeline (sequence search + USalign) with shipped run data |
| Analysis tools | `tools/cafe5_pipeline/` | BUSCO/IQ-TREE/MCMCtree/CAFE5 exploratory family-size pipeline (inputs + scripts) |
| | `tools/phylogenetic_analysis.sh`, `tools/evolution_analysis.sh` | Website phylogenetic (MAFFT+FastTree) and sequence-identity (MAFFT) module backends |
| Database snapshots | `database_snapshots/vector_database/` | Metadata-only public export of the `papers_rag_md` RAG vector store (document/chunk DOI index + index configuration) |
| Database snapshot | Zenodo | `PlantTPDB_core_20261105` core database snapshot; download: [10.5281/zenodo.23995306](https://zenodo.org/records/23995306) |

Supplementary Tables (S1-S30) are archived on Zenodo: [10.5281/zenodo.24187256](https://zenodo.org/records/24187256).

**Reproducibility layers.** Frozen results are reproducible offline: the
archived evidence windows, human annotations, and predictions are rescored
by the shipped scripts (e.g., benchmark 04 `score.py`). The public
vector-store snapshot is metadata-only (no texts, no vectors); re-running
retrieval or the reranking ablation requires obtaining the underlying
articles and rebuilding the full index, and regenerating answers
additionally requires the model services.

## Benchmark Summary

| Benchmark | Task | Scale | Principal reported metrics |
|---|---|---|---|
| 01 Transporter identification | classify transporter vs non-transporter protein | 1,000 proteins (500 / 500) | two-of-three rule: accuracy 0.982, F1 0.982 |
| 02 Literature screening | plant-transporter relevance screening | 1,000 papers | adjudicated records vs reference labels: accuracy 0.986 |
| 03 Entity–relation extraction | triple and entity extraction from abstracts | 200 papers; 631 reference / 654 predicted triples | micro F1: relation 0.8279, entity 0.8910 |
| 04 Agent QA | retrieval, answer quality, claim grounding, abstention | 240 QA questions; 3,534 claims; separate 60-question abstention set | composite answer-quality score 82.9/100; 95.6% of claims evidence-supported; 82.9% automated-evaluator (RAGAS) agreement over all claims; 93.3% abstention rate on the designated unanswerable set; retrieval nDCG@10 0.684, MRR 0.967 |

Benchmark 01 metrics are development-set performance: the decision rule
and evidence thresholds were selected on the same 1,000-protein set (see
`benchmarks/01_transporter_identification/README.md`).

Each metric above can be recomputed from the archived files in the
corresponding benchmark directory using the commands in the per-benchmark
READMEs.

## Code and Data Organization

```text
PlantTPDB_release/
├── README.md / MANIFEST.json / LICENSE / LICENSING.md
├── benchmarks/
│   ├── 01_transporter_identification/   data/ code/ results/ run_benchmark.sh
│   ├── 02_literature_screening/         data/ predictions/ code/ results/
│   ├── 03_entity_relation_extraction/   data/ predictions/ code/ results/ run_evaluation.sh
│   └── 04_agent_qa/                     data/ code/ results/ docs/
├── pipelines/
│   ├── knowledge_graph/
│   ├── literature_screening/
│   ├── literature_update/
│   ├── transporter_identification/
│   └── rag_agent/
├── AF3/
├── PDB/
├── tools/
│   └── cafe5_pipeline/
└── database_snapshots/
    └── vector_database/
```

`MANIFEST.json` lists every file in this deposit except itself, each with
its byte size and SHA-256 checksum.

## Data and Code Availability

- **This archive** (v1.0.0) — one public release, also deposited as a
  single frozen ZIP on Zenodo (concept DOI
  [10.5281/zenodo.20593738](https://zenodo.org/records/20593738);
  this version: [10.5281/zenodo.24199058](https://zenodo.org/records/24199058)):
  - **GitHub** (`Kone1y/PlantTPDB`) — all code and
    small-to-medium data (143 files). The two files larger than 100 MB
    are not included in the Git repository. They are available in the Zenodo
    benchmark-reproduction deposit [10.5281/zenodo.24074039](https://zenodo.org/records/24074039), with
    their SHA-256 checksums recorded in `MANIFEST.json`:
    - `benchmarks/04_agent_qa/data/claims/ragas_eval_input_3534.jsonl.gz` (142 MiB)
    - `benchmarks/01_transporter_identification/data/reference_db/9_all_spe_add_16osa_v1_dedup.fa` (189 MiB)
- **Core database snapshot** — `PlantTPDB_core_20261105` is available on
  Zenodo: [10.5281/zenodo.23995306](https://zenodo.org/records/23995306).
- **External databases used by the benchmarks** — TCDB
  (https://www.tcdb.org) and Pfam-A
  (https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/) are not
  redistributed here and are downloaded by `run_benchmark.sh` under their
  own licenses.
- **Model weights** — the embedding (`bge-embedding-large-en-v1.5`) and reranker
  (`bge-reranker-large`) models and the LLM endpoints (DeepSeek, Qwen) are
  external resources configured via environment variables; no credentials
  are archived.
- **Container** — the production transporter-identification pipeline is
  packaged as the Docker image `paulfire/transporter-pred:v1.0.0`
  (linux/amd64; registry digest
  `sha256:576d887f58bfb0528f0353853b7a58f64fb8dab1a3248c3aab2841d1d9036b7c`;
  the `latest` tag points to the same digest, and the digest is the
  immutable reference):

  ```bash
  docker pull paulfire/transporter-pred@sha256:576d887f58bfb0528f0353853b7a58f64fb8dab1a3248c3aab2841d1d9036b7c
  ```

  The benchmark-level identification logic (the same three evidence
  criteria and decision rules) can be reproduced without the container via
  `benchmarks/01_transporter_identification/run_benchmark.sh` (note: the
  license-restricted TCDB/Pfam-A databases are fetched as published at run
  time — see that benchmark's README for the reproducibility caveat); the
  per-species pipeline implementation is archived under
  `pipelines/transporter_identification/`.
- **Not included in this Git repository** — AlphaFold 3 model parameters
  and genetic databases (see `AF3/README.md`), and the 15-species
  proteome sequence sets.
- **Companion Zenodo deposits** — versioned records accompanying this
  repository (concept DOI of the record chain:
  [10.5281/zenodo.20593738](https://zenodo.org/records/20593738)):

  | Deposit | Version DOI |
  |---|---|
  | Core database snapshot `PlantTPDB_core_20261105` | [10.5281/zenodo.23995306](https://zenodo.org/records/23995306) |
  | Supplementary Tables (S1-S30) | [10.5281/zenodo.24187256](https://zenodo.org/records/24187256) |
  | AF3 structure models, 15 selected crop and model angiosperm species (59,494 local predictions) | [10.5281/zenodo.21637784](https://zenodo.org/records/21637784) |
  | AF3 Server supplement (+278 models; 59,772 total) | [10.5281/zenodo.23949550](https://zenodo.org/records/23949550) |
  | AF3 model manifest and quality metrics (59,772 models) | [10.5281/zenodo.24097576](https://zenodo.org/records/24097576) |
  | AF3–PDB sequence mappings (19,297 protein isoform–PDB chain pairs) | [10.5281/zenodo.24088683](https://zenodo.org/records/24088683) |
  | PDB CIF library, raw download | [10.5281/zenodo.20619303](https://zenodo.org/records/20619303) |
  | PDB CIF, curated set (transporters / electron transport / photosystems) | [10.5281/zenodo.21636014](https://zenodo.org/records/21636014) |
  | Large benchmark inputs: deduplicated reference sequences and frozen RAGAS evaluation data | [10.5281/zenodo.24074039](https://zenodo.org/records/24074039) |
  | Code, workflows, and benchmark data archive | [10.5281/zenodo.24199058](https://zenodo.org/records/24199058) |

## Citation and License

Citation: this archive is citable via the Zenodo concept DOI
[10.5281/zenodo.20593738](https://zenodo.org/records/20593738) (versioned record chain).
License: original code is released under the MIT License (`LICENSE`);
original research data and documentation are licensed under
CC BY 4.0; third-party components embedded in this deposit (published
article texts and abstracts, curated reference libraries, BUSCO/TCDB/PDB
derivatives, Araport11 subsets) remain under their own licenses and terms —
see `LICENSING.md` for the complete scope mapping, attribution requirements,
and the CC BY 4.0 license text.
