# Literature Update Agent

Weekly cron-driven pipeline that keeps PlantTPDB current with new plant
transporter literature. Stage 1 (`auto_plant_transporter_pipeline.py`)
searches OpenAlex, PubMed and Crossref for recent publications, applies a
keyword pre-filter (`literature_filter.py`) and an LLM relevance filter
(`literature_filter_llm_api_v2.py`), and writes per-run reports under
`results/run_YYYYMMDD/`. Stage 2 (`run_weekly_mapper.sh` →
`symbol_literature_mapper.py`) maps the screened articles of the day's run
onto gene symbols and transporter families under the curated Arabidopsis
symbol standard (`ath_symbol_tc_final_v2.tsv`, Araport11-derived), writing
`symbol_literature_map.json/.tsv` and `symbol_family_map.tsv` back into the
run directory. This is the deployed weekly configuration.

## Layout

- `openalex_search.py` / `pubmed_search.py` / `crossref_search.py` — keyless search clients for the three public APIs
- `literature_filter.py` — deterministic keyword pre-filter (plant × transporter terms, animal-study exclusions)
- `literature_filter_llm_api_v2.py` — LLM relevance filter (OpenAI SDK; DeepSeek-compatible endpoint by default)
- `auto_plant_transporter_pipeline.py` — stage-1 orchestrator: search → merge/dedup → filter → report (cross-run dedup via `collected_articles.json`)
- `run_weekly_mapper.sh` — stage-2 cron entry; processes the run's `filtered.tsv`
- `symbol_literature_mapper.py` — symbol/family mapping with optional LLM symbol extraction; normalises to the ath v2 symbol system by default (`--no-ath-filter` reverts)
- `ath_symbol_normalizer.py` — library module imported by the mapper (Araport11 symbol bridging)
- `symbol.list` — curated standard symbol library (14,232 entries; default `--symbol-list` input)
- `ath_symbol_tc_final_v2.tsv` — curated Ath_id/Symbol/TC standard table (default `--ath-table` input)
- `Araport11_mRNA_info.tsv` — Araport11-derived gene model info (default `--araport` input; redistributed as a derived subset)
- `LLM_API_CONFIG.example` — template for `pipeline.env` / environment configuration
- `tests/` — offline unit tests (synthetic fixtures, no network)

## Configuration

All credentials are environment-injected and never archived
(see `LLM_API_CONFIG.example`):

- `LLM_API_URL` — OpenAI-compatible endpoint (DeepSeek by default)
- `LLM_API_KEY` — API key (required for the LLM stages)
- `LLM_MODEL` — model name
- `PYTHON` — interpreter override used by `run_weekly_mapper.sh` (default `python3`)

The search APIs (OpenAlex, PubMed, Crossref) require no keys.

## Usage

```bash
# Stage 1 — weekly search + screening (keyword mode; drop --no-llm after exporting the key)
python3 auto_plant_transporter_pipeline.py --output-dir ./results --days-ago 7 --no-llm

# Stage 2 — map the run onto symbols/families (requires LLM_API_KEY)
./run_weekly_mapper.sh [run_YYYYMMDD]
```

Production schedule (crontab, Wednesdays): stage 1 at 08:00, stage 2 at 08:30.

## Tests

```bash
python3 -m pytest tests/
```

Offline; fixtures are synthetic mini-libraries and do not touch the shipped
data files or the network.

## Scope and limitations

- `symbol_literature_mapper.py` is the deployed variant (ath-normalised,
  `--tsv-only` weekly flow).
- LLM extraction results are cached per symbol (`llm_symbol_cache.json`,
  shared across runs); caches and run outputs are not archived.
