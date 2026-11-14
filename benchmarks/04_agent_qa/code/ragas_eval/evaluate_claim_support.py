#!/usr/bin/env python3
"""Compare PRE-EXTRACTED atomic claims with human gold using RAGAS Faithfulness NLI only.

Input: JSONL, one claim per row, with:
  claim_id, qid, claim_text, human_label,
  and either contexts OR database_contexts + literature_contexts.
Human labels: SUPPORTED or NOT_SUPPORTED (case insensitive; aliases accepted).

IMPORTANT: This script bypasses RAGAS's statement-generation step. It does not
reproduce a five-class custom judge, and verdict 0 includes all non-entailed
claims (whether unsupported or contradicted by supplied contexts).

RAGAS collections API source:
  ragas.metrics.collections.Faithfulness
  ragas.metrics.collections.faithfulness.util.NLIStatementInput/NLIStatementOutput

These helper models are not guaranteed stable across releases: pin the RAGAS
version recorded in evaluation_manifest.json before archiving.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict, deque
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(obj: Any) -> str:
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def gold_label(raw: Any) -> int:
    s = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    yes = {"SUPPORTED", "SUPPORT", "1", "TRUE"}
    no = {"NOT_SUPPORTED", "UNSUPPORTED", "NOT_ENOUGH_INFO", "CONTRADICTED", "0", "FALSE"}
    if s in yes:
        return 1
    if s in no:
        return 0
    # Do not silently convert NON_FACTUAL, QUALIFIED_INFERENCE or NOT_ASSESSABLE.
    raise ValueError(f"unrecognized/non-binary human gold label: {raw!r}")


def context_list(data: Any) -> list[str]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("context must be a list of strings or JSON objects")
    out = []
    for x in data:
        s = x if isinstance(x, str) else stable_json(x)
        if s.strip():
            out.append(s.strip())
    return out


def read_input(path: Path, include_reference: bool) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            r = json.loads(line)
            claim_id = str(r["claim_id"]).strip()
            qid = str(r.get("qid", claim_id.split("|")[0])).strip()
            claim = str(r["claim_text"]).strip()
            if not claim or not claim_id or claim_id in seen:
                raise ValueError(f"bad/duplicate claim_id or empty claim at line {line_no}: {claim_id}")
            seen.add(claim_id)
            if "contexts" in r:
                contexts = context_list(r["contexts"])
            else:
                db = context_list(r.get("database_contexts", []))
                lit = context_list(r.get("literature_contexts", []))
                contexts = ([f"[DB{i}] {x}" for i, x in enumerate(db, 1)]
                            + [f"[LIT{i}] {x}" for i, x in enumerate(lit, 1)])
            if include_reference:
                reference = str(r.get("reference_answer", "")).strip()
                if reference:
                    contexts.append("[REF] " + reference)
            if not contexts:
                raise ValueError(f"missing frozen evidence for {claim_id} at line {line_no}")
            rows.append({"claim_id": claim_id, "qid": qid, "claim_text": claim,
                         "human_label": gold_label(r["human_label"]),
                         "contexts": contexts, "evidence_hash": sha(contexts)})
    if not rows:
        raise ValueError("input has no claims")
    return rows


def make_groups(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    """Batch only rows sharing question ID AND identical evidence snapshots."""
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r["qid"], r["evidence_hash"])].append(r)
    return [members[i:i + batch_size]
            for members in grouped.values()
            for i in range(0, len(members), batch_size)]


def validate_verdicts(batch: list[dict[str, Any]], result: Any) -> list[dict[str, Any]]:
    """Never align model verdicts to IDs by position unless statement texts really match."""
    statements = result.statements
    if len(statements) != len(batch):
        raise ValueError(f"RAGAS returned {len(statements)} verdicts for {len(batch)} claims")
    bins = defaultdict(deque)
    for s in statements:
        text = str(s.statement)
        if type(s.verdict) is not int or s.verdict not in (0, 1):
            raise ValueError(f"nonbinary RAGAS verdict: {s.verdict!r}")
        bins[text].append(s.verdict)
    if Counter(r["claim_text"] for r in batch) != Counter(s.statement for s in statements):
        raise ValueError("RAGAS returned changed/unmatched statements; refusing misaligned labels")
    return [bins[r["claim_text"]].popleft() for r in batch]


async def evaluate_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    # Delayed imports permit --dry-run and offline smoke tests without dependencies or API key.
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness
    from ragas.metrics.collections.faithfulness.util import NLIStatementInput, NLIStatementOutput

    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY environment variable is required")
    client = AsyncOpenAI(api_key=key, base_url=args.base_url, timeout=args.timeout)
    llm = llm_factory(args.model, client=client, temperature=0.0, max_tokens=args.max_tokens)
    metric = Faithfulness(llm=llm)
    gate = asyncio.Semaphore(args.workers)

    async def invoke(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Exactly RAGAS Faithfulness's NLI phase; skip statement generation.
        item = NLIStatementInput(
            context="\n\n".join(batch[0]["contexts"]),
            statements=[r["claim_text"] for r in batch],
        )
        prompt = metric.nli_statement_prompt.to_string(item)
        result = await llm.agenerate(prompt, NLIStatementOutput)
        return validate_verdicts(batch, result)

    async def process(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with gate:
            failure = ""
            for attempt in range(args.retries):
                try:
                    verdicts = await invoke(batch)
                    return [{**r, "ragas_verdict": v, "error": ""} for r, v in zip(batch, verdicts)]
                except Exception as e:
                    failure = f"{type(e).__name__}: {e}"
                    if attempt + 1 < args.retries:
                        await asyncio.sleep(min(2 ** attempt, 8))
            # An invalid multi-statement reply should not invalidate unrelated rows.
            if len(batch) > 1:
                out = []
                for r in batch:
                    try:
                        verdict = await invoke([r])
                        out.append({**r, "ragas_verdict": verdict[0], "error": ""})
                    except Exception as e:
                        out.append({**r, "ragas_verdict": None,
                                    "error": f"{type(e).__name__}: {e}"})
                return out
            return [{**batch[0], "ragas_verdict": None, "error": failure}]

    try:
        chunks = make_groups(rows, args.batch_size)
        outputs = await asyncio.gather(*(process(chunk) for chunk in chunks))
        merged = {r["claim_id"]: r for batch_out in outputs for r in batch_out}
        if len(merged) != len(rows):
            raise RuntimeError("claim IDs were lost/duplicated while merging outputs")
        return [merged[r["claim_id"]] for r in rows]
    finally:
        await client.close()


def safe_div(a: int, b: int) -> float | None:
    return round(a / b, 6) if b else None


def calculate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("ragas_verdict") in (0, 1) and not r.get("error")]
    gold_s_pred_s = sum(r["human_label"] == 1 and r["ragas_verdict"] == 1 for r in scored)
    gold_s_pred_ns = sum(r["human_label"] == 1 and r["ragas_verdict"] == 0 for r in scored)
    gold_ns_pred_s = sum(r["human_label"] == 0 and r["ragas_verdict"] == 1 for r in scored)
    gold_ns_pred_ns = sum(r["human_label"] == 0 and r["ragas_verdict"] == 0 for r in scored)
    assert gold_s_pred_s + gold_s_pred_ns + gold_ns_pred_s + gold_ns_pred_ns == len(scored)
    agree = gold_s_pred_s + gold_ns_pred_ns
    metrics = {
        "input_claims": len(rows), "scored_claims": len(scored),
        "failed_claims": len(rows) - len(scored),
        "confusion_matrix": {
            "human_supported__ragas_supported": gold_s_pred_s,
            "human_supported__ragas_not_supported": gold_s_pred_ns,
            "human_not_supported__ragas_supported": gold_ns_pred_s,
            "human_not_supported__ragas_not_supported": gold_ns_pred_ns,
        },
        "overall_agreement": safe_div(agree, len(scored)),
        "supported_recall": safe_div(gold_s_pred_s, gold_s_pred_s + gold_s_pred_ns),
        "not_supported_recall": safe_div(gold_ns_pred_ns, gold_ns_pred_s + gold_ns_pred_ns),
        "not_supported_precision": safe_div(gold_ns_pred_ns, gold_s_pred_ns + gold_ns_pred_ns),
    }
    return metrics


def save_results(rows: list[dict[str, Any]], input_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    details = out / "per_claim.csv"
    fields = ["claim_id", "qid", "human_label", "ragas_verdict", "same_decision", "evidence_hash", "error"]
    with details.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            pred = r.get("ragas_verdict")
            writer.writerow({
                "claim_id": r["claim_id"], "qid": r["qid"],
                "human_label": "supported" if r["human_label"] else "not supported",
                "ragas_verdict": ("supported" if pred == 1 else "not supported" if pred == 0 else ""),
                "same_decision": ("1" if pred == r["human_label"] else "0") if pred is not None else "",
                "evidence_hash": r["evidence_hash"], "error": r.get("error", ""),
            })

    summary = calculate(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    version = importlib.metadata.version("ragas") if not args.dry_run else "not-loaded"
    manifest = {
        "protocol": "pre-extracted atomic claims -> RAGAS Faithfulness NLI -> human gold comparison",
        "not_full_ragas_faithfulness": True,
        "ragas_version": version,
        "judge_model": args.model,
        "base_url": args.base_url,
        "temperature": 0.0,
        "batch_size": args.batch_size,
        "reference_as_evidence": args.include_reference,
        "input_file": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "no_claim_decomposition": True,
        "failure_policy": "failed/mismatched/invalid verdicts excluded from scoring and reported",
    }
    (out / "evaluation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="input JSONL, one atomic claim per line")
    parser.add_argument("--output", default="ragas_claim_eval", help="output folder")
    parser.add_argument("--model", default=os.getenv("EVAL_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--include-reference", action="store_true", help="ONLY if human labeling protocol uses reference as evidence")
    parser.add_argument("--dry-run", action="store_true", help="validate and count, without any API calls")
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1 or args.retries < 1:
        parser.error("workers, batch-size, retries must be >= 1")
    rows = read_input(args.input, args.include_reference)
    print(f"Read {len(rows)} unique claims in {len(make_groups(rows, args.batch_size))} batches")
    print(f"Gold: Supported={sum(r['human_label'] for r in rows)}, Not supported={sum(1-r['human_label'] for r in rows)}")
    if args.dry_run:
        print("Validation passed. No API calls and no report values were generated.")
        return 0
    result_rows = asyncio.run(evaluate_rows(rows, args))
    summary = save_results(result_rows, args.input, args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed_claims"]:
        print("ERROR: incomplete evaluation; inspect per_claim.csv before using summary", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
