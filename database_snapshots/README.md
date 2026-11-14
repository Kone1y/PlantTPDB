# Database Snapshots

## vector_database/

Public **metadata-only export** of the Chroma vector store `papers_rag_md`
used by the PlantTP Agent for literature retrieval. It contains the
document/chunk identifier index and the index configuration — no texts,
no embedding vectors, and no database files.

| Field | Value |
|---|---|
| Export file | `vector_database/chroma_vdb_public_minimal_20261105.tar.gz` |
| Contents | `documents.csv` (10,160 publications), `chunks.csv` (81,011 section chunks), `index_config.json`, inner `README.md` + `MANIFEST.json` |
| Source store | `papers_rag_md` collection (Chroma, dimension 1024), state 2026-11-05 |
| Related evaluation | `benchmarks/04_agent_qa/` |
| Checksum | SHA-256 recorded in the top-level `MANIFEST.json` |

## Core database snapshot

The full PlantTPDB relational-database snapshot, `PlantTPDB_core_20261105`,
is available on Zenodo: [10.5281/zenodo.23995306](https://zenodo.org/records/23995306).
