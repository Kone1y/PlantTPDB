#!/usr/bin/env python3
"""rescore_dense.py — no-reranking (bi-encoder dense ranking) arm, frozen 240-question set.
Ranks each deduplicated candidate pool by bge-embedding-large-en cosine, scores the top-18
window with ../final_scoring/score.py; writes results/dense_arm_per_question.json.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "final_scoring"))
from score import canon, score  # canonical scoring formulas


def pool(candidates):
    sel = [c for c in candidates if f"{c['route']}:{c['qv']}" in {"VEC:rw", "BM25G:rw", "FULL:aug"}]
    sel = sorted(sel, key=lambda x: x["seq"])
    seen, dd = set(), []
    for c in sel:
        if c["id"] not in seen:
            seen.add(c["id"])
            dd.append({"id": c["id"], "doi": canon((c.get("doi") or "").strip().lower())})
    return dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--caches", nargs="+", required=True)
    ap.add_argument("--vdb", required=True)
    ap.add_argument("--release-root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    ROOT = Path(args.release_root)

    qrels = json.load(open(ROOT / "data/qrels/chunk_level_qrels.json",
                           encoding="utf-8"))["qrels"]
    ev = {q["qid"]: q for q in json.load(
        open(ROOT / "data/evidence_windows/evidence_windows.json",
             encoding="utf-8"))["questions"]}

    pools, symbols = {}, {}
    for path in args.caches:
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            if rec["qid"] in qrels:
                pools[rec["qid"]] = pool(rec["candidates"])
                symbols[rec["qid"]] = rec["symbol"]
    print("questions with candidate pools:", len(pools))
    assert len(pools) == 240, "expected exactly the frozen 240 set"

    import chromadb
    from chromadb.config import Settings
    con = chromadb.PersistentClient(path=args.vdb, settings=Settings(anonymized_telemetry=False))
    col = con.get_collection("papers_rag_md")
    need = sorted({c["id"] for dd in pools.values() for c in dd})
    emb, texts = {}, {}
    for i in range(0, len(need), 400):
        part = col.get(ids=need[i:i + 400], include=["documents", "embeddings"])
        for cid, doc, e in zip(part["ids"], part["documents"], part["embeddings"]):
            texts[cid] = doc or ""
            emb[cid] = e
    print("chunks fetched:", len(texts))

    import numpy as np
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(args.model, device="cuda:0")
    oqs = sorted(pools)
    queries = ["Target symbol/gene: %s\nQuestion: %s" % (symbols[o], ev[o]["question"]) for o in oqs]
    qemb = model.encode(queries, normalize_embeddings=True, convert_to_numpy=True)

    out = {}
    for i, oq in enumerate(oqs):
        dd = pools[oq]
        E = np.stack([np.asarray(emb[c["id"]], dtype=np.float32) for c in dd])
        E = E / np.linalg.norm(E, axis=1, keepdims=True)
        order = np.argsort(-(E @ qemb[i]))[:18]
        window = [{"rank": r + 1, "doi": dd[j]["doi"], "text": texts.get(dd[j]["id"], "")}
                  for r, j in enumerate(order)]
        members = [{"doi": m["doi"], "text": m["text"]} for m in qrels[oq]["chunks"]]
        out[oq] = score(members, window)

    KEYS = ["MRR", "R@5", "R@18", "P@5", "P@18", "nDCG@10"]
    means = {k: round(sum(v[k] for v in out.values()) / len(out), 3) for k in KEYS}
    print("dense-arm means (Table S19 Panel B):", json.dumps(means))
    expected = {"MRR": 0.732, "R@5": 0.218, "R@18": 0.547, "P@5": 0.472, "P@18": 0.339, "nDCG@10": 0.462}  # reference row = archived results/dense_arm_per_question.json (= Table S19 Panel B; asymmetric identity rule)
    json.dump({"arm": "no-reranking (bi-encoder dense ranking)", "n_questions": len(out),
               "means": means, "reference_means": expected,
               "note": ("Query texts are the frozen 240-question set verbatim (evidence_windows.json); "
                        "candidate pools are the archived caches; scoring formulas identical to score.py."),
               "per_question": out},
              open(ROOT / "results" / "dense_arm_per_question.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    worst = max(abs(means[k] - expected[k]) for k in KEYS)
    print("max |diff| vs published row:", round(worst, 4))
    if worst > 0.001:
        raise SystemExit("reproduction out of tolerance")
    print("wrote results/dense_arm_per_question.json")


if __name__ == "__main__":
    sys.exit(main())
