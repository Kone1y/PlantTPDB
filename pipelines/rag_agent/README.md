# PlantTP Agent — RAG QA Service

Flask service implementing the PlantTP Agent: retrieval routes, cross-encoder
reranking, abstention guard, PTDB database utilities.

## Layout

- `app.py` — Flask application (normal QA, document retrieval, index/tccode
  dedicated-entity pages)
- `src/service/` — `normal_rag.py`, `rag.py` (retrieval + answer generation),
  `guard.py` (gatekeeper: routing + trap classification), `ptdb_utils.py`,
  `citation_enrich.py`, `tc_symbol_tem.py`, `config.py`
- `requirements.txt`

## Configuration

All credentials are environment-injected (see `src/service/config.py`):

- `RAG_VDB_PATH` — Chroma vector store (collection `papers_rag_md`). The
  full store (full-text chunks + embeddings) is not redistributed with
  this deposit for copyright reasons; `database_snapshots/vector_database/`
  ships a metadata-only public export of the store (document/chunk DOI
  index and index configuration, from which the ingestion setup can be
  read). Rebuild the store locally from licensed full texts to run the
  service.
- `RAG_EMBEDDING_MODEL` — local `bge-embedding-large-en-v1.5` model directory
- `RAG_RERANKER_MODEL` — local `bge-reranker-large` model directory
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` — answer generation and gating
- `QWEN_API_KEY` / `QWEN_BASE_URL` — query rewriting (Qwen3-32B,
  OpenAI-compatible endpoint)

Model weights are external and not archived. Run with
`python3 app.py` after setting the environment variables.

## Frozen configuration used for the reported evaluation

The benchmark-04 evaluation ran with these settings (defaults in code; no
environment overrides were set):

| Setting | Value | Location |
|---|---|---|
| Candidate chunks per retriever / query variant | 20 (`RAG_LOCAL_QUERY_N`) | `src/service/rag.py`, `src/service/normal_rag.py` |
| Evidence window after reranking | 18 (`RAG_SUMMARY_TOP_CHUNKS`) | same |
| Query rewriting | Qwen3-32B, up to 3 variants | `normal_rag.py` |
| Answer generation | DeepSeek-V4-Flash, temperature 0.2, max_tokens 16384 | `normal_rag.py` |
| Input gate | DeepSeek-V4-Flash, temperature 0 | `guard.py` |
| Embedding / reranker | bge-embedding-large-en-v1.5 / bge-reranker-large | env `RAG_EMBEDDING_MODEL` / `RAG_RERANKER_MODEL` |
| Vector store | chroma `papers_rag_md` (public metadata export in `database_snapshots/`) | env `RAG_VDB_PATH` |
