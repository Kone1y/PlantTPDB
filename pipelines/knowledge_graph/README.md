# Knowledge-Graph Triplet Extractor

`extract_relation_triples.py` — LLM-based quintuple extractor for building
knowledge-graph triples. It reads per-gene sentence files
(`<symbol>_sentences.json`) and extracts
`(head, head_type, relation, tail, tail_type)` quintuples through the
DeepSeek chat API (`deepseek-chat`).

Relation schema: `transports`, `imports`, `exports`, `symports`, `location`
(sentence-level extraction targets) plus `published_in` (attached during graph
assembly). Entity schema: `transport_protein`, `paper`, `substrate`,
`membrane` (`paper` attached during graph assembly).

The prediction set of benchmark 03
(`benchmarks/03_entity_relation_extraction/predictions/predicted_triples.json`)
originates from this extractor.

Schema note: the extraction prompt targets the five sentence-level relations
(`transports`, `imports`, `exports`, `symports`, `location`) and the three
sentence-level entity types (`transport_protein`, `substrate`, `membrane`);
`paper` entities and `published_in` relations are attached during graph
assembly from source-paper metadata (DOI), not extracted from sentences.

## Usage

Requires `DEEPSEEK_API_KEY` (not stored in code) and Python 3 with `tqdm`.
Paths are configurable via environment variables (defaults relative to the
working directory):

| Variable | Meaning | Default |
|---|---|---|
| `SENTENCES_DIR` | input `<symbol>_sentences.json` directory | `sentences/` |
| `TRIPLETS_DIR` | output directory | `triplets/` |
| `SYMBOL_MAPPING_FILE` | gene symbol -> category JSON (defines the gene set) | `symbol_to_categories.json` |
