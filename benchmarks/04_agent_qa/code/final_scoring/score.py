#!/usr/bin/env python3
"""score.py — canonical scoring for the frozen 240-question set.
Computes MRR / R@5 / R@18 / P@5 / P@18 / nDCG@10 from data/qrels/ and
data/evidence_windows/; writes results/per_question.json.
"""
import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 04_agent_qa/


def canon(doi: str) -> str:
    return doi.replace(":", "/") if doi.startswith("10.1023/") and ":" in doi else doi


_MD = re.compile(r"(\*\*|\*|##+|#+|\[|\]\([^)]*\)|`|>)")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", str(t or "")).lower()
    t = _MD.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()



def containment(gold: str, chunk: str) -> float:
    """Credit rule: 1.0 when the normalized retrieved text is equal to or
    fully contains the normalized gold text (the retrieved record must
    deliver the complete adjudicated gold passage); 0.0 otherwise."""
    g, r = _norm(gold), _norm(chunk)
    if not g or not r:
        return 0.0
    return 1.0 if (g == r or g in r) else 0.0


def one_to_one_matches(members, window):
    """Return {retrieved_rank: gold_index}, with one use of either endpoint.

    Keep the DOI/identity eligibility rule (same DOI; retrieved text equal to
    or fully containing the gold text). Process ranks in
    ascending order and use augmenting paths, rather than greedy pairing,
    to maximize matched gold members at every ranking prefix. Earlier
    matched ranks stay matched when their gold assignment is rearranged.
    """
    chunks = sorted(window, key=lambda c: c["rank"])
    ranks = [c["rank"] for c in chunks]
    if any(not isinstance(p, int) or isinstance(p, bool) or p < 1 for p in ranks):
        raise ValueError("Retrieved ranks must be positive integers")
    if len(set(ranks)) != len(ranks):
        raise ValueError("Retrieved ranks must be unique")

    eligible = [
        [j for j, gold in enumerate(members)
         if canon(gold["doi"]) == canon(chunk["doi"])
         and containment(gold["text"], chunk["text"]) == 1.0]
        for chunk in chunks
    ]
    gold_to_chunk = {}

    def augment(i, seen_gold):
        for j in eligible[i]:
            if j in seen_gold:
                continue
            seen_gold.add(j)
            if j not in gold_to_chunk or augment(gold_to_chunk[j], seen_gold):
                gold_to_chunk[j] = i
                return True
        return False

    for i in range(len(chunks)):
        augment(i, set())
    return {chunks[i]["rank"]: j for j, i in gold_to_chunk.items()}


def score(members, window):
    """members: [{doi, text}]; window: [{rank, doi, text}] -> six metrics."""
    nG = len(members)
    if not nG:
        return None
    rrp = sorted(one_to_one_matches(members, window))
    first = min(rrp) if rrp else None
    dcg = sum(1 / math.log2(p + 1) for p in rrp if p <= 10)
    idcg = sum(1 / math.log2(j + 1) for j in range(1, min(nG, 10) + 1))
    return {"MRR": round(1.0 / first, 3) if first else 0.0,
            "R@5": round(sum(1 for p in rrp if p <= 5) / nG, 3),
            "R@18": round(sum(1 for p in rrp if p <= 18) / nG, 3),
            "P@5": round(len({p for p in rrp if p <= 5}) / 5, 3),
            "P@18": round(len({p for p in rrp if p <= 18}) / 18, 3),
            "nDCG@10": round(dcg / idcg, 3) if idcg else 0.0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=ROOT,
                        help="Benchmark-04 directory containing data/")
    parser.add_argument("--output", type=Path,
                        help="Output JSON; defaults to results/per_question.json")
    args = parser.parse_args()
    root = args.release_root
    qrels = json.load(open(root / "data/qrels/chunk_level_qrels.json",
                           encoding="utf-8"))["qrels"]
    ev = {q["qid"]: q for q in json.load(
        open(root / "data/evidence_windows/evidence_windows.json",
             encoding="utf-8"))["questions"]}
    out = {}
    for qid, entry in qrels.items():
        win = ev.get(qid)
        if not win:
            continue
        window = [{"rank": c["rank"], "doi": c["doi"], "text": c["text"]} for c in win["window"]]
        members = [{"doi": m["doi"], "text": m["text"]} for m in entry["chunks"]]
        out[qid] = score(members, window)
    KEYS = ["MRR", "R@5", "R@18", "P@5", "P@18", "nDCG@10"]
    means = {k: round(sum(v[k] for v in out.values()) / len(out), 3) for k in KEYS}
    dest = args.output or root / "results" / "per_question.json"
    json.dump({"n_questions": len(out), "means": means, "per_question": out},
              open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("n =", len(out), "means =", json.dumps(means))
    print("wrote", dest)


if __name__ == "__main__":
    sys.exit(main())
