# Literature Screening Benchmark

Evaluation of the screening Agent against final expert reference labels for
1,000 publications: 500 positive and 500 negative.

## Files

| File | Contents |
|---|---|
| `predictions/agent_screening_outputs.tsv` | Original screening Agent outputs; tab-separated |
| `data/reference_labels.csv` | Final expert reference labels: `pos` or `neg` |
| `code/score_screening.py` | Deterministic rescoring script (no model calls) |
| `results/screening_metrics.tsv` | Frozen output of `score_screening.py` |

## Evaluation

Records are matched by DOI. Agent decisions of `Yes` are counted as
positive; `No` and `Boundary` are counted as negative. All 1,000
publications are included.

| TP | FP | TN | FN | Accuracy | Precision | Recall | F1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 486 | 0 | 500 | 14 | 0.986 | 100% | 97.2% | 0.986 |

Recompute with `python3 code/score_screening.py` (writes
`results/screening_metrics.tsv`). These metrics describe performance on
this balanced benchmark.
