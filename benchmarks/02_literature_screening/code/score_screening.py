#!/usr/bin/env python3
"""Recompute the literature-screening confusion matrix and headline metrics
from the archived predictions and reference labels, and write the frozen
result table to results/screening_metrics.tsv.

Matching rule (as documented in this benchmark's README): records are
matched by DOI (case-insensitive); agent decisions of "Yes" count as
positive, "No" and "Boundary" count as negative; all 1,000 publications
are included.
"""
import csv
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PRED = BASE / "predictions" / "agent_screening_outputs.tsv"
REF = BASE / "data" / "reference_labels.csv"
OUT = BASE / "results" / "screening_metrics.tsv"


def main():
    pred = {}
    with PRED.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if row and row[0].strip():
                pred[row[0].strip().lower()] = row[4].strip()
    labels = {}
    with REF.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            labels[row["doi"].strip().lower()] = row["type"].strip()

    tp = fp = tn = fn = 0
    missing = [d for d in labels if d not in pred]
    if missing:
        raise SystemExit(f"{len(missing)} reference DOIs missing from predictions, e.g. {missing[:3]}")
    for doi, lab in labels.items():
        pos = pred[doi] == "Yes"
        if lab == "pos" and pos:
            tp += 1
        elif lab == "pos":
            fn += 1
        elif pos:
            fp += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["TP", "FP", "TN", "FN", "Accuracy", "Precision", "Recall", "F1"])
        w.writerow([tp, fp, tn, fn, f"{acc:.4f}", f"{prec:.4f}", f"{rec:.4f}", f"{f1:.4f}"])
    print(f"TP={tp} FP={fp} TN={tn} FN={fn}  acc={acc:.4f} prec={prec:.4f} rec={rec:.4f} F1={f1:.4f}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
