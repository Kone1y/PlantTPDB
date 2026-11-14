# Benchmark 03 — Entity–Relation Extraction

Evaluation of LLM-based entity and relation extraction from plant-transporter
abstracts, against a manually curated gold standard.

## Data

- `data/benchmark_paper_dois.txt` — 200 benchmark papers.
- `data/source_abstract_records.json` — abstract records for the 200
  benchmark papers.
- `data/gold_triples.json` — reference standard: 631 triples.
- `predictions/predicted_triples.json` — archived predictions: 654
  triples.

## Evaluation

Predictions are compared with the reference annotations using the
supplied evaluation code and `code/matching_rules.json`. Both sides are
first deduplicated by canonical entity identity; a prediction unit and a
gold unit are matched only on identical paper (DOI), identical relation
label, compatible endpoint types, and equal canonical entity names,
where names are canonicalized through type-scoped alias groups and
DOI-scoped evidenced protein-name mappings. Units are paired by
maximum-cardinality one-to-one matching, so each prediction or gold unit
is counted at most once.

## Results

| Evaluation | TP | FP | FN | Micro F1 |
|---|---|---|---|---|
| Relation extraction | 529 | 120 | 100 | **0.8279** |
| Entity extraction | 736 | 118 | 62 | **0.8910** |

These F1 values characterize the extraction benchmark only; they are not
accuracy measures of the knowledge graph or of the agent QA system.

## Reproduction

```bash
bash run_evaluation.sh
```
