#!/usr/bin/env python3
"""dataset_stats.py — headline statistics of Supplementary Tables 20 and 21, recomputed
from the archived benchmark datasets."""
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]


def pct1(k, n):
    """Ratio as a percentage string with 1 decimal, computed exactly and
    rounded half-up (e.g. 99/240 = 41.25 -> '41.3'), avoiding binary-float
    ties rounding down."""
    return str((Decimal(k) / Decimal(n) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def exact_ci(k, n):
    z = 1.959964
    den = 1 + z * z / n
    c = k / n + z * z / (2 * n)
    h = z * math.sqrt((k / n) * (1 - k / n) / n + z * z / (4 * n * n))
    return max(0.0, (c - h) / den), min(1.0, (c + h) / den)


def claim_support():
    ws = openpyxl.load_workbook(
        ROOT / "data/claims/claim_adjudication_record.xlsx",
        read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c) for c in rows[0]]
    ci = {h: i for i, h in enumerate(hdr)}
    n = sup = 0
    bad_q, all_q = set(), set()
    for r in rows[1:]:
        q = str(r[ci["QID"]])
        all_q.add(q)
        n += 1
        if str(r[ci["Final label"]]) == "Supported":
            sup += 1
        else:
            bad_q.add(q)
    print(f"[S20] claims: {n} total; supported {sup} ({pct1(sup, n)}%); "
          f"not supported {n-sup} ({pct1(n-sup, n)}%)")
    lo, hi = exact_ci(len(bad_q), len(all_q))
    print(f"[S20] answers with >=1 not-supported claim: {len(bad_q)}/{len(all_q)} "
          f"({pct1(len(bad_q), len(all_q))}%; 95% CI {100*lo:.1f}-{100*hi:.1f})")


def judge_agreement():
    """S20 (automated-evaluator comparison panel): RAGAS automated
    evidence-support evaluation vs human labels, recomputed from the
    archived per-claim verdicts (all 3,534 claims)."""
    import csv
    rows = list(csv.DictReader(open(
        ROOT / "results/ragas_full_comparison_per_claim.csv",
        encoding="utf-8-sig")))
    h_sup = [r for r in rows if r["human_label"] == "supported"]
    h_ns = [r for r in rows if r["human_label"] == "not supported"]
    a_sup = sum(1 for r in h_sup if r["ragas_verdict"] == "supported")
    a_ns = sum(1 for r in h_ns if r["ragas_verdict"] == "not supported")
    tot = a_sup + a_ns
    print(f"[S20 judge] all claims {len(rows)}; agreement {tot} "
          f"({pct1(tot, len(rows))}%)")
    print(f"[S20 judge] supported {a_sup}/{len(h_sup)} ({pct1(a_sup, len(h_sup))}%); "
          f"not supported {a_ns}/{len(h_ns)} ({pct1(a_ns, len(h_ns))}%)")


def abstention():
    rows = [json.loads(l) for l in open(
        ROOT / "data/abstention/abstention_bench_final.jsonl",
        encoding="utf-8")]
    ans = [r for r in rows if r["reference_decision"] == "Answer"]
    abs_ = [r for r in rows if r["reference_decision"] == "Abstain"]
    ok_a = sum(1 for r in ans if r["system_action"] == "Answer")
    ok_b = sum(1 for r in abs_ if r["system_action"] == "Abstain")
    la, ha = exact_ci(ok_a, len(ans))
    lb, hb = exact_ci(ok_b, len(abs_))
    print(f"[S21] answerable answered {ok_a}/{len(ans)} (95% CI {100*la:.1f}-{100*ha:.1f}); "
          f"reference-abstain abstained {ok_b}/{len(abs_)} "
          f"({pct1(ok_b, len(abs_))}%; 95% CI {100*lb:.1f}-{100*hb:.1f}), answered {len(abs_)-ok_b}")


def answer_quality():
    ws = openpyxl.load_workbook(
        ROOT / "results/reference/answer_quality_claim_support.xlsx",
        read_only=True)["S20"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c) for c in rows[1]]
    ci = {h: i for i, h in enumerate(hdr)}
    comps = [[], [], []]
    means = []
    for r in rows[2:]:
        if r[0] and str(r[0]).startswith("Q"):
            row_scores = []
            for j in range(3):
                v = float(r[ci[f"E{j+1} Composite"]])
                comps[j].append(v)
                row_scores.append(v)
            means.append(sum(row_scores) / 3)
    n = len(means)
    grand = sum(means) / n
    k = 3
    col_means = [sum(c) / n for c in comps]
    ss_t = sum((x - grand) ** 2 for c in comps for x in c)
    ss_r = k * sum((m - grand) ** 2 for m in means)
    ss_c = n * sum((m - grand) ** 2 for m in col_means)
    ss_e = ss_t - ss_r - ss_c
    ms_r = ss_r / (n - 1)
    ms_c = ss_c / (k - 1)
    ms_e = ss_e / ((n - 1) * (k - 1))
    icc = (ms_r - ms_e) / (ms_r + (k - 1) * ms_e + k * (ms_c - ms_e) / n)
    var = sum((m - grand) ** 2 for m in means) / (len(means) - 1)
    se = math.sqrt(var / len(means))
    print(f"[S20] mean composite quality score {grand:.1f}/100 (95% CI {grand-1.959963984540054*se:.1f}-"
          f"{grand+1.959963984540054*se:.1f}); ICC(2,1) {icc:.3f}")


if __name__ == "__main__":
    claim_support()
    judge_agreement()
    abstention()
    answer_quality()
