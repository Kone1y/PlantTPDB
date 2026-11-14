import json
import os
import copy
import threading
from contextlib import contextmanager
import re
import uuid

import requests
import numpy as np
import chromadb
from chromadb.config import Settings
from rank_bm25 import BM25Okapi
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from .config import LLM_client, VDB_PATH, EMBEDDING_MODEL_PATH, RERANKER_MODEL_PATH
from .tc_symbol_tem import TCCODE_WHITELIST, SYMBOL_WHITELIST
from .citation_enrich import (
    enrich_parenthetical_citations_with_ptdb_titles,
    link_symbol_or_pid_to_ptdb,
    wrap_bare_citations,
)
from .ptdb_utils import (
    filter_redundant_ptdb_index_link_chips,
    link_bare_tair_pids_in_text,
    ptdb_url_search,
    ptdb_url_search_index,
    ptdb_url_tc_code,
)
from .guard import inject_guard, unmatched_entity_guard, user_guard_prefix, detect_trap_type, looks_like_gene_name, intercept
from datetime import datetime
from typing import Callable

PTDB_API_CAPTURE = os.environ.get("PTDB_API_CAPTURE", "").strip()
_ptdb_call_log: list = []
_ptdb_call_lock = __import__("threading").Lock()

if PTDB_API_CAPTURE:
    _orig_requests_get = requests.get

    def _capturing_requests_get(url, **kwargs):
        if "yanglab.hzau.edu.cn/ptdb" not in str(url):
            return _orig_requests_get(url, **kwargs)
        entry = {"url": str(url), "params": kwargs.get("params") or {},
                 "ts": datetime.now().isoformat(), "status": None, "response": None}
        try:
            resp = _orig_requests_get(url, **kwargs)
            entry["status"] = getattr(resp, "status_code", None)
            try:
                entry["response"] = resp.json()
            except Exception:
                entry["response"] = None
            return resp
        except Exception as e:
            entry["error"] = repr(e)
            raise
        finally:
            with _ptdb_call_lock:
                _ptdb_call_log.append(entry)

    requests.get = _capturing_requests_get


def drain_ptdb_calls() -> list:
    with _ptdb_call_lock:
        out = _ptdb_call_log[:]
        _ptdb_call_log.clear()
    return out

try:
    import torch
except Exception:
    torch = None


def _auto_device() -> str:
    forced = (os.getenv("RAG_DEVICE") or "").strip().lower()
    if forced in {"cpu", "cuda", "cuda:0", "cuda:1"}:
        return forced
    if torch is not None and torch.cuda.is_available():
        return "cuda"
    return "cpu"


_RAG_DEVICE = _auto_device()
print(f"[normal_rag] init device={_RAG_DEVICE}, CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '<unset>')}")

RAG_SUMMARY_TOP_CHUNKS = int(os.getenv("RAG_SUMMARY_TOP_CHUNKS", "18"))
RAG_LOCAL_QUERY_N = int(os.getenv("RAG_LOCAL_QUERY_N", "20"))


"""
Symbol extraction + external data aggregation + simple RAG orchestration.
To integrate more APIs later, just add a new fetch_xxx in this file and compose it in build_entity_evidence / run_normal_chat.
"""

def _export_prererank(question: str, candidates: list[dict], tag: str = "") -> None:
    outdir = (os.getenv("RAG_PRERERANK_EXPORT") or "").strip()
    if not outdir:
        return
    import hashlib
    import time as _time
    os.makedirs(outdir, exist_ok=True)
    qh = hashlib.md5((question or "").encode("utf-8")).hexdigest()[:10]
    rows = []
    for rank, c in enumerate(candidates, 1):
        meta = c.get("metadata") or {}
        rows.append({
            "rank_pre_rerank": rank,
            "chunk_id": c.get("id"),
            "doi": meta.get("DOI", ""),
            "section": meta.get("Section", ""),
            "text": (c.get("text") or "")[:2000],
            "other_fields": {k: v for k, v in c.items()
                             if k not in ("id", "text", "metadata", "rerank_score")},
        })
    path = os.path.join(outdir, f"prererank_{tag}_{int(_time.time())}_{qh}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"question": question, "tag": tag, "n": len(rows), "candidates": rows},
                  f, ensure_ascii=False)


# =====================================================
# Global singleton RAG resources (init once at import)
# =====================================================
DB_PATH = VDB_PATH
COLLECTION_NAME = "papers_rag_md"

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # /symbol/src
    "database",
    "cache",
)
NORMAL_CACHE_PATH = os.path.join(CACHE_DIR, "normal_cache.json")

_CHROMA_CLIENT = chromadb.PersistentClient(
    path=DB_PATH,
    settings=Settings(anonymized_telemetry=False),
)
_COLLECTION = _CHROMA_CLIENT.get_collection(COLLECTION_NAME)

try:
    _EMBEDDING_MODEL = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_PATH,
        model_kwargs={"device": _RAG_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )
except Exception as e:
    print(f"[normal_rag] Embedding model init failed on device={_RAG_DEVICE}: {e}")
    if _RAG_DEVICE != "cpu":
        print("[normal_rag] fallback Embedding model to cpu")
        _EMBEDDING_MODEL = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_PATH,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    else:
        raise

try:
    _RERANKER = CrossEncoder(
        RERANKER_MODEL_PATH,
        device=_RAG_DEVICE,
    )
except Exception as e:
    print(f"[normal_rag] CrossEncoder init failed on device={_RAG_DEVICE}: {e}")
    if _RAG_DEVICE != "cpu":
        print("[normal_rag] fallback CrossEncoder to cpu")
        _RERANKER = CrossEncoder(
            RERANKER_MODEL_PATH,
            device="cpu",
        )
    else:
        raise


def _call_qwen(messages, temperature: float = 0.2) -> str:
    resp = LLM_client(
        model="qwen3-32b",
        messages=messages,
        temperature=temperature,
        extra_body={"enable_thinking": False},
    )
    return (resp.choices[0].message.content or "").strip()


def _call_deepseek(question: str) -> str | None:
    question = (question or "").strip()
    if not question:
        return None
    try:
        resp = deepseek_LLM_client(
            model="qwen3-32b",
            messages=[
                {"role": "system", "content": "You are a helpful assistant in plant biology and membrane transporter research. Answer the user's question concisely and accurately based on your knowledge."},
                {"role": "user", "content": question},
            ],
            temperature=0.3,
        )
        content = (resp.choices[0].message.content or "").strip()
        return content if content else None
    except Exception as e:
        print("_call_deepseek error (will continue without DeepSeek reference):", e)
        return None


def _load_normal_cache(path: str | None = None) -> list[dict]:
    p = path or NORMAL_CACHE_PATH
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_normal_cache(items: list[dict], path: str | None = None) -> None:
    p = path or NORMAL_CACHE_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save normal_cache failed:", e)


def _normalize_question_for_cache(q: str) -> str:
    if not q:
        return ""
    s = str(q).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _find_normal_cached_answer(question: str, path: str | None = None) -> str | None:
    qn = _normalize_question_for_cache(question)
    if not qn:
        return None
    items = _load_normal_cache(path)
    for item in items:
        q = item.get("question_norm") or _normalize_question_for_cache(item.get("question", ""))
        if q == qn:
            ans = item.get("answer")
            if isinstance(ans, str) and ans.strip():
                return ans
    return None


def _append_normal_cache(question: str, answer: str, path: str | None = None) -> None:
    if not question or not answer:
        return
    items = _load_normal_cache(path)
    qn = _normalize_question_for_cache(question)
    items.append(
        {
            "id": uuid.uuid4().hex,
            "question": question,
            "question_norm": qn,
            "answer": answer,
        }
    )
    _save_normal_cache(items, path)


def _build_source_metadata(
    literature: list[dict] | None = None,
    api_calls: list[dict] | None = None,
    inference_notes: list[str] | None = None,
) -> dict:
    return {
        "literature": literature or [],
        "api_calls": api_calls or [],
        "inference_notes": inference_notes or [],
    }


def _enrich_api_calls_with_links(api_calls: list[dict]) -> list[dict]:
    if not api_calls:
        return []

    hidden_apis = {"deepseek_reference", "vector_bm25_rerank"}

    enriched_list = []
    for call in api_calls:
        enriched = dict(call)
        params = call.get("params") or {}
        api_name = call.get("api", "")

        if api_name in hidden_apis:
            continue

        if api_name == "ptdb_symbol_tc_doi" and params.get("symbol"):
            symbol = params["symbol"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_tc_symbol_doi_info?insymbol={symbol}"
        elif api_name == "ptdb_symbol_to_doi" and params.get("symbol"):
            symbol = params["symbol"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_doi_by_symbol?insymbol={symbol}"
        elif api_name == "ptdb_index" and params.get("pid"):
            pid = params["pid"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_base_info_by_id?inid={pid}"
        elif api_name == "ptdb_tccode":
            tc = params.get("tc") or params.get("tc_code")
            if tc:
                enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_tc_info?intc={tc}"
        elif api_name == "ptdb_symbol_index_tc" and params.get("symbol"):
            symbol = params["symbol"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_symbol_info?insymbol={symbol}"
        elif api_name == "ptdb_smallfamily" and params.get("family"):
            family = params["family"]
            enriched["raw_url"] = "https://yanglab.hzau.edu.cn/ptdb/Table/queryNeo4j.1"
        elif api_name == "doi_reconstruct" and params.get("doi"):
            doi = params["doi"]
            enriched["raw_url"] = f"https://doi.org/{doi}"
        elif api_name == "ptdb_doi_to_symbol" and params.get("doi"):
            doi = params["doi"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_symbol_by_doi?indoi={doi}"

        enriched_list.append(enriched)

    return enriched_list


def _extract_ai_inference_note(answer: str) -> str | None:
    marker = "AI Inference Note:"
    idx = answer.rfind(marker)
    if idx == -1:
        return None
    content = answer[idx + len(marker):].strip()
    for end_marker in ["__SOURCES_AND_EVIDENCE__", "__GUIDING_SUGGESTIONS__"]:
        end_idx = content.find(end_marker)
        if end_idx != -1:
            content = content[:end_idx].strip()
    return content if content else None


def _append_sources_evidence_marker(answer: str, source_metadata: dict) -> str:
    if not source_metadata:
        return answer

    enriched_api_calls = _enrich_api_calls_with_links(source_metadata.get("api_calls") or [])

    if not (source_metadata.get("literature") or enriched_api_calls):
        return answer

    cleaned_metadata = {
        "literature": source_metadata.get("literature") or [],
        "api_calls": enriched_api_calls,
        "inference_notes": [],
    }

    marker = "__SOURCES_AND_EVIDENCE__"
    guiding_marker = "__GUIDING_SUGGESTIONS__"

    metadata_json = json.dumps(cleaned_metadata, ensure_ascii=False)

    if guiding_marker in answer:
        parts = answer.split(guiding_marker, 1)
        return f"{parts[0].rstrip()}\n\n{marker}\n{metadata_json}\n\n{guiding_marker}{parts[1]}"
    else:
        return f"{answer.rstrip()}\n\n{marker}\n{metadata_json}"


def generate_guiding_suggestions(
    question: str,
    entity_type: str,
    entity_value: str | None,
    style: str = "broad",
    k: int = 3,
) -> list[dict]:
    q = (question or "").strip()
    et = (entity_type or "").strip()
    ev = (entity_value or "").strip() if entity_value else ""
    if not q:
        return []

    style = (style or "broad").strip().lower()
    if style not in {"broad", "narrow"}:
        style = "broad"

    try:
        k = int(k)
    except Exception:
        k = 3
    if k < 1:
        k = 1
    if k > 5:
        k = 5

    focus_entity = ev if ev else "NONE"

    prompt = f"""
You are generating suggested follow-up questions for a Plant Transport Protein QA chat UI.

Return ONLY a JSON array of objects. Each object MUST have exactly two string fields:
- "display": short label for a clickable chip (<= 80 characters)
- "input": the exact English question to fill into the input box

Context:
- Original user question: {q}
- Detected entity_type: {et}
- Detected entity_value: {focus_entity}
- Suggestion style: {style}
- Number of suggestions: {k}

Important: The system data only has four types — TCCODE (e.g. 2.A.1), index (e.g. AT2G23810.1, locus ID), symbol (e.g. NRT1.1, OsAAP3), and DOI (e.g. 10.1038/s41477-017-0022-8). Every suggested question must clearly involve one of these four (a specific TC code, a specific index, a specific symbol, or a specific DOI). Do NOT suggest generic or vague exploration.

Rules:
- Output JSON only. No markdown. No extra keys.
- All suggestions must stay in scope: plant transport proteins, and must mention or imply a concrete TCCODE, index, symbol, or DOI.
- If a SYMBOL (gene/protein name) is provided as entity_value (or inferred from context), EVERY suggested question must explicitly mention that SYMBOL in the text.
- If a DOI is provided as entity_value (or inferred from context), EVERY suggested question must clearly be about THAT SPECIFIC PAPER (for example, asking about its methods, key findings, limitations, or applications). The question may also explicitly include the DOI string, but it is not required.
- Do NOT invent new symbols or TC codes that were not given; reuse the provided entity_value whenever possible.
- Every suggestion must be a COMPLETE and SPECIFIC QUESTION, not a short phrase. Avoid vague prompts like "DMT transport mechanism" and instead ask for concrete aspects (e.g. substrates, conformational cycle, regulation, experimental evidence, phenotypes) in 1 full sentence.
- Each question should typically contain 15–35 English words and clearly state what the user will learn by clicking it (e.g. structural basis, substrate range, regulatory network, species context, or experimental methods).
- Avoid repeating the original question verbatim. Do NOT diverge to unrelated proteins, families, or topics.
- If style is "narrow": ONLY suggest follow-ups that keep the SAME entity_value (e.g. same symbol or same TC code or same index). No other entities.
- If style is "broad": suggest only follow-ups that stay tightly around the current entity_value (same symbol / same TC / same index). Do NOT suggest "other transport proteins" or unrelated families.
- If entity_value is NONE: suggest only questions that mention a concrete symbol, or a TC code, or an index (e.g. "What is the function of NRT1.1?", "Explain TC family 2.A.1").
"""

    raw = _call_qwen(
        [
            {"role": "system", "content": "Output JSON only. No explanations."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    try:
        text = (raw or "").strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            arr = json.loads(text[start : end + 1])
        else:
            arr = json.loads(text)
        if not isinstance(arr, list):
            return []

        out: list[dict] = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            display = it.get("display")
            inp = it.get("input")
            if isinstance(display, str) and isinstance(inp, str):
                d = display.strip()
                i = inp.strip()
                if d and i:
                    out.append({"display": d[:120], "input": i})
        return out[:k]
    except Exception as e:
        print("❌ generate_guiding_suggestions parse error:", e)
        return []


def _filter_symbol_suggestions(suggestions: list[dict], symbol: str) -> list[dict]:
    sym = (symbol or "").strip()
    if not sym:
        return suggestions or []
    sym_lower = sym.lower()
    out: list[dict] = []
    for it in suggestions or []:
        if not isinstance(it, dict):
            continue
        disp = str(it.get("display") or "")
        inp = str(it.get("input") or "")
        text = (disp + " " + inp).lower()
        if sym_lower in text:
            out.append({"display": disp, "input": inp})
    return out


def _dedupe_suggestion_chips(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        inp = str(it.get("input") or "").strip()
        disp = str(it.get("display") or "").strip()
        if not inp or inp in seen:
            continue
        seen.add(inp)
        out.append({"display": disp or inp, "input": inp})
    return out


def build_ptdb_quick_links(entities: dict) -> list[dict]:
    ents = entities or {}
    tcs = ents.get("TCCODE") or []
    indices = ents.get("index") or []
    symbols = ents.get("symbol") or []
    families = ents.get("protein_family") or []

    links: list[dict] = []

    if symbols:
        s = str(symbols[0]).strip()
        if s:
            links.append(
                {
                    "display": f"Open PTDB symbol page for {s}",
                    "input": ptdb_url_search_index(s),
                }
            )

    if indices:
        pid = str(indices[0]).strip()
        if pid:
            links.append(
                {
                    "display": f"Open PTDB index page for {pid}",
                    "input": ptdb_url_search(pid),
                }
            )

    if tcs:
        tc = str(tcs[0]).strip()
        if tc:
            links.append(
                {
                    "display": f"Open PTDB TC page for {tc}",
                    "input": ptdb_url_tc_code(tc),
                }
            )

    if families:
        fam = str(families[0]).strip()
        if fam:
            links.append(
                {
                    "display": f"Open PTDB family page for {fam}",
                    "input": ptdb_url_search_index(fam),
                }
            )

    return _dedupe_suggestion_chips(links)


def _ensure_ptdb_links_in_cached_answer(question: str, cached_answer: str) -> str:
    return cached_answer


def simple_tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return [t for t in text.split() if t.strip()]


# ============================================================
# Corpus-wide raw-BM25 fallback route (process-level singleton)
# ============================================================
_FULL_BM25 = None
_FULL_IDS: list[str] = []
_FULL_DOCS: list[str] = []
_FULL_METAS: list[dict] = []


def _get_full_bm25():
    global _FULL_BM25, _FULL_IDS, _FULL_DOCS, _FULL_METAS
    if _FULL_BM25 is None:
        base = _COLLECTION.get(include=["documents", "metadatas"])
        _FULL_IDS = base["ids"] or []
        _FULL_DOCS = base["documents"] or []
        _FULL_METAS = base["metadatas"] or []
        _FULL_BM25 = BM25Okapi([simple_tokenize(d) for d in _FULL_DOCS])
        print(f"[normal_rag] full-corpus BM25 index built: {len(_FULL_DOCS)} chunks")
    return _FULL_BM25


def _bm25_full_route(question: str, top_k: int | None = None) -> list[dict]:
    bm25 = _get_full_bm25()
    scores = bm25.get_scores(simple_tokenize(question))
    topk = np.argsort(scores)[::-1][: (top_k or RAG_LOCAL_QUERY_N)]
    out = []
    for idx in topk:
        if scores[idx] <= 0:
            break
        out.append({"id": _FULL_IDS[idx], "text": _FULL_DOCS[idx], "metadata": _FULL_METAS[idx]})
    return out


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items or []:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def normalize_doi(doi: str) -> str:
    s = (doi or "").strip()
    if not s:
        return s
    if re.match(r"^10/\d{4,9}/", s):
        s = "10." + s[3:]
    return s


def extract_doi_from_text(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\b10[./](\d{4,9}/[^\s<>()\"']+)", t, flags=re.IGNORECASE)
    if not m:
        return None
    raw = "10." + m.group(1) if t[m.start() : m.start() + 3].lower() == "10/" else "10." + m.group(1) if "10/" in m.group(0) else m.group(0)
    return normalize_doi(raw)


def strip_doi_from_text(text: str, doi: str) -> str:
    t = (text or "").strip()
    if not t or not doi:
        return text or ""
    nd = normalize_doi(doi)
    if not nd:
        return text or ""

    pattern_paren = r"\(\s*DOI\s+" + re.escape(nd) + r"\s*\)"
    t = re.sub(pattern_paren, "", t, flags=re.IGNORECASE)

    pattern_doi = r"\b" + re.escape(nd) + r"\b"
    t = re.sub(pattern_doi, "", t)

    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def build_reference_summary_suggestions(dois: list[str], max_k: int = 2) -> list[dict]:
    if not dois:
        return []
    normed = []
    for d in dois:
        nd = normalize_doi(str(d).strip())
        if nd:
            normed.append(nd)
    normed = dedupe_preserve_order(normed)
    if not normed:
        return []

    title_map: dict[str, str] = {}
    try:
        title_map = fetch_titles_by_dois(normed[: max_k], timeout=30)
    except Exception:
        title_map = {}

    chips: list[dict] = []
    for doi in normed[: max_k]:
        title = (title_map.get(doi) or "").strip()
        display = f"Summarize: {title}" if title else f"Summarize: {doi}"
        input_q = (
            f"Please summarize the paper with DOI {doi}. "
            "Briefly describe its biological question, key methods, main findings, and conclusions in plant transporter research."
        )
        chips.append({"display": display, "input": input_q})
    return chips


def _append_reference_summary_suggestions(answer: str, dois: list[str], extra_chips: list[dict] | None = None) -> str:
    chips = build_reference_summary_suggestions(dois or [])
    extras = filter_redundant_ptdb_index_link_chips(answer, list(extra_chips or []))
    if extras:
        chips = _dedupe_suggestion_chips((chips or []) + extras)
    if not chips:
        return answer
    return answer.rstrip() + "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(chips, ensure_ascii=False)


def reconstruct_and_summarize_paper_by_doi(
    user_question: str,
    doi: str,
    stream_handler: Callable[[str], None] | None = None,
) -> str:
    doi = normalize_doi(str(doi or "").strip())
    if not doi:
        return "Missing DOI."

    if (re.search(r'(?i)9999|00000|not.a.real|replace.with|example', doi)
            or not re.match(r'^10\.\d{4,9}/\S+$', doi)):
        return (
            f"The DOI '{doi}' is not a valid publication identifier. "
            "Please provide a real DOI. Only plant transporter-related papers "
            "indexed in the PlantTP Agent database can be summarized."
        )

    collection = _COLLECTION
    try:
        res = collection.get(where={"DOI": doi}, include=["documents", "metadatas"])
    except Exception as e:
        print("reconstruct_and_summarize_paper_by_doi collection.get error:", e)
        return "Failed to fetch paper chunks by DOI from the vector database."

    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    if not docs:
        return (
            f"No paper with DOI '{doi}' was found in the PlantTP Agent database. "
            "Please verify the DOI or try a different paper."
        )

    items = []
    for i in range(min(len(docs), len(metas))):
        meta = metas[i] or {}
        sec = str(meta.get("Section") or "").strip()
        items.append((sec, str(i), str(docs[i] or "")))
    items.sort(key=lambda x: (x[0], x[1]))

    max_chars = 120_000
    parts: list[str] = [f"DOI: {doi}", ""]
    seen_sections: set[str] = set()
    used = 0
    for sec, _id, txt in items:
        block = (txt or "").strip()
        if not block:
            continue
        header = ""
        if sec and sec not in seen_sections:
            seen_sections.add(sec)
            header = f"\n\n## {sec}\n"
        candidate = (header + block + "\n")
        if used + len(candidate) > max_chars:
            break
        parts.append(candidate)
        used += len(candidate)

    reconstructed = "".join(parts).strip()

    ptdb_titles = fetch_titles_by_dois([doi], timeout=30)
    paper_title = (ptdb_titles.get(doi) or "").strip()
    title_meta = f"\nPaper title from PTDB (optional to show next to citations): {paper_title}\n" if paper_title else ""

    defer_stream = stream_handler is not None

    prompt = f"""
You are summarizing ONE specific academic paper provided below as reconstructed text chunks from a vector database.

User request:
{user_question}
{title_meta}
Hard constraints:
- Use ONLY the provided reconstructed paper text. Do NOT use external knowledge.
- Write 2–4 coherent paragraphs in ENGLISH.
- Cover: (1) research question/background, (2) key methods/assays/data types, (3) principal findings/results, (4) conclusions/implications.
- If key details are missing from the reconstructed text, explicitly say what is missing.
- Cite frequently in-line: after every 2–3 sentences (or sooner if the source switches) that report content from the paper, append the citation in parentheses on the same line. Each citation MUST be either ({doi}) only, or if a PTDB paper title line appears above, ({doi}; concise exact or lightly shortened title). Never invent titles.
- Do NOT output a separate "References" or "References Source" section.

SENTENCE-LEVEL MARKERS (MANDATORY at end of every sentence):
- Append [LITERATURE-GROUNDED] when the sentence draws from the provided paper text (cite the DOI).
- Append [AI-inferred] when the sentence contains interpretation, extrapolation, or
  speculation beyond what is directly stated in the paper text. These sentences must also
  appear in the AI Inference Note and the Uncertainty section of the Sources & Evidence panel.
- When writing LaTeX-style math, always use \\( ... \\) and \\[ ... \\] delimiters instead of $...$.

Reconstructed paper text:
{reconstructed}
""".strip()

    messages = [
        {"role": "system", "content": inject_guard("You summarize a single paper strictly from the provided text.")},
        {"role": "user", "content": prompt},
    ]
    if stream_handler:
        stream = LLM_client(
            model="deepseek-v4-flash",
            messages=messages,
            temperature=0.2,
            stream=True,
            )
        pieces: list[str] = []
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if not delta:
                continue
            pieces.append(delta)
            if not defer_stream:
                stream_handler(delta)
        summary = "".join(pieces)
    else:
        summary = _call_qwen(messages, temperature=0.2)
    summary = summary or ""
    if not summary.strip():
        summary = "Failed to generate summary from reconstructed paper text."

    summary = wrap_bare_citations(summary)
    summary = enrich_parenthetical_citations_with_ptdb_titles(
        summary, ptdb_titles, normalize_doi=normalize_doi
    )
    summary = link_bare_tair_pids_in_text(summary)
    if defer_stream:
        stream_handler(summary)

    return summary


def fetch_titles_by_dois(dois: list[str], timeout: int = 60) -> dict[str, str]:
    cleaned = []
    for d in dois or []:
        nd = normalize_doi(str(d).strip())
        if nd:
            cleaned.append(nd)
    cleaned = dedupe_preserve_order(cleaned)
    if not cleaned:
        return {}

    try:
        indoi = ";".join(cleaned)
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_title_by_doi",
            params={"indoi": indoi},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out: dict[str, str] = {}
        if isinstance(data, list):
            for rec in data:
                if not isinstance(rec, dict):
                    continue
                doi = normalize_doi(str(rec.get("DOI") or "").strip())
                title = str(rec.get("Title") or "").strip()
                if doi and title:
                    out[doi] = title
        return out
    except Exception as e:
        print("❌ fetch_titles_by_dois error:", e)
        return {}


def rewrite_queries(question: str, k: int = 3) -> list[str]:
    prompt = f"""
You are a query rewriting model for academic document retrieval.

Given a question, generate {k} diverse rewritten search queries.
Each query should focus on a different aspect such as:
background, methodology, and experimental results.

Return ONLY a JSON array of strings.

Question:
{question}
"""

    resp = LLM_client(
        model="qwen3-32b",
        messages=[
            {"role": "system", "content": "You generate search queries for academic retrieval."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        extra_body={"enable_thinking": False},
    )

    content = (resp.choices[0].message.content or "").strip()
    try:
        queries = json.loads(content)
    except Exception:
        queries = [question]
    if not isinstance(queries, list):
        queries = [question]
    return queries[:k]


def extract_symbol(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        return {"symbol": "NONE", "raw": ""}

    system_prompt = (
        "You are an expert in plant molecular biology. "
        "Your ONLY job is to extract the SYMBOL / gene name mentioned in the user question. "
        "The symbol usually looks like short uppercase/mixture tokens such as 'OsAAP3', 'AtSUC2', 'NRT1.1', etc. "
        "If there is no clear symbol, return NONE."
    )

    few_shot = """
You MUST answer in STRICT JSON format:
{
  "symbol": "<SYMBOL or NONE>"
}

Rules:
- Only return ONE symbol string.
- If there are multiple, choose the most central one.
- If you are not sure or no symbol is mentioned, use "NONE".
- Do NOT explain.

Examples:

Q1: "What are the substrates transported by OsAAP3 in rice?"
-> {"symbol": "OsAAP3"}

Q2: "Please summarize the function of AtSUC2 under salt stress."
-> {"symbol": "AtSUC2"}

Q3: "Compare the roles of NRT1.1 and NRT2.1 in nitrate uptake."
-> {"symbol": "NRT1.1"}

Q4: "How do transport proteins regulate plant responses to drought?"
-> {"symbol": "NONE"}

Now extract the symbol from the following question.

Question:
"""

    user_content = few_shot + question

    raw = _call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
    )

    symbol = "NONE"
    try:
        text = raw.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
        else:
            obj = json.loads(text)
        sym = (obj.get("symbol") or "").strip()
        if sym:
            symbol = sym
    except Exception:
        pass

    return {"symbol": symbol or "NONE", "raw": raw}


# ========= Entity extraction (TCCODE / index / symbol / protein_family) =========

ENTITY_EXTRACTION_PROMPT = """You are an entity extraction model for a plant transporter question answering system.
A user query may contain the following five types of entities: TCCODE, index, symbol, protein_family, DOI.
Your task is to identify and extract all entities appearing in the input sentence and classify them into the correct categories, then return a structured result.

Entity type definitions and extraction rules:

TCCODE: Transporter classification code. Pattern: digits and dots (e.g., 2.A.1, 1.A.4). Extract when the user explicitly mentions a TC code.

index: Unique protein/gene identifier. Pattern: species prefix + locus (e.g., AT2G23810.1, Os01g0109300). Extract when the user mentions a specific accession or locus ID.

symbol: Gene or protein symbol used for literature mapping. Rule: ANY token that the user refers to as a specific gene/protein/transporter name. This includes:
- Short symbols (3+ letters): NRT, AZG, PIN, ABC
- Longer symbols: PIN1, SWEET11, NRT1.1, OsAAP3
- Do NOT exclude a token just because it is short; if the user is asking about "X" as a gene/protein, extract "X" as symbol.
- When in doubt between symbol and protein_family, prefer symbol if it looks like a standard gene symbol (uppercase/mixed case, alphanumeric).

protein_family: Small protein family or specific family member with species prefix. Pattern: species prefix + family name (e.g., OsAAP3, AtSWEET11, ZmPHT1;6). Use when the name clearly indicates a family or ortholog group.

DOI: Digital Object Identifier of a scientific article. Pattern always starts with \"10.\" followed by 4–9 digits, a slash, and any non-space characters (for example, 10.1038/s41477-017-0022-8). If the user provides a DOI with a wrong separator such as \"10/1038/...\", you MUST normalize it to the standard form with a dot: \"10.1038/...\". Extract only well-formed DOIs; if you are not sure, do not extract.

Return only one JSON object with five fields: TCCODE, index, symbol, protein_family, and DOI.
Each field must be an array of strings.
If no entity of a given type is found, return an empty array.
Output JSON only. Do not include any additional explanations.

Examples:
Input: "Explain the TC classification and mechanism of family 2.A.1."
Output: {
  "TCCODE": ["2.A.1"],
  "index": [],
  "symbol": [],
  "protein_family": [],
  "DOI": []
}

Input: "Summarize the biological roles of the amino acid transporter OsAAP3 in rice."
Output: {
  "TCCODE": [],
  "index": [],
  "symbol": [],
  "protein_family": ["OsAAP3"],
  "DOI": []
}

Output format:
{
  "TCCODE": [],
  "index": [],
  "symbol": [],
  "protein_family": [],
  "DOI": []
}
"""


def _dedupe_keep_order_strs(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _looks_like_symbol(name: str) -> bool:
    s = (name or "").strip()
    if not s:
        return False
    return any(ch.isupper() or ch.isdigit() for ch in s)


def extract_query_entities(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        return {"TCCODE": [], "index": [], "symbol": [], "protein_family": [], "DOI": []}

    raw = _call_qwen(
        [
            {"role": "system", "content": "You extract entities and output JSON only."},
            {
                "role": "user",
                "content": (
                    ENTITY_EXTRACTION_PROMPT
                    + "\n\nValid TCCODE values are restricted to the following whitelist. "
                    + "You MUST NOT output any TCCODE that is not exactly one of these strings:\n"
                    + TCCODE_WHITELIST
                    + "\n\nPreferred symbol values are from the following whitelist. "
                    + "If the user mentions a specific gene/protein symbol that is NOT in the whitelist "
                    + "but looks like a standard gene symbol (uppercase/mixed alphanumeric, 3+ chars, "
                    + "optionally with numbers/suffixes like ALMT1, NRT1.1, PIN2), "
                    + "you MUST include it in the symbol field instead of substituting a different symbol.\n"
                    + "Whitelist:\n"
                    + SYMBOL_WHITELIST + "\n"
                    + "\n\nInput sentence:\n"
                    + question
                ),
            },
        ],
        temperature=0.0,
    )

    empty = {"TCCODE": [], "index": [], "symbol": [], "protein_family": [], "DOI": []}
    try:
        text = (raw or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
        else:
            obj = json.loads(text)

        out: dict[str, list[str]] = {}
        for k in ["TCCODE", "index", "symbol", "protein_family", "DOI"]:
            v = obj.get(k, [])
            if v is None:
                out[k] = []
            elif isinstance(v, str):
                out[k] = [v.strip()] if v.strip() else []
            elif isinstance(v, list):
                cleaned: list[str] = []
                for it in v:
                    if isinstance(it, str) and it.strip():
                        cleaned.append(it.strip())
                out[k] = _dedupe_keep_order_strs(cleaned)
            else:
                out[k] = []

        symbols = out.get("symbol") or []
        if symbols:
            out["symbol"] = [s for s in symbols if _looks_like_symbol(s)]

        _NON_ENTITY_STOPWORDS = {
            "which", "what", "list", "identify", "planttpdb", "find", "based", "infer",
            "compare", "how", "many", "report", "show", "describe", "summarize",
            "explain", "provide", "combining", "synthesize", "integrate", "discuss",
            "is", "the", "a", "an", "of", "for", "in", "on", "to", "by", "from",
            "with", "and", "or", "not", "no", "yes", "none", "unknown",
        }
        for field in ("symbol", "index", "protein_family"):
            vals = out.get(field) or []
            if vals:
                out[field] = [v for v in vals if v.lower().strip() not in _NON_ENTITY_STOPWORDS]

        for field in ("symbol", "protein_family"):
            vals = out.get(field) or []
            if vals:
                refined: list[str] = []
                q_words = re.findall(r'\b[A-Za-z][A-Za-z0-9\.\-_;]{1,19}\b', question)
                for v in vals:
                    best = v
                    v_lower = v.lower()
                    for w in q_words:
                        if w.lower().startswith(v_lower) and len(w) > len(v):
                            best = w
                            break
                    refined.append(best)
                out[field] = _dedupe_keep_order_strs(refined)

        dois = out.get("DOI") or []
        if dois:
            cleaned_dois: list[str] = []
            for d in dois:
                nd = normalize_doi(str(d).strip())
                if not nd:
                    continue
                if re.match(r"^10\.\d{4,9}/\S+$", nd):
                    cleaned_dois.append(nd)
            out["DOI"] = _dedupe_keep_order_strs(cleaned_dois)

        return out
    except Exception:
        return empty


def extract_symbol_from_answer(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return "NONE"
    try:
        ents = extract_query_entities(text)
        symbols = ents.get("symbol") or []
        if symbols:
            return (symbols[0] or "").strip() or "NONE"
        families = ents.get("protein_family") or []
        if families:
            return (families[0] or "").strip() or "NONE"
        return "NONE"
    except Exception:
        return "NONE"


def smallfamily(family_name: str, timeout: int = 120) -> list:
    print("Entering small-family info fetch:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    family_name = (family_name or "").strip()
    if not family_name:
        return []
    try:
        resp = requests.post(
            "https://yanglab.hzau.edu.cn/ptdb/Table/queryNeo4j.1",
            headers={
                "accept": "application/json, text/javascript, */*; q=0.01",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            data={"inid": family_name, "intype": "name", "hops": "1"},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw_json = resp.json()
        rels = (raw_json.get("data") or {}).get("relationships") or []
        return rels
    except Exception as e:
        print("❌ smallfamily API error for protein_family:", family_name, "error:", e)
        return []


def _validate_entities_in_question(question: str, entities: dict) -> dict:
    qn = (question or "").lower()
    dropped = {}
    for key in ("symbol", "protein_family", "TCCODE"):
        keep, drop = [], []
        for e in entities.get(key) or []:
            (keep if str(e).strip().lower() in qn else drop).append(e)
        if drop:
            dropped[key] = drop
            entities[key] = keep
    keep, drop = [], []
    for e in entities.get("index") or []:
        locus = str(e).strip().lower().split(".")[0]
        (keep if locus and locus in qn else drop).append(e)
    if drop:
        dropped["index"] = drop
        entities["index"] = keep
    if dropped:
        print("⚠️ entity validation: dropped entities not present as substrings of the question:", json.dumps(dropped, ensure_ascii=False))
    return entities


def build_entity_evidence(question: str) -> dict:
    print("Entering entity extraction:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    entities = extract_query_entities(question)
    print("Finished entity extraction:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    entities = _validate_entities_in_question(question, entities)
    ent_symbols = entities.get("symbol") or []
    symbol = ((ent_symbols[0] or "").strip() if ent_symbols else "") or "NONE"
    families = entities.get("protein_family") or []
    if families:
        relationships = smallfamily(families[0])
    else:
        relationships = []
    print("✅ Extracted entities:", json.dumps(entities, ensure_ascii=False))

    return {
        "symbol": symbol,
        "raw": "",
        "relationships": relationships,
        "entities": entities,
    }


_LAST_RETRIEVED_CONTEXTS: list[str] = []

_LAST_RETRIEVED_CONTEXTS_META: list[dict] = []


def reset_last_retrieved_contexts() -> None:
    global _LAST_RETRIEVED_CONTEXTS, _LAST_RETRIEVED_CONTEXTS_META
    _LAST_RETRIEVED_CONTEXTS = []
    _LAST_RETRIEVED_CONTEXTS_META = []


_QA_CTX_LOCK = threading.Lock()


@contextmanager
def qa_evidence_scope():
    """Serialize the clear -> answer -> snapshot lifecycle of the shared
    retrieved-evidence globals. Concurrent QA requests queue end-to-end
    inside this scope. The holder is filled in `finally`, so the caller
    must read `ev["contexts"]` / `ev["meta"]` AFTER exiting the with-block.
    NOTE: functions called inside the scope (the retrieval writers) must
    NOT acquire _QA_CTX_LOCK themselves (non-reentrant -> deadlock)."""
    with _QA_CTX_LOCK:
        reset_last_retrieved_contexts()
        holder = {"contexts": [], "meta": []}
        try:
            yield holder
        finally:
            holder["contexts"] = list(_LAST_RETRIEVED_CONTEXTS)
            holder["meta"] = copy.deepcopy(_LAST_RETRIEVED_CONTEXTS_META)

def _filter_chunk_noise(text: str) -> str | None:
    t = (text or "").strip()
    if len(t) < 100:
        return None
    t_lower = t[:200].lower()
    noise_starts = [
        "acknowledgment", "acknowledgements", "acknowledgement",
        "author contributions", "author contribution",
        "competing interests", "conflict of interest",
        "data availability", "code availability",
        "supplementary", "supporting information",
        "references", "bibliography",
        "©", "all rights reserved", "springer nature",
        "this article is distributed under",
        "supplementary data", "additional file",
        "correspondence and requests", "correspondence to",
        "received:", "accepted:", "published:",
        "citation:", "how to cite", "recommended citation",
        "credit author statement", "credit authorship",
        "declarations", "ethics approval", "consent for publication",
        "publisher's note", "editor's note",
        "open access", "creative commons",
        "licensee", "license", "licence",
        "peer review", "editorial",
    ]
    for ns in noise_starts:
        if t_lower.startswith(ns):
            return None
    return t

def local_rag_chunks(question: str, relationships: list[dict], symbol_name: str | None = None) -> dict:
    print("Entering literature-chunk stage:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    dois: list[str] = []
    for r in relationships or []:
        source_paper = (r.get("properties") or {}).get("source_paper")
        if source_paper:
            dois.append(source_paper)
    dois = list(set(dois))
    if not dois:
        return {"chunks": [], "return_dois": []}

    # 3.2 Use global singleton resources (vector store / embedding / reranker)
    collection = _COLLECTION
    embedding_model = _EMBEDDING_MODEL
    reranker = _RERANKER

    # 3.5 Rewrite the query
    rewrite_qs = rewrite_queries(question, 3)

    all_candidates: list[dict] = []

    bm25_base = collection.get(
        where={"DOI": {"$in": dois}},
        include=["documents", "metadatas"],
    )
    docs = bm25_base.get("documents") or []
    metas = bm25_base.get("metadatas") or []
    ids = bm25_base.get("ids") or []
    bm25 = None
    if docs:
        tokenized_corpus = [simple_tokenize(d) for d in docs]
        bm25 = BM25Okapi(tokenized_corpus)

    # 3.6 Retrieval: vector + BM25
    for q in rewrite_qs:
        print("Rewritten query:", q)
        q_emb = embedding_model.embed_query(q)

        vec_res = collection.query(
            query_embeddings=[q_emb],
            n_results=RAG_LOCAL_QUERY_N,
            where={"DOI": {"$in": dois}},
            include=["documents", "metadatas"],
        )
        now = datetime.now()
        print("--------------------------------")
        print("Vector formatting time:", now.strftime("%Y-%m-%d %H:%M:%S"))
        print("--------------------------------")
        print("--------------------------------")
        if vec_res.get("ids") and len(vec_res["ids"][0]) > 0:
            for i in range(len(vec_res["ids"][0])):
                all_candidates.append(
                    {
                        "id": vec_res["ids"][0][i],
                        "text": vec_res["documents"][0][i],
                        "metadata": vec_res["metadatas"][0][i],
                    }
                )

        if bm25 is not None:
            scores = bm25.get_scores(simple_tokenize(q))
            topk = np.argsort(scores)[::-1][:RAG_LOCAL_QUERY_N]
            for idx in topk:
                all_candidates.append(
                    {
                        "id": ids[idx],
                        "text": docs[idx],
                        "metadata": metas[idx],
                    }
                )
        now = datetime.now()
        print("BM25 formatting time:", now.strftime("%Y-%m-%d %H:%M:%S"))
        print("--------------------------------")
        print("--------------------------------")

    all_candidates.extend(_bm25_full_route(question))

    # 3.7 Dedup
    uniq: dict[str, dict] = {}
    for c in all_candidates:
        uniq[c["id"]] = c
    candidates = list(uniq.values())
    if not candidates:
        print("No chunks recalled")
        return {"chunks": [], "return_dois": []}

    # 3.8 rerank
    _export_prererank(question, candidates, tag="local_rag")
    pairs = [[question, c["text"]] for c in candidates]
    scores = reranker.predict(pairs)
    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])
    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

    dbg_n = min(12, len(candidates))
    print(f"\n===== Retrieved Chunks (Top {dbg_n}, local_rag_chunks) =====")
    for i, c in enumerate(candidates[:dbg_n]):
        meta = c.get("metadata", {})
        doi = meta.get("DOI")
        section = meta.get("Section")
        score = c.get("rerank_score")
        text_preview = (c.get("text") or "").replace("\n", " ")[:300]
        print(f"[{i+1}] score={score:.4f} | DOI={doi} | Section={section}")
        print(f"     {text_preview}")
        print()

    top_k = min(RAG_SUMMARY_TOP_CHUNKS, len(candidates))
    final_chunks = candidates[:top_k]
    return_dois = dedupe_preserve_order(
        [(c.get("metadata") or {}).get("DOI") for c in final_chunks if (c.get("metadata") or {}).get("DOI")]
    )
    print("Finished literature-chunk stage:", datetime.now().strftime("%Y-%m-%d %H:%m:%S"))
    result = {"chunks": final_chunks, "return_dois": return_dois}
    global _LAST_RETRIEVED_CONTEXTS, _LAST_RETRIEVED_CONTEXTS_META
    _LAST_RETRIEVED_CONTEXTS = [c.get("text", "") for c in final_chunks if c.get("text")]
    _LAST_RETRIEVED_CONTEXTS_META = [
        {
            "text": c.get("text", ""),
            "doi": (c.get("metadata") or {}).get("DOI", ""),
            "section": (c.get("metadata") or {}).get("Section", ""),
            "rerank_score": c.get("rerank_score"),
        }
        for c in final_chunks if c.get("text")
    ]
    return result


def _fallback_pure_rag_answer(
    question: str,
    entity_type: str | None = None,
    entity_value: str | None = None,
    deepseek_answer: str | None = None,
    stream_handler: Callable[[str], None] | None = None,
    source_meta: dict | None = None,
) -> str:
    question = (question or "").strip()

    if source_meta is None:
        source_meta = _build_source_metadata()
    source_meta["api_calls"].append({
        "api": "unrestricted_rag",
        "params": {"entity_type": entity_type or "", "entity_value": entity_value or "", "question": question[:80]},
        "result_summary": "Running unrestricted vector RAG as fallback",
    })

    try:
        rag_res = simple_rag_chunks_unrestricted(question)
    except Exception as e:
        print("fallback pure RAG error:", e)
        return "Backend retrieval failed when running fallback RAG. Please try again later."

    chunks = (rag_res or {}).get("chunks") or []
    dois = (rag_res or {}).get("return_dois") or []
    source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs (unrestricted)"
    if not chunks:
        prefix = ""
        if entity_type == "symbol" and entity_value:
            prefix = f"Detected symbol/gene name: {entity_value}. "
        elif entity_type == "protein_family" and entity_value:
            prefix = f"Detected protein small family: {entity_value}. "
        elif entity_type == "index" and entity_value:
            prefix = f"Detected index: {entity_value}. "
        elif entity_type == "TCCODE" and entity_value:
            prefix = f"Detected TC code: {entity_value}. "
        return (
            prefix
            + "However, no relevant literature chunks could be retrieved from the unrestricted corpus. "
            "Please try a more specific or different question."
        )

    display_entity = entity_value or "NONE"
    idx_rec = None
    tc_rec = None
    if entity_type == "symbol" and entity_value:
        source_meta["api_calls"].append({
            "api": "ptdb_symbol_index_tc",
            "params": {"symbol": entity_value},
            "result_summary": "Querying symbol→index API for full record",
        })
        try:
            idx_rec, tc_rec = fetch_index_and_tc_from_symbol(entity_value)
            if idx_rec:
                source_meta["api_calls"][-1]["result_summary"] = f"Found record: GeneID={idx_rec.get('GeneID', '?')}"
            else:
                source_meta["api_calls"][-1]["result_summary"] = "No record found for symbol"
        except Exception:
            idx_rec, tc_rec = None, None
            source_meta["api_calls"][-1]["result_summary"] = "Error querying symbol→index API"
    try:
        result = summarize_index_tc_and_rag(
            question,
            display_entity,
            idx_rec,
            tc_rec,
            chunks,
            dois,
            deepseek_answer=deepseek_answer,
            stream_handler=stream_handler,
            source_metadata=source_meta,
        )
        if entity_type and entity_value:
            try:
                suggestions = generate_guiding_suggestions(
                    question=question,
                    entity_type=entity_type,
                    entity_value=entity_value,
                    style="broad",
                    k=3,
                )
            except Exception as e:
                print("generate_guiding_suggestions error (fallback pure RAG):", e)
                suggestions = []
            if suggestions:
                result += "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(suggestions, ensure_ascii=False)
        result = _append_sources_evidence_marker(result, source_meta)
        _append_normal_cache(question, result, NORMAL_CACHE_PATH)
        return result
    except Exception as e:
        print("summarize_index_tc_and_rag error (fallback pure RAG):", e)
        return (
            "Failed to run literature-only summarization in fallback RAG. "
            "Here are the top retrieved literature chunks (JSON):\n"
            + repr(chunks)
        )


def is_transporter_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False

    prompt = f"""
You are a classifier for a plant membrane / transporter protein QA system.

TASK:
Given an input question, decide whether its MAIN focus is on:
- plant membrane transport proteins or channels,
- transporter genes / symbols / protein families,
- TC classification families and their mechanisms,
- transport substrates, directions, or regulatory mechanisms of plant transporters,
- database-style annotation of such transport proteins.

If the question is mainly about other topics (e.g. general agronomy, crop management, human disease, general statistics, etc.) and does NOT clearly ask about a specific plant transporter, then it is OUT OF SCOPE.

Return STRICT JSON ONLY:
{{ "in_scope": true }}  if the question is in scope.
{{ "in_scope": false }} if the question is out of scope.

Input question:
{q}
"""

    raw = _call_qwen(
        [
            {"role": "system", "content": "You classify whether a question is in scope for plant transporter QA and output JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )

    try:
        text = (raw or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
        else:
            obj = json.loads(text)
        return bool(obj.get("in_scope"))
    except Exception:
        return True


def simple_rag_chunks_unrestricted(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        return {"chunks": [], "return_dois": []}

    collection = _COLLECTION
    embedding_model = _EMBEDDING_MODEL
    reranker = _RERANKER

    rewrite_qs = rewrite_queries(question, 3)
    all_candidates: list[dict] = []

    for q in rewrite_qs:
        q_emb = embedding_model.embed_query(q)
        vec_res = collection.query(
            query_embeddings=[q_emb],
            n_results=RAG_LOCAL_QUERY_N,
            include=["documents", "metadatas"],
        )
        if vec_res.get("ids") and len(vec_res["ids"][0]) > 0:
            for i in range(len(vec_res["ids"][0])):
                all_candidates.append(
                    {
                        "id": vec_res["ids"][0][i],
                        "text": vec_res["documents"][0][i],
                        "metadata": vec_res["metadatas"][0][i],
                    }
                )

    all_candidates.extend(_bm25_full_route(question))

    uniq: dict[str, dict] = {}
    for c in all_candidates:
        uniq[c["id"]] = c
    candidates = list(uniq.values())
    if not candidates:
        return {"chunks": [], "return_dois": []}

    _export_prererank(question, candidates, tag="unrestricted")
    pairs = [[question, c["text"]] for c in candidates]
    scores = reranker.predict(pairs)
    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])
    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

    top_k = min(RAG_SUMMARY_TOP_CHUNKS, len(candidates))
    final_chunks = candidates[:top_k]
    return_dois = dedupe_preserve_order(
        [(c.get("metadata") or {}).get("DOI") for c in final_chunks if (c.get("metadata") or {}).get("DOI")]
    )
    global _LAST_RETRIEVED_CONTEXTS, _LAST_RETRIEVED_CONTEXTS_META
    _LAST_RETRIEVED_CONTEXTS = [c.get("text", "") for c in final_chunks if c.get("text")]
    _LAST_RETRIEVED_CONTEXTS_META = [
        {
            "text": c.get("text", ""),
            "doi": (c.get("metadata") or {}).get("DOI", ""),
            "section": (c.get("metadata") or {}).get("Section", ""),
            "rerank_score": c.get("rerank_score"),
        }
        for c in final_chunks if c.get("text")
    ]
    return {"chunks": final_chunks, "return_dois": return_dois}


def fetch_index_info(protein_id: str, timeout: int = 120) -> dict | None:
    from .ptdb_utils import ptdb_index_inid_candidates

    protein_id = (protein_id or "").strip()
    if not protein_id:
        return None
    for inid in ptdb_index_inid_candidates(protein_id):
        try:
            resp = requests.get(
                "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_base_info_by_id",
                params={"inid": inid},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            print("❌ fetch_index_info error:", e, "inid=", inid)
    return None


def fetch_tc_info(tc_code: str, timeout: int = 120) -> dict | None:
    tc_code = (tc_code or "").strip()
    if not tc_code:
        return None
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_tc_info",
            params={"intc": tc_code},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return data
    except Exception as e:
        print("❌ fetch_tc_info error:", e)
        return None


def fetch_index_and_tc_from_symbol(symbol: str) -> tuple[dict | None, dict | None]:
    symbol = (symbol or "").strip()
    if not symbol or symbol == "NONE":
        return None, None
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_symbol_info",
            params={"insymbol": symbol},
            timeout=(30, 1800),
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None, None
        first = data[0] or {}
        pid = (first.get("GeneID") or first.get("AthID") or first.get("Arabidopsis_PID") or "").strip()
        if not pid:
            return None, None
        idx = first if "base_info" in first else fetch_index_info(pid, timeout=1800)
        if not idx:
            return None, None
        tc_code = (idx.get("base_info") or {}).get("tc") or (idx.get("family_info") or {}).get("tc_code")
        tc = fetch_tc_info(tc_code, timeout=1800) if tc_code else None
        return idx, tc
    except Exception as e:
        print("❌ fetch_index_and_tc_from_symbol error:", e)
        return None, None


def fetch_symbol_by_doi(doi: str, timeout: int = 60) -> list[str]:
    doi = (doi or "").strip()
    if not doi:
        return []
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_symbol_by_doi",
            params={"indoi": doi},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        if isinstance(data, str) and data.strip():
            return [data.strip()]
        return []
    except Exception as e:
        print("❌ fetch_symbol_by_doi error:", e)
        return []


def fetch_tc_symbol_doi_info(symbol: str, timeout: int = 120) -> dict | None:
    symbol = (symbol or "").strip()
    if not symbol or symbol == "NONE":
        return None
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_tc_symbol_doi_info",
            params={"insymbol": symbol},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rec = None
        if isinstance(data, list) and data:
            rec = data[0] or None
        elif isinstance(data, dict):
            rec = data
        if not isinstance(rec, dict) or not rec:
            return None

        return {
            "dois": rec.get("dois") or "",
            "tc_code": rec.get("tc_code") or "",
            "putative_substrates": rec.get("Putative_substrates") or rec.get("putative_substrates") or "",
            "superfamily": rec.get("superfamily") or "",
            "family": rec.get("family") or "",
            "description": rec.get("description") or "",
        }
    except Exception as e:
        print("❌ fetch_tc_symbol_doi_info error:", e)
        return None


def fetch_dois_by_symbol(symbol: str, timeout: int = 120) -> list[str]:
    symbol = (symbol or "").strip()
    if not symbol or symbol == "NONE":
        return []
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_doi_by_symbol",
            params={"insymbol": symbol},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        dois = data.get("dois") or []
        if isinstance(dois, list):
            cleaned = [str(d).strip() for d in dois if str(d).strip()]
        elif isinstance(dois, str) and dois.strip():
            cleaned = [s.strip() for s in dois.split(",") if s.strip()]
        else:
            cleaned = []
        return _dedupe_keep_order_strs(cleaned)
    except Exception as e:
        print("❌ fetch_dois_by_symbol error:", e)
        return []


def _parse_gff_segments(gff: str, feature: str = "TMhelix") -> list[tuple[int, int]]:
    s = (gff or "").strip()
    if not s:
        return []
    segs: list[tuple[int, int]] = []
    for tok in s.split(";"):
        t = tok.strip()
        if not t:
            continue
        parts = t.split("|")
        if len(parts) < 4:
            continue
        feat = parts[-3]
        if feat != feature:
            continue
        try:
            start = int(parts[-2])
            end = int(parts[-1])
        except Exception:
            continue
        if start > 0 and end >= start:
            segs.append((start, end))
    return segs


def format_index_record(index_rec: dict) -> str:
    rec = index_rec or {}
    base = rec.get("base_info") or {}
    fam = rec.get("family_info") or {}

    protein_id = (rec.get("Protein_ID") or rec.get("AthID") or base.get("pid") or "").strip()
    gene_id = (rec.get("GeneID") or "").strip()
    species = (rec.get("species") or base.get("speID") or "").strip()
    symbol = rec.get("Symbol")
    if isinstance(symbol, list):
        symbol = symbol[0] if symbol else None
    symbol = (str(symbol).strip() if symbol is not None else "") or "N/A"

    tc = (base.get("tc") or fam.get("tc_code") or "").strip()
    family = (fam.get("family") or "").strip()
    superfamily = (fam.get("superfamily") or "").strip()

    pfam = (base.get("tid_pfam") or base.get("seed_pfam") or "").strip()
    domain_info = rec.get("Domain_info") or {}
    pfam_name = (domain_info.get("pfamName") or "").strip()
    pfam_des = (domain_info.get("pfamDes") or "").strip()
    tc_pident = base.get("tc_pident")
    tc_evalue = (base.get("tc_evalue") or "").strip() if base.get("tc_evalue") is not None else ""

    deeptm_num = base.get("DeepTM_num")
    tmhmm_num = base.get("TMHMM2_num")
    deeptm_gff = (base.get("DeepTM_gff") or "").strip()
    tmhmm_gff = (base.get("TMHMM2_gff") or "").strip()
    deeptm_segs = _parse_gff_segments(deeptm_gff, "TMhelix")
    tmhmm_segs = _parse_gff_segments(tmhmm_gff, "TMhelix")

    loc = (base.get("DeepLoc2_Localizations") or "").strip()
    signals = (base.get("DeepLoc2_Signals") or "").strip()
    memout = base.get("DeepLoc2_memout")

    length = base.get("pep_prop_len")
    mw = base.get("pep_prop_MW")
    pi = base.get("pep_prop_PI")

    lines: list[str] = []
    lines.append("Protein-level summary (parsed from index API):")
    if protein_id or gene_id or species:
        lines.append(f"- ID: {protein_id or 'N/A'} | GeneID: {gene_id or 'N/A'} | Species: {species or 'N/A'} | Symbol: {symbol}")
    if tc or family or superfamily:
        lines.append(f"- TC: {tc or 'N/A'} | Family: {family or 'N/A'} | Superfamily: {superfamily or 'N/A'}")
    if pfam or tc_pident is not None or tc_evalue:
        lines.append(f"- Pfam: {pfam or 'N/A'} | TC pident: {tc_pident if tc_pident is not None else 'N/A'} | TC evalue: {tc_evalue or 'N/A'}")
    if pfam_name or pfam_des:
        lines.append(f"- Pfam domain(s): name={pfam_name or 'N/A'}, description={pfam_des or 'N/A'}")
    if pfam:
        lines.append("  [Pfam-domain-guide] The Pfam IDs above (e.g. PF00083) identify conserved protein domains. When answering, you should explain what each Pfam domain represents — its name, typical function, and why it is relevant to this transporter family. If a Pfam name/description is provided above, use it in your answer.")

    topo_bits: list[str] = []
    if deeptm_num is not None:
        topo_bits.append(f"DeepTM TM count: {deeptm_num}")
    if tmhmm_num is not None:
        topo_bits.append(f"TMHMM2 TM count: {tmhmm_num}")
    if topo_bits:
        lines.append("- Topology: " + " | ".join(topo_bits))
    if deeptm_segs:
        lines.append(f"- DeepTM TM segments: {deeptm_segs}")
    if tmhmm_segs:
        lines.append(f"- TMHMM2 TM segments: {tmhmm_segs}")

    if loc or signals or memout is not None:
        memout_str = f"{float(memout):.3f}" if isinstance(memout, (int, float)) else (str(memout).strip() if memout is not None else "")
        lines.append(f"- Localization: {loc or 'N/A'} | Signals: {signals or 'N/A'} | DeepLoc2 memout: {memout_str or 'N/A'}")

    props: list[str] = []
    if length is not None:
        props.append(f"Length: {length} aa")
    if isinstance(mw, (int, float)):
        props.append(f"MW: {mw/1000.0:.2f} kDa")
    elif mw is not None and str(mw).strip():
        props.append(f"MW: {str(mw).strip()}")
    if isinstance(pi, (int, float)):
        props.append(f"pI: {pi:.2f}")
    elif pi is not None and str(pi).strip():
        props.append(f"pI: {str(pi).strip()}")
    if props:
        lines.append("- Physicochemical: " + " | ".join(props))

    af3_list = rec.get("3d_alphafold_prediction_info")
    if isinstance(af3_list, list) and af3_list:
        af_parts: list[str] = []
        for af in af3_list:
            if not isinstance(af, dict):
                continue
            plddt = af.get("pLDDT")
            ptm = af.get("pTM")
            af_pid = af.get("PID") or af.get("pid") or ""
            bits: list[str] = []
            if af_pid:
                bits.append(f"PID={af_pid}")
            if plddt is not None:
                bits.append(f"pLDDT={plddt}")
            if ptm is not None:
                bits.append(f"pTM={ptm}")
            if bits:
                af_parts.append("(" + " | ".join(bits) + ")")
        if af_parts:
            lines.append("- AlphaFold 3D prediction: " + " ".join(af_parts))
            lines.append("  (pLDDT: >90 high confidence, 70-90 moderate, <70 low; pTM: >0.5 indicates reliable overall topology)")

    blast_with_pdb = rec.get("Blast_with_pdb_info")
    pdb_blist = rec.get("pdb_Blast_info")

    if isinstance(blast_with_pdb, list) and blast_with_pdb:
        # — New format Blast_with_pdb_info (with homology_judgment) —
        best = blast_with_pdb[0] if isinstance(blast_with_pdb[0], dict) else {}
        bm = best.get("blast_metrics") or {}
        hj = best.get("homology_judgment") or {}
        b_pdb_id = (best.get("pdb_id") or "").strip()
        b_ptd_id = (best.get("ptd_id") or "").strip()
        b_protein_id = (best.get("protein_id") or "").strip()
        b_species = (best.get("species") or "").strip()
        b_pident = bm.get("pident")
        b_evalue = bm.get("e_value")
        b_qcov = bm.get("qcovhsp")
        b_score = bm.get("score")
        b_category = (hj.get("category") or "").strip()
        b_confidence = (hj.get("confidence") or "").strip()
        is_self = hj.get("is_same_protein") is True and b_pident == 100
        is_high = hj.get("is_highly_homologous") is True

        pdb_bits: list[str] = []
        if b_ptd_id:
            pdb_bits.append(f"ptd_id={b_ptd_id}")
        if b_protein_id:
            pdb_bits.append(f"protein_id={b_protein_id}")
        if b_pdb_id:
            pdb_bits.append(f"PDB hit={b_pdb_id}")
        b_resolution = (best.get("Resolution") or "").strip()
        if b_resolution:
            pdb_bits.append(f"resolution={b_resolution}")
        b_method = (best.get("Method") or "").strip()
        if b_method.lower().startswith("method:"):
            b_method = b_method[7:].strip()
        if b_method:
            pdb_bits.append(f"method={b_method}")
        if b_species:
            pdb_bits.append(f"species={b_species}")
        if b_pident is not None:
            pdb_bits.append(f"pident={b_pident}")
        if b_evalue is not None:
            pdb_bits.append(f"e-value={b_evalue}")
        if b_qcov is not None:
            pdb_bits.append(f"qcov={b_qcov}")
        if b_score is not None:
            pdb_bits.append(f"score={b_score}")
        lines.append("- PDB BLAST (top hit): " + " | ".join(pdb_bits))

        if is_self:
            if b_confidence == "Experimentally validated":
                lines.append(f"  (self-hit; same protein — experimental structure confirmed in PDB: {b_pdb_id})")
            else:
                lines.append("  (self-hit; same protein — no external experimental structure)")
        elif is_high:
            lines.append(f"  (highly homologous experimental template; confidence={b_confidence}; pident>30% with high coverage — suitable for homology modeling)")
        elif b_pident is not None and float(b_pident) > 30:
            lines.append(f"  (homologous template; category={b_category}; confidence={b_confidence}; pident>30% with coverage — potential homology modeling template)")
        else:
            lines.append(f"  (distant homolog; category={b_category}; confidence={b_confidence})")

        lines.append("  [PDB-BLAST-field-guide] For the PDB BLAST result above, when answering you MUST explain what these metrics mean for the match quality of the query protein (our PID) against the PDB hit:")
        lines.append("    - pident (percent identity): how similar the query and PDB sequences are at the amino-acid level. >90% = near-identical; 50-90% = clearly homologous; 30-50% = remote homolog; <30% = likely unrelated fold.")
        lines.append("    - e-value (expect value): the statistical significance of the match. The smaller the better. e-value < 1e-10 = highly significant; 1e-5 to 1e-10 = moderately significant; > 1e-3 = marginal or noise.")
        lines.append("    - qcov (query coverage, qcovhsp): the fraction of our PID sequence that aligns to the PDB hit. >70% = good coverage of the whole protein; 40-70% = partial coverage (may cover only a domain); <40% = only a small region matches.")
        lines.append("    - score (bit score): higher is better; independent of database size. >100 = strong match; 50-100 = moderate; <50 = weak.")
        lines.append("    - category: summarizes the overall homology level judged by our pipeline (e.g. 'homologous template', 'distant homolog', 'self-hit').")
        lines.append("    - confidence: our pipeline's overall assessment of how reliable this PDB match is. Possible values: 'Experimentally validated' (self-hit confirmed, the query protein has a known experimental structure in PDB), 'high' (strong evidence of homology), 'medium' (moderate evidence), 'low' (weak/unreliable match, should not be used as a structural template).")
        lines.append("    - is_highly_homologous: True when pident>30% AND high coverage — indicates a usable experimental structural template for comparative modeling.")
        lines.append("  When writing your answer, you MUST explicitly discuss: (1) what the confidence level means for match reliability; (2) whether the PDB hit provides a good structural template by discussing pident, e-value, qcov, and confidence together.")

    elif isinstance(pdb_blist, list) and pdb_blist:
        # — Old format pdb_Blast_info —
        best = pdb_blist[0] if isinstance(pdb_blist[0], dict) else {}
        pdb_pid = (best.get("pid") or best.get("PID") or "").strip()
        pdb_pident = best.get("pident")
        pdb_evalue = best.get("e_value")
        pdb_qcov = best.get("qcovhsp")
        pdb_score = best.get("score")
        if pdb_pid or pdb_pident is not None or pdb_evalue is not None:
            pdb_bits: list[str] = []
            if pdb_pid:
                pdb_bits.append(f"PDB hit={pdb_pid}")
            if pdb_pident is not None:
                pdb_bits.append(f"pident={pdb_pident}")
            if pdb_evalue is not None:
                pdb_bits.append(f"e-value={pdb_evalue}")
            if pdb_qcov is not None:
                pdb_bits.append(f"qcov={pdb_qcov}")
            if pdb_score is not None:
                pdb_bits.append(f"score={pdb_score}")
            lines.append("- PDB BLAST (top hit): " + " | ".join(pdb_bits))
            lines.append("  (pident>30% with high coverage suggests a usable homology modeling template)")
            lines.append("  [PDB-BLAST-field-guide] For the PDB BLAST result above, when answering you MUST explain what these metrics mean for the match quality. pident = amino-acid identity (higher is better, >90% near-identical, 30-50% remote). e-value = statistical significance (smaller is better, <1e-10 highly significant, >1e-3 weak). qcov = query coverage (fraction of our protein aligned, >70% good, <40% limited). Score = bit score (higher is better, >100 strong). Explicitly discuss whether this PDB hit provides a good structural template for the query protein.")
    else:
        lines.append("- PDB BLAST: No direct PDB experimental structure hit.")

    return "\n".join(lines).strip()


def _split_csv_maybe(s: str) -> list[str]:
    txt = (s or "").strip()
    if not txt:
        return []
    parts = [p.strip() for p in txt.split(",")]
    return [p for p in parts if p]


def format_tc_record(
    tc_rec: dict,
    *,
    max_symbols: int = 12,
    max_substrates: int = 18,
    max_dois: int = 8,
    max_desc_chars: int = 900,
) -> str:
    rec = tc_rec or {}
    tc_code = (rec.get("tc_code") or "").strip()
    family = (rec.get("family") or "").strip()
    superfamily = (rec.get("superfamily") or "").strip()

    desc = (rec.get("description") or "").strip()
    if desc and max_desc_chars > 0 and len(desc) > max_desc_chars:
        desc = desc[: max_desc_chars - 1].rstrip() + "…"

    symbols_raw = rec.get("symbols")
    symbols: list[str] = []
    if isinstance(symbols_raw, list):
        symbols = [str(x).strip() for x in symbols_raw if str(x).strip()]
    elif isinstance(symbols_raw, str) and symbols_raw.strip():
        symbols = [x.strip() for x in symbols_raw.split(",") if x.strip()]

    substrates_raw = rec.get("substrates")
    substrates: list[str] = []
    if isinstance(substrates_raw, list):
        substrates = [str(x).strip() for x in substrates_raw if str(x).strip()]
    elif isinstance(substrates_raw, str):
        substrates = _split_csv_maybe(substrates_raw)
    else:
        put = rec.get("putative_substrates") or rec.get("Putative_substrates")
        if isinstance(put, list):
            substrates = [str(x).strip() for x in put if str(x).strip()]
        elif isinstance(put, str):
            substrates = _split_csv_maybe(put)

    dois_raw = rec.get("dois")
    dois: list[str] = []
    if isinstance(dois_raw, list):
        dois = [normalize_doi(str(x).strip()) for x in dois_raw if normalize_doi(str(x).strip())]
    elif isinstance(dois_raw, str) and dois_raw.strip():
        dois = [normalize_doi(x.strip()) for x in dois_raw.split(",") if normalize_doi(x.strip())]

    lines: list[str] = []
    lines.append("TC/family summary (parsed from tc_code API):")
    lines.append(f"- TC code: {tc_code or 'N/A'}")
    if family or superfamily:
        lines.append(f"- Family: {family or 'N/A'} | Superfamily: {superfamily or 'N/A'}")
    if symbols:
        preview = symbols[:max_symbols]
        more = f" (+{len(symbols) - len(preview)} more)" if len(symbols) > len(preview) else ""
        lines.append(f"- Symbols/keywords: {', '.join(preview)}{more}")
    if dois:
        preview = dois[:max_dois]
        more = f" (+{len(dois) - len(preview)} more)" if len(dois) > len(preview) else ""
        lines.append(f"- DOIs (DB list, preview): {', '.join(preview)}{more}")
    if substrates:
        preview = substrates[:max_substrates]
        more = f" (+{len(substrates) - len(preview)} more)" if len(substrates) > len(preview) else ""
        lines.append(f"- Substrates (DB list, preview): {', '.join(preview)}{more}")
    if desc:
        lines.append("- Description (DB):")
        lines.append(desc)

    return "\n".join(lines).strip()


def _build_data_card_urls(index_rec: dict | None, symbol: str, tc_rec: dict | None = None) -> str:
    lines = []
    gene_id = ""
    protein_id = ""
    base_sym = symbol.strip() if symbol and symbol.strip().upper() != "NONE" else ""
    base_tc = ""

    if index_rec and isinstance(index_rec, dict):
        gene_id = (index_rec.get("GeneID") or "").strip()
        protein_id = (index_rec.get("Protein_ID") or "").strip()
        base_tc = ((index_rec.get("base_info") or {}).get("tc") or (index_rec.get("family_info") or {}).get("tc_code") or "").strip()
        if gene_id:
            lines.append(f"  GeneID: [{gene_id}](https://yanglab.hzau.edu.cn/ptdb/index/search?pid={gene_id})")
        if protein_id and protein_id != gene_id:
            lines.append(f"  Protein_ID: [{protein_id}](https://yanglab.hzau.edu.cn/ptdb/index/search_index?id={protein_id})")

    if not base_tc and tc_rec and isinstance(tc_rec, dict):
        base_tc = (tc_rec.get("tc_code") or "").strip()

    if base_sym and base_sym.upper() not in ("NONE", "", gene_id.upper()):
        lines.append(f"  Symbol: [{base_sym}](https://yanglab.hzau.edu.cn/ptdb/index/search_index?id={base_sym})")
    if base_tc:
        lines.append(f"  TC: [{base_tc}](https://yanglab.hzau.edu.cn/ptdb/index/tc_code?tc_code={base_tc})")
    if not lines:
        return "(no data card available)"
    return "\n".join(lines)


def _format_chunks_for_prompt(chunks: list[dict]) -> str:
    if not chunks:
        return "(no literature chunks available)"
    lines = []
    for i, c in enumerate(chunks[:8]):
        meta = c.get("metadata") or {}
        doi = (meta.get("DOI") or "").strip()
        section = (meta.get("Section") or "").strip()
        text = (c.get("text") or "").strip()
        header = f"[Chunk {i+1}] DOI: {doi}"
        if section:
            header += f" | Section: {section}"
        lines.append(header)
        lines.append(text[:800])
        lines.append("")
    return "\n".join(lines)


_KINETIC_RE = re.compile(
    r"(?i)\b(K\s?_?m|V\s?_?max|K\s?_?d|IC50|Michaelis[- ]Menten)\b[^.\n]{0,80}?\b"
    r"(\d+(?:\.\d+)?(?:\s*[~∼–—-]\s*\d+(?:\.\d+)?)?\s*(?:µ|μ|u|n|m)\s*M)\b"
)


def _normalize_for_kinetic_scan(text: str) -> str:
    s = text or ""
    s = s.replace("\\mathrm {", "").replace("\\mathrm{", "")
    s = s.replace("\\mathbf {", "").replace("\\mathbf{", "")
    s = s.replace("\\mu", "µ").replace("\\sim", "~")
    s = s.replace("{", "").replace("}", "").replace("$", "")
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    return s


def _norm_kinetic_term(t: str) -> str:
    t2 = re.sub(r"[\s_]+", "", t or "")
    return {"Km": "Km", "Vmax": "Vmax", "Kd": "Kd", "IC50": "IC50",
            "MichaelisMenten": "Michaelis-Menten"}.get(t2, t)


def _build_evidence_inventory(
    symbol: str,
    index_rec: dict | None,
    member_entities: list[tuple[str, str]],
    tc_rec: dict | None,
    chunks: list[dict],
) -> str:
    has_any = (index_rec or member_entities or tc_rec or chunks)
    if not has_any:
        return ""
    lines: list[str] = ["=== EVIDENCE INVENTORY (ground truth of what the provided materials contain) ==="]

    rec_lines: list[str] = []
    if isinstance(index_rec, dict) and index_rec:
        gid = str(index_rec.get("GeneID") or (index_rec.get("base_info") or {}).get("pid") or "").strip()
        rec_lines.append(f"primary gene record: {symbol}" + (f" (GeneID {gid})" if gid else "") + " — PRESENT")
    else:
        rec_lines.append("primary gene record: NONE in the provided materials (expected for family-level symbols)")
    if member_entities:
        rec_lines.append(
            "member record(s): " + ", ".join(f"{a} (GeneID {g})" for a, g in member_entities) + " — PRESENT"
        )
    if isinstance(tc_rec, dict) and tc_rec:
        dois_raw = tc_rec.get("dois")
        n_dois = len([d for d in str(dois_raw or "").split(",") if d.strip()])
        fields: list[str] = []
        for label, keys in (
            ("tc_code", ("tc_code",)),
            ("family", ("family",)),
            ("superfamily", ("superfamily",)),
            ("substrates", ("substrates", "putative_substrates", "Putative_substrates")),
            ("description", ("description",)),
        ):
            val = ""
            for k in keys:
                v = tc_rec.get(k)
                if isinstance(v, str) and v.strip():
                    val = v.strip()
                    break
                if isinstance(v, list) and v:
                    val = ",".join(str(x) for x in v)
                    break
            fields.append(f"{label}={'present' if val else 'empty'}")
        rec = f"symbol/TC record for {symbol}: PRESENT"
        if n_dois:
            rec += f" ({n_dois} linked DOIs)"
        rec_lines.append(rec + "; " + ", ".join(fields))
    else:
        rec_lines.append(f"symbol/TC record for {symbol}: NONE in the provided materials")
    lines.append("- PTDB records provided:")
    lines.extend(f"    {r}" for r in rec_lines)

    hits: list[str] = []
    for i, c in enumerate(chunks, 1):
        text = _normalize_for_kinetic_scan(c.get("text") or "")
        doi = ((c.get("metadata") or {}).get("DOI") or "").strip()
        for m in _KINETIC_RE.finditer(text):
            val = re.sub(r"\s+", "", m.group(2))
            hits.append(f"{_norm_kinetic_term(m.group(1))} … {val} (doi:{doi})")
    lines.append("- Kinetic values found in retrieved chunks (as they appear in the chunk text):")
    if hits:
        lines.extend(f"    {h}" for h in hits[:8])
        if len(hits) > 8:
            lines.append(f"    (+{len(hits) - 8} more)")
    else:
        lines.append("    NONE — denying kinetic values is then appropriate for the queried entity")

    print(f"[evidence-inventory] symbol={symbol} chunks_after_filter={len(chunks)} "
          f"kinetic_hits={len(hits)} primary={'Y' if index_rec else 'N'} "
          f"tc_rec={'Y' if tc_rec else 'N'}")

    lines.append(
        "RULE: any existence/availability statement MUST be consistent with this inventory — records or values\n"
        "listed above EXIST in the materials: citing them is REQUIRED, denying them is an error. Cite a listed\n"
        "kinetic value only if its subject matches the queried entity; if all listed values concern other\n"
        "transporters, the denial may stand but must be scoped to the queried entity. Records marked NONE may be\n"
        "described as absent from the provided materials only — never as absent from the database as a whole.\n"
        "If the user's question concerns uptake affinity or transport kinetics and kinetic values are listed\n"
        "above, you MUST include the relevant value(s) in the answer body with their DOI citation.\n\n"
    )
    return "\n".join(lines)


def _filter_related_chunks(
    chunks: list[dict], index_rec: dict | None, tc_rec: dict | None, symbol: str = ""
) -> list[dict]:
    if not chunks:
        return []
    target_tc = ""
    target_pfams: set[str] = set()
    if index_rec and isinstance(index_rec, dict):
        base = index_rec.get("base_info") or {}
        target_tc = (base.get("tc") or (index_rec.get("family_info") or {}).get("tc_code") or "").strip()
        pfam_str = (base.get("tid_pfam") or "").strip()
        if pfam_str:
            target_pfams = {p.strip() for p in pfam_str.split(";") if p.strip()}
    if tc_rec and isinstance(tc_rec, dict):
        tc_code = (tc_rec.get("tc_code") or "").strip()
        if tc_code and not target_tc:
            target_tc = tc_code
    related = []
    for c in chunks:
        meta = c.get("metadata") or {}
        chunk_tc = (meta.get("TC") or meta.get("tc") or "").strip()
        chunk_pfam = (meta.get("Pfam") or meta.get("pfam") or "").strip()
        if target_tc and chunk_tc and target_tc != chunk_tc:
            continue
        if target_pfams and chunk_pfam:
            chunk_pfams = {p.strip() for p in chunk_pfam.split(";") if p.strip()}
            if chunk_pfams and not target_pfams.intersection(chunk_pfams):
                continue
        related.append(c)
    return _filter_dois_by_gene_mention(related, index_rec, symbol)


def _filter_dois_by_gene_mention(
    chunks: list[dict], index_rec: dict | None, symbol: str
) -> list[dict]:
    if not chunks or not index_rec:
        return chunks
    gene_id = (index_rec.get("GeneID") or "").strip().lower()
    protein_id = (index_rec.get("Protein_ID") or "").strip().lower()
    sym = (index_rec.get("Symbol") or symbol or "").strip().lower()
    base_pid = ((index_rec.get("base_info") or {}).get("pid") or "").strip().lower()
    primary_terms = [t for t in {gene_id, protein_id, base_pid} if t and t not in ("none", "")]
    if primary_terms:
        query_terms = primary_terms
    else:
        query_terms = [t for t in {sym} if t and t not in ("none", "")]
    if not query_terms:
        return chunks
    from collections import defaultdict
    doi_groups: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        meta = c.get("metadata") or {}
        doi = (meta.get("DOI") or "").strip()
        doi_groups[doi].append(c)
    result = []
    for doi, group in doi_groups.items():
        all_text = " ".join((c.get("text") or "").lower() for c in group)
        if any(t in all_text for t in query_terms):
            result.extend(group)
    return result


def _build_chunk_relevance(
    chunks: list[dict], index_rec: dict | None, tc_rec: dict | None
) -> str:
    if not chunks:
        return "(no literature chunks provided)"
    target_tc = ""
    target_pfams: set[str] = set()
    target_species = ""
    if index_rec and isinstance(index_rec, dict):
        base = index_rec.get("base_info") or {}
        target_tc = (base.get("tc") or (index_rec.get("family_info") or {}).get("tc_code") or "").strip()
        pfam_str = (base.get("tid_pfam") or "").strip()
        if pfam_str:
            target_pfams = {p.strip() for p in pfam_str.split(";") if p.strip()}
        target_species = (index_rec.get("species") or base.get("speID") or "").strip()
    if tc_rec and isinstance(tc_rec, dict):
        tc_code = (tc_rec.get("tc_code") or "").strip()
        if tc_code and not target_tc:
            target_tc = tc_code
    has_target_info = bool(target_tc or target_pfams)
    lines = []
    for i, c in enumerate(chunks):
        meta = c.get("metadata") or {}
        chunk_tc = (meta.get("TC") or meta.get("tc") or "").strip()
        chunk_pfam = (meta.get("Pfam") or meta.get("pfam") or "").strip()
        chunk_species = (meta.get("species") or meta.get("Species") or "").strip()
        doi = (meta.get("DOI") or "").strip()
        is_related = True
        is_unknown = False
        reasons = []
        if not chunk_tc and not chunk_pfam:
            is_unknown = True
            reasons.append("chunk metadata missing TC/Pfam — relevance unverified; cite only if chunk text clearly matches the query protein")
        elif target_tc and chunk_tc:
            if target_tc != chunk_tc:
                is_related = False
                reasons.append(f"TC mismatch: chunk={chunk_tc} vs target={target_tc}")
        if not is_unknown and target_pfams and chunk_pfam:
            chunk_pfams = {p.strip() for p in chunk_pfam.split(";") if p.strip()}
            if chunk_pfams and not target_pfams.intersection(chunk_pfams):
                is_related = False
                reasons.append(f"Pfam mismatch: chunk={chunk_pfam} vs target={'|'.join(sorted(target_pfams))}")
        if not is_unknown and target_species and chunk_species:
            if target_species.lower() != chunk_species.lower():
                is_related = False
                reasons.append(f"species mismatch: chunk={chunk_species} vs target={target_species}")
        if is_unknown:
            status = "unknown (no TC/Pfam metadata — treat as NON-CITABLE)"
        elif is_related:
            status = "related"
        else:
            status = "unrelated"
        detail = "; ".join(reasons) if reasons else "matches"
        lines.append(f"  chunk[{i}] {status} (DOI={doi}): {detail}")
    return "\n".join(lines)


def summarize_index_tc_and_rag(
    question: str,
    symbol: str,
    index_rec: dict | None,
    tc_rec: dict | None,
    chunks: list[dict],
    dois: list[str],
    deepseek_answer: str | None = None,
    stream_handler: Callable[[str], None] | None = None,
    source_metadata: dict | None = None,
    guard_note: str | None = None,
    extra_index_recs: list[tuple[str, dict]] | None = None,
) -> str:
    index_prompt = (
        "You are an assistant specialized in plant membrane and transporter protein annotation.\n"
        "Based only on the provided database fields, produce a concise and integrated interpretation of the protein, "
        "focusing on its TC classification and family background, predicted transmembrane architecture, "
        "predicted subcellular localization, and basic physicochemical properties of the sequence, and assess whether "
        "the protein structurally matches a typical membrane or transporter-related family.\n"
        "Do not infer specific substrates, transport directions, or biological functions, and do not use any external "
        "knowledge or literature.\n\n"
        "The input includes the protein ID, species, TC code, family, superfamily, Pfam domain(s), "
        "transmembrane predictions (DeepTM and TMHMM2), localization predictions (DeepLoc2), "
        "AlphaFold 3D prediction quality (pLDDT, pTM) with interpretation thresholds, "
        "PDB BLAST results (pident, qcovhsp, E-value, confidence), protein length, "
        "molecular weight, isoelectric point, and the amino-acid sequence.\n\n"
        "STRUCTURE EVIDENCE — apply this decision rule. Always report EXPERIMENTAL and PREDICTED "
        "evidence as two clearly separated statements; never merge or confuse them.\n"
        "1. Read Blast_with_pdb_info.homology_judgment.confidence and is_same_protein:\n"
        "   - confidence = \"Experimentally validated\" OR is_same_protein = true (self-hit):\n"
        "       STATE CLEARLY that this protein HAS ITS OWN experimental structure; give the PDB ID\n"
        "       (e.g. 9U9F). This is the STRONGEST structural evidence available.\n"
        "       NEVER write \"no experimental validation\", \"no independent experimental structure\",\n"
        "       or \"no external template\" for a self-hit — those are factually WRONG here.\n"
        "   - confidence = \"high\"  -> a strong HOMOLOGOUS template usable for homology modeling.\n"
        "   - confidence = \"medium\" -> a usable template, with explicit caution.\n"
        "   - confidence = \"low\"   -> an unreliable template; do not over-interpret.\n"
        "   - no PDB hit at all     -> state explicitly that only a predicted model exists.\n"
        "   For any non-self template, interpret pident / qcovhsp / e-value: whether the match is\n"
        "   statistically significant, whether qcov covers the whole protein or only one domain,\n"
        "   and whether it is a good template for modeling the query PID.\n"
        "2. AlphaFold pLDDT / pTM are PREDICTED quality metrics — label them as predicted; never\n"
        "   present them as experimental confirmation.\n"
        "3. PDB method / resolution / ligand: report only if present in the input. If absent,\n"
        "   do NOT mention them at all — simply give the PDB ID without method/resolution/ligand.\n"
        "Pfam coverage: enumerate EVERY Pfam ID in the input (e.g. PF01740 AND PF00916). For each,\n"
        "name the domain, its typical function, and how it relates to this transporter's family.\n"
        "Do not silently drop any Pfam ID.\n"
        "When writing LaTeX-style math, always use \\( ... \\) and \\[ ... \\] delimiters instead of $...$.\n"
    )

    tc_prompt = (
        "You are an assistant for membrane transporter classification and family-level summarization.\n"
        "Based only on the provided database description of a given TC code, produce a concise and integrated overview "
        "of this transporter family or superfamily, summarizing its position in the TC classification system, its "
        "general structural features (such as transmembrane topology and repeat architecture), the functional scope at "
        "the family level (without referring to specific proteins or substrates), and its broad biological relevance "
        "across organisms.\n"
        "Do not introduce any information beyond the given description and do not discuss detailed molecular "
        "mechanisms or individual members.\n"
        "When writing LaTeX-style math, always use \\( ... \\) and \\[ ... \\] delimiters instead of $...$.\n"
    )

    user_parts = [
        user_guard_prefix(question),
        f"User question:\n{question}\n\n",
        f"Detected symbol/gene name: {symbol}\n\n",
    ]

    _map_gid = ""
    if isinstance(index_rec, dict):
        _b = index_rec.get("base_info") or {}
        _map_gid = str(index_rec.get("GeneID") or index_rec.get("AthID") or _b.get("pid") or "").strip()
    if symbol and symbol != "NONE" and _map_gid and _map_gid.upper() != symbol.upper():
        user_parts.append(
            f"=== SYMBOL → LOCUS MAPPING (presentation rule, MANDATORY) ===\n"
            f"The user queried by the SYMBOL \"{symbol}\". The PTDB index record resolves this symbol to "
            f"GeneID/locus \"{_map_gid}\" — this is the SAME protein, not a different entity.\n"
            f"- Open with the USER'S symbol first, locus IDs in apposition, e.g. "
            f"\"{symbol} ({_map_gid}) encodes…\" — NEVER open with a bare AGI locus the user never typed.\n"
            f"- State the mapping explicitly exactly once in the opening sentence "
            f"(\"{symbol}, encoded by {_map_gid}, …\" or \"{symbol} (AT-locus {_map_gid}) …\"), "
            f"so the user is never surprised by an identifier they did not use.\n"
            f"- Thereafter you may use either identifier, but link the first occurrence of the GeneID as usual.\n\n"
        )

    if guard_note:
        user_parts.append(f"=== SYSTEM GUARD NOTE ===\n{guard_note}\n\n")

    if deepseek_answer:
        user_parts.extend([
            "=== Reference answer from DeepSeek (AUXILIARY ONLY — it may cite papers about DIFFERENT proteins/families; "
            "DO NOT echo its DOIs unless the paper's TC/Pfam/species actually match the query protein's TC/Pfam/species; "
            "ALWAYS prefer the chunk_relevance tags below over DeepSeek's citations) ===\n",
            deepseek_answer,
            "\n\n",
        ])

    if index_rec:
        parsed = ""
        try:
            parsed = format_index_record(index_rec)
        except Exception:
            parsed = ""
        user_parts.extend(
            [
                index_prompt,
                "\n=== Protein-level fields (parsed summary) ===\n",
                parsed or "N/A",
                "\n=== Protein-level fields (JSON) ===\n",
                repr(index_rec),
                "\n\n",
            ]
        )

    _member_entities: list[tuple[str, str]] = []  # (alias, GeneID)
    for _i, _item in enumerate(extra_index_recs or [], 1):
        if isinstance(_item, dict):
            _alias, _rec = "", _item
        else:
            _alias, _rec = _item
        try:
            _parsed = format_index_record(_rec)
        except Exception:
            _parsed = ""
        if not _parsed:
            continue
        _sym = _rec.get("Symbol") or (_rec.get("base_info") or {}).get("pid") or f"entity {_i}"
        if isinstance(_sym, list):
            _sym = _sym[0] if _sym else f"entity {_i}"
        _gid = str(_rec.get("GeneID") or _rec.get("AthID") or (_rec.get("base_info") or {}).get("pid") or "").strip()
        _label = _alias or str(_sym)
        _gid_note = f", GeneID {_gid}" if _gid else ""
        _db_sym_note = (
            f', DB Symbol "{_sym}"' if _alias and str(_sym).lower() != _alias.lower() else ""
        )
        user_parts.extend([
            f"\n=== Additional protein-level record (secondary entity: {_label} — member record{_gid_note}{_db_sym_note}) ===\n",
            _parsed, "\n\n",
        ])
        if _gid:
            _member_entities.append((_label, _gid))

    if _member_entities:
        _member_list = "; ".join(f"{_a} (GeneID {_g})" for _a, _g in _member_entities)
        _first_alias, _first_gid = _member_entities[0]
        user_parts.append(
            f"=== RECORD PROVENANCE (presentation rule, MANDATORY) ===\n"
            f"The user's question concerns the family-level symbol \"{symbol}\". The additional protein-level\n"
            f"record(s) above belong to SPECIFIC member(s) ({_member_list}) retrieved as representative member\n"
            f"data — NOT properties of the family as a whole.\n"
            f"- The FIRST sentence that draws on ANY field of a member record (PDB/structure, topology,\n"
            f"  localization, length/MW/pI, AlphaFold metrics) MUST state provenance, e.g.: \"Protein-level\n"
            f"  properties below are from {_first_alias} ({_first_gid}), selected as a retrieved {symbol} family member.\"\n"
            f"- NEVER present member-specific values as family-level properties.\n"
            f"- In the \"Sources & Evidence\" panel, any Structures line built from member data must name the\n"
            f"  member and its GeneID.\n\n"
        )

    if tc_rec:
        parsed_tc = ""
        try:
            parsed_tc = format_tc_record(tc_rec)
        except Exception:
            parsed_tc = ""
        user_parts.extend(
            [
                tc_prompt,
                "\n=== TC family description (parsed summary) ===\n",
                parsed_tc or "N/A",
                "\n=== TC family description (JSON) ===\n",
                repr(tc_rec),
                "\n\n",
            ]
        )
    # --- drop chunks with clear TC/Pfam mismatch ---
    _chunks_pre_filter = chunks or []
    chunks = _filter_related_chunks(chunks, index_rec, tc_rec, symbol)
    if _chunks_pre_filter and not chunks:
        print(f"[chunk-filter] filtered ALL {len(_chunks_pre_filter)} chunks "
              f"(symbol={symbol}) — falling back to unfiltered set")
        chunks = _chunks_pre_filter
    distinct_chunk_dois = dedupe_preserve_order(
        [(c.get("metadata") or {}).get("DOI") for c in (chunks or []) if (c.get("metadata") or {}).get("DOI")]
    )
    merged_prompt_dois = dedupe_preserve_order([d for d in (dois or []) if d] + distinct_chunk_dois)
    tm: dict[str, str] = fetch_titles_by_dois(merged_prompt_dois, timeout=30) if merged_prompt_dois else {}
    multi_doi_extra = ""
    if len(merged_prompt_dois) >= 2:
        multi_doi_extra = (
            f"\n- Multiple distinct literature DOI(s) are provided ({len(merged_prompt_dois)} unique). "
            "Across the answer, parenthetical citations from at least TWO different DOIs must appear (each from chunk metadata or the DOI list), "
            "typically after different 2–3 sentence spans—do not attribute all literature-backed claims to a single DOI.\n"
        )

    doi_title_block = ""
    if tm:
        lines_tt = []
        for d in merged_prompt_dois:
            nd = normalize_doi(str(d).strip()) if d else ""
            if not nd:
                continue
            tit = (tm.get(nd) or "").strip()
            if tit:
                lines_tt.append(f"- {nd} → {tit}")
        if lines_tt:
            doi_title_block = (
                "\n\n=== DOI → title (PTDB API get_title_by_doi; authoritative titles for citations) ===\n"
                + "\n".join(lines_tt)
                + "\n"
            )

    _inv = _build_evidence_inventory(symbol, index_rec, _member_entities, tc_rec, chunks)
    if _inv:
        user_parts.append(_inv)
    user_parts.extend(
        [
            "=== Retrieved literature chunks ===\n",
            _format_chunks_for_prompt(chunks),
            "\n\n=== DOIs from literature retrieval (list) ===\n",
            repr(dois),
            doi_title_block,
            "=== IMPORTANT: The literature chunks above contain experimental findings about the query entity.\n"
            "You MUST incorporate their key findings AND cite them. A protein with 10+ retrieved chunks\n"
            "MUST have at least one in-text citation. Answering from database JSON alone is INCOMPLETE.\n"
            "HARD RULE: If chunks exist, your answer MUST contain at least one (DOI) citation in the body.\n"
            "A Sources & Evidence panel with zero References when chunks exist is INVALID.\n"
            "If chunks are EMPTY (no literature chunks available), rely ONLY on PTDB database records;\n"
            "do NOT echo any citations from the DeepSeek reference — it may discuss DIFFERENT proteins.\n\n"
            "Write the final result as an academically rigorous, thorough interpretation based STRICTLY on the inputs above.\n"
            "\nOutput requirements:\n"
            "- Write 2-4 coherent paragraphs (not one short paragraph). When multiple DISTINCT and\n"
            "  RELEVANT DOIs appear, prefer 3-4 paragraphs so evidence is attributed separately.\n"
            "- Integrate (i) protein-level annotation, (ii) TC/family background, (iii) evidence from\n"
            "  retrieved chunks. End with one integrative paragraph linking structure + function +\n"
            "  evidence into a cautious, evidence-based conceptual model.\n"
            "\nCITATION RULES:\n"
            "- Cite literature AFTER every 2-3 sentences that draw facts from a retrieved chunk.\n"
            "  Format: (DOI; short title). Do NOT cite for facts that come from the database JSON.\n"
            "- If a chunk is about a DIFFERENT protein/family than the query — skip it, don't cite.\n"
            "- If a chunk is about the SAME protein or family — DO cite it. Default to citing\n"
            "  when the chunk text clearly relates to the entity being discussed.\n"
            "- A well-documented protein should typically have at least one literature citation.\n"
            "- When in doubt about a chunk's relevance, cite it — erring on inclusion is better than\n"
            "  having zero references for a protein that clearly has literature available.\n"
            "- NEVER cite a DOI for database facts (TC code, Pfam ID, TM count, pI, MW, pLDDT/pTM).\n"
            "\nSENTENCE-LEVEL MARKERS (MANDATORY at end of every sentence):\n"
            "- Append [DATABASE-GROUNDED] when the sentence draws ONLY from PTDB JSON fields.\n"
            "- Append [LITERATURE-GROUNDED] when the sentence draws from a retrieved chunk (cite the DOI).\n"
            "- Append [AI-inferred] when the sentence contains interpretation, extrapolation, or\n"
            "  speculation beyond what is directly stated in any source. Examples: functional hypotheses\n"
            "  (\"may contribute to\"), cross-species extrapolations, mechanistic models combining\n"
            "  multiple sources. These sentences must also appear in the Uncertainty section of the Sources & Evidence panel.\n"
            "\nDATA CARD LINKS:\n"
            "- On first mention, link EACH entity in the body using EXACTLY these URL templates:\n"
            "  GeneID (e.g. AT4G12980): https://yanglab.hzau.edu.cn/ptdb/index/search?pid=<GeneID>\n"
            "  Symbol:                 https://yanglab.hzau.edu.cn/ptdb/index/search_index?id=<Symbol>\n"
            "  Protein_ID:             https://yanglab.hzau.edu.cn/ptdb/index/search_index?id=<Protein_ID>\n"
            "  TC code:                https://yanglab.hzau.edu.cn/ptdb/index/tc_code?tc_code=<TC>\n"
            "- The FIRST occurrence of the GeneID MUST be linked — typically in apposition with the queried symbol in the opening sentence (see SYMBOL → LOCUS MAPPING rule above), not as a standalone opening identifier.\n"
            "- Use the EXACT URLs in {data_card_urls} for the Data entries section; do NOT modify them.\n"
            "- NEVER invent Pfam, PDB, UniProt, or any other external URLs.\n"
            "\nCitation (DOI) requirements:\n"
            "- Place citations inline every 2-3 sentences for LITERATURE-GROUNDED content only.\n"
            "- Every literature citation MUST use (10.xxxx/yyyy; concise exact or lightly shortened PTDB\n"
            "  title) when that DOI has a title line above; otherwise (10.xxxx/yyyy) only. Never invent titles.\n"
            "- Use ONLY DOI strings present in chunk metadata (field DOI) or the provided DOI list.\n"
            + (
                "- Paragraphs based only on protein/TC JSON without a clear chunk need not add a DOI.\n"
                + multi_doi_extra
                + "\nHard constraints:\n"
            )
            + "- Do NOT use any external knowledge beyond the provided JSON fields and literature chunks.\n"
            "- Do NOT attach any DOI to a sentence that is a database fact.\n"
            "- Do NOT add a trailing 'References', 'References Source:', numbered list, or a separate 'DOI:' line; inline parenthetical DOIs are sufficient.\n"
            "- After the Sources & Evidence panel, append a \"Sources & Evidence\" panel as the VERY LAST block:\n"
            "  **Sources & Evidence**\n"
            "  - References: ONLY the DOIs actually cited inline above, each as (DOI; title). If none were\n"
            "    cited, write exactly: \"No literature evidence was used; this answer relies on PTDB records only.\"\n"
            "  - Data entries: the data card link(s) with GeneID, Protein_ID, TC code.\n"
            "    Do NOT include Pfam IDs, substrates, or other fields here.\n"
            "  - Structures: Experimental — PDB ID (method/resolution/ligand only if provided);\n"
            "    Predicted — AlphaFold3 model ID with pLDDT/pTM.\n"
            "  - Uncertainty / inferred: bullet the SPECIFIC inferred statements (topic-level, not vague).\n"
            "  CONSISTENCY: the panel's References list MUST equal the set of DOIs cited inline above —\n"
            "  no DOI may appear in one but not the other.\n"
            + "\nImportant: Do NOT add an \"AI Inference Note\" section.\n",
        ]
    )
    print("Entering summarization:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    user_content = "".join(user_parts)

    # --- build data_card_urls and inject into the prompt ---
    data_card_urls = _build_data_card_urls(index_rec, symbol, tc_rec)
    user_content = user_content.replace("{data_card_urls}", data_card_urls)

    if not index_rec:
        has_tc = tc_rec and isinstance(tc_rec, dict) and tc_rec.get("tc_code", "").strip()
        if has_tc:
            user_content += (
                "\n\n=== NOTE: FAMILY-LEVEL SYMBOL (no single gene index entry) ===\n"
                "This is EXPECTED for family-level symbols: the gene-level index API has no entry, but the "
                "symbol/TC lookup record above IS the PTDB record for this entity (valid evidence — cite its DOIs). "
                "Phrase it exactly as: 'PTDB holds this as a family-level symbol record without a single "
                "gene-level index entry.' Do NOT claim that no PTDB record exists or that TC/family annotation "
                "is unavailable — the record fields above define the authoritative scope. "
                "Do NOT fabricate GeneID or Protein_ID.\n"
            )
        else:
            user_content += (
                "\n\n=== NOTE: NO RECORD IN THE PROVIDED MATERIALS ===\n"
                "The provided materials contain no gene-level index record and no symbol/TC record carrying a "
                "TC code. Do NOT include GeneID, Protein_ID, or TC code links; do NOT add a \"Data entries\" "
                "section; only the Symbol link may be included; do NOT fabricate AT#G##### IDs. When mentioning "
                "this gap, scope it to the provided materials ('the provided records do not include …') — do NOT "
                "claim the database as a whole lacks the symbol.\n"
            )

    defer_stream = stream_handler is not None
    messages = [
        {
            "role": "system",
            "content": inject_guard("As a leading bioinformatics authority in plant transport systems, synthesize the provided JSON datasets and literature excerpts into a high-level, rigorously reasoned analytical synthesis. When the user message includes a DOI→title list from PTDB, every literature citation parenthesis that uses those DOIs must place the DOI first, then a semicolon, then the corresponding title text (same order as the deterministic post-processor). The final answer MUST end with a \"Sources & Evidence\" panel. The panel's reference list must contain exactly the DOIs cited inline in the body — never more, never fewer."),
        },
        {"role": "user", "content": user_content},
    ]

    summary = ""
    for _attempt in range(3):
        if stream_handler:
            stream = LLM_client(
                model="deepseek-v4-flash",
                messages=messages,
                temperature=0.2,
                stream=True,
                max_tokens=16384,
                )
            pieces: list[str] = []
            for chunk in stream:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if not delta:
                    continue
                pieces.append(delta)
                if not defer_stream:
                    stream_handler(delta)
            summary = "".join(pieces)
        else:
            resp = LLM_client(
                model="deepseek-v4-flash",
                messages=messages,
                temperature=0.2,
                max_tokens=16384,
            )
            summary = (resp.choices[0].message.content or "").strip()
        if summary.strip():
            break
        print(f"[summarize] empty response (attempt {_attempt + 1}/3), retrying")
    if stream_handler:
        summary = summary or ""
    print("Finished summarization:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    summary = wrap_bare_citations((summary or "").strip())
    summary = enrich_parenthetical_citations_with_ptdb_titles(
        summary, tm, normalize_doi=normalize_doi
    )
    link_rec = index_rec
    if not link_rec:
        sym_for_fetch = (symbol or "").strip()
        if sym_for_fetch and sym_for_fetch.upper() not in ("NONE",):
            try:
                fetched, _ = fetch_index_and_tc_from_symbol(sym_for_fetch)
            except Exception:
                fetched = None
            if isinstance(fetched, dict) and fetched:
                link_rec = fetched
    if link_rec:
        _spid = (
            (link_rec.get("Protein_ID") or link_rec.get("AthID") or (link_rec.get("base_info") or {}).get("pid") or "")
            .strip()
        )
        summary = link_symbol_or_pid_to_ptdb(summary, symbol, _spid, link_rec)
    else:
        _legit_loci = set()
        for _it in extra_index_recs or []:
            _r = _it if isinstance(_it, dict) else _it[1]
            if not isinstance(_r, dict):
                continue
            for _k in (_r.get("GeneID"), _r.get("AthID"), _r.get("Protein_ID"), (_r.get("base_info") or {}).get("pid")):
                _v = str(_k or "").strip()
                if _v:
                    _legit_loci.add(_v.split(".")[0].upper())
        if _legit_loci:
            summary = re.sub(
                r'\bAT[1-5C-M]G\d{5}(\.\d+)?\b',
                lambda m: m.group(0) if m.group(0).split(".")[0].upper() in _legit_loci else "",
                summary,
            )
        else:
            summary = re.sub(r'\bAT[1-5C-M]G\d{5}(\.\d+)?\b', '', summary)
        has_tc = tc_rec and isinstance(tc_rec, dict) and tc_rec.get("tc_code", "").strip()
        if not has_tc:
            summary = re.sub(r'\b\d\.[A-Z]\.\d+(\.\d+)*\b', '', summary)
    summary = link_bare_tair_pids_in_text(summary)

    data_card_urls = _build_data_card_urls(index_rec, symbol, tc_rec)
    if data_card_urls and data_card_urls != "(no data card available)":
        summary = re.sub(
            r'(\n\s*- Data entries:)[\s\S]*?(?=\n\s*- (?:Structures|References|Uncertainty|inferred|$))',
            r'\1\n' + "\n".join(f"    {line.lstrip()}" for line in data_card_urls.split("\n")),
            summary
        )

    if defer_stream and stream_handler:
        stream_handler(summary)

    if source_metadata is not None and isinstance(source_metadata, dict):
        distinct_dois = dedupe_preserve_order(
            [(c.get("metadata") or {}).get("DOI") for c in (chunks or []) if (c.get("metadata") or {}).get("DOI")]
        )
        for doi in distinct_dois:
            nd = normalize_doi(str(doi).strip()) if doi else ""
            if not nd:
                continue
            chunks_for_doi = sum(
                1 for c in (chunks or [])
                if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd
            )
            title = (tm.get(nd) or "").strip() if tm else ""
            chunk_texts_for_doi = [c.get("text", "") for c in (chunks or []) if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
            chunk_texts_for_doi = [t for t in chunk_texts_for_doi if _filter_chunk_noise(t)]
            source_metadata.setdefault("literature", []).append({
                "doi": doi,
                "title": title,
                "chunks_used": chunks_for_doi,
                "chunks": chunk_texts_for_doi,
            })
        source_metadata.setdefault("inference_notes", []).append(
            "See the \"AI Inference Note\" paragraph at the end of the answer for details on which parts are AI-inferred."
        )

    return summary


def run_normal_chat(question: str, stream_handler: Callable[[str], None] | None = None) -> str:
    question = (question or "").strip()
    if not question:
        return "Missing question."

    rejection = intercept(question, mode="normal")
    if rejection is not None:
        return rejection

    cached = _find_normal_cached_answer(question, NORMAL_CACHE_PATH)
    if isinstance(cached, str) and cached.strip():
        print("normal_chat hit normal_cache for question:", question[:80])
        return _ensure_ptdb_links_in_cached_answer(question, cached)

    source_meta = _build_source_metadata()

    print("Entering DeepSeek stage:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    deepseek_answer = _call_deepseek(question)
    if deepseek_answer:
        deepseek_answer = re.sub(r'\bAT[1-5C-M]G\d{5}(\.\d+)?\b', '[AthID redacted]', deepseek_answer)
        deepseek_answer = re.sub(r'\b\d\.[A-Z]\.\d+(\.\d+)*\b', '[TC code redacted]', deepseek_answer)
    source_meta["api_calls"].append({
        "api": "deepseek_reference",
        "params": {"question": question[:80]},
        "result_summary": "DeepSeek reference answer retrieved" if deepseek_answer else "DeepSeek call failed or returned empty",
    })
    print("Finished DeepSeek stage:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    info = build_entity_evidence(question)
    entities = info.get("entities") or {}
    ent_tcs = entities.get("TCCODE") or []
    ent_indices = entities.get("index") or []
    ent_symbols = entities.get("symbol") or []
    ent_families = entities.get("protein_family") or []
    ent_dois = entities.get("DOI") or []
    quick_links = []  # PTDB quick links disabled

    requested_doi = None
    if ent_dois:
        requested_doi = ent_dois[0]
    else:
        requested_doi = extract_doi_from_text(question)
    if requested_doi:
        source_meta["api_calls"].append({
            "api": "doi_reconstruct",
            "params": {"doi": requested_doi},
            "result_summary": "Reconstructing and summarizing paper by DOI",
        })
        clean_question = strip_doi_from_text(question, requested_doi)
        doi_summary = reconstruct_and_summarize_paper_by_doi(
            clean_question,
            requested_doi,
            stream_handler=stream_handler,
        )
        if doi_summary:
            source_meta["api_calls"][-1]["result_summary"] = "Paper reconstructed and summarized successfully"
            source_meta["literature"].append({
                "doi": requested_doi,
                "title": "",
                "chunks_used": 0,
            })
            doi_summary = _append_sources_evidence_marker(doi_summary, source_meta)
            _append_normal_cache(question, doi_summary, NORMAL_CACHE_PATH)
            return doi_summary
        else:
            source_meta["api_calls"][-1]["result_summary"] = "DOI reconstruction failed, falling back to RAG"

    if ent_symbols:
        symbol = (ent_symbols[0] or "").strip()
        if not symbol:
            symbol = "NONE"

        source_meta["api_calls"].append({
            "api": "ptdb_symbol_tc_doi",
            "params": {"symbol": symbol},
            "result_summary": "Querying PTDB for symbol TC/DOI info",
        })
        sym_tc = fetch_tc_symbol_doi_info(symbol)
        rel_list: list[dict] = []
        tc_rec = None
        if sym_tc:
            tc_rec = sym_tc
            source_meta["api_calls"][-1]["result_summary"] = f"Found TC/DOI info for symbol {symbol}"
        else:
            source_meta["api_calls"][-1]["result_summary"] = f"No TC/DOI info for symbol {symbol}"

        source_meta["api_calls"].append({
            "api": "ptdb_symbol_to_doi",
            "params": {"symbol": symbol},
            "result_summary": "Fetching DOIs by symbol",
        })
        doi_list = fetch_dois_by_symbol(symbol)
        if doi_list:
            doi_list = dedupe_preserve_order(doi_list)
            rel_list = [{"properties": {"source_paper": d}} for d in doi_list]
            source_meta["api_calls"][-1]["result_summary"] = f"Found {len(doi_list)} DOIs for symbol {symbol}"
        else:
            source_meta["api_calls"][-1]["result_summary"] = f"No DOIs found for symbol {symbol}"

        if not rel_list:
            fallback_symbol = None
            try:
                idx_rec_fb, _ = fetch_index_and_tc_from_symbol(symbol)
                if idx_rec_fb:
                    fb_sym = idx_rec_fb.get("Symbol") or ""
                    if isinstance(fb_sym, list) and fb_sym:
                        fb_sym = fb_sym[0]
                    fb_sym = str(fb_sym).strip()
                    if fb_sym and fb_sym != symbol:
                        fb_dois = fetch_dois_by_symbol(fb_sym)
                        if fb_dois:
                            doi_list = dedupe_preserve_order(fb_dois)
                            rel_list = [{"properties": {"source_paper": d}} for d in doi_list]
                            fallback_symbol = fb_sym
                            source_meta["api_calls"].append({
                                "api": "ptdb_symbol_to_doi",
                                "params": {"symbol": fb_sym},
                                "result_summary": f"Fallback: found {len(doi_list)} DOIs via index symbol {fb_sym}",
                            })
            except Exception:
                pass
        
        if not rel_list:
            if sym_tc is None and symbol and symbol != "NONE":
                return (
                    f"No information was found for '{symbol}' in the PlantTP Agent database. "
                    "This gene/protein symbol was not recognized. "
                    "Please verify the symbol name and try again."
                )
            return _fallback_pure_rag_answer(
                question,
                entity_type="symbol",
                entity_value=symbol,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        source_meta["api_calls"].append({
            "api": "vector_bm25_rerank",
            "params": {"symbol": symbol, "dois_count": len(doi_list), "question": question[:80]},
            "result_summary": "Retrieving literature chunks",
        })
        augmented = f"Target symbol/gene: {symbol}\nQuestion: {question}"
        try:
            print("Literature-chunk fetch time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            rag_res = local_rag_chunks(augmented, rel_list, symbol)
            print("Literature-chunk fetch time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            print("run_normal_chat retrieval error (symbol mode):", e)
            return "Backend retrieval failed when running RAG. Please try again later."

        chunks = (rag_res or {}).get("chunks") or []
        dois = (rag_res or {}).get("return_dois") or []
        source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs"
        chunk_title_by_doi = fetch_titles_by_dois(dois, timeout=30) if dois else {}
        for doi in dois:
            nd = normalize_doi(str(doi).strip())
            chunks_for_doi = sum(1 for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
            chunk_texts = [c.get("text", "") for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
            chunk_texts = [t for t in chunk_texts if _filter_chunk_noise(t)]
            source_meta["literature"].append({
                "doi": doi,
                "title": chunk_title_by_doi.get(nd, ""),
                "chunks_used": chunks_for_doi,
                "chunks": chunk_texts,
            })
        if not chunks:
            return _fallback_pure_rag_answer(
                question,
                entity_type="symbol",
                entity_value=symbol,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        idx_rec_for_link = None
        try:
            idx_rec_for_link, _ = fetch_index_and_tc_from_symbol(symbol)
        except Exception:
            idx_rec_for_link = None

        extra_index_recs: list[dict] = []
        _seen = {symbol.lower()}
        _primary_pid = str(((idx_rec_for_link or {}).get("base_info") or {}).get("pid") or "").lower()
        if _primary_pid:
            _seen.add(_primary_pid)
        for _extra in list(ent_symbols[1:]) + list(ent_families) + list(ent_indices):
            if len(extra_index_recs) >= 3:
                break
            _e = (_extra or "").strip()
            if not _e or _e.lower() in _seen:
                continue
            _rec = None
            try:
                if _e.upper().startswith("AT") and _e[2:3].isdigit():
                    _rec = fetch_index_info(_e)
                else:
                    _rec, _ = fetch_index_and_tc_from_symbol(_e)
            except Exception:
                _rec = None
            if not isinstance(_rec, dict) or not _rec:
                continue
            _pid = str((_rec.get("base_info") or {}).get("pid") or "").lower()
            if _pid and _pid in _seen:
                continue
            _seen.add(_e.lower())
            if _pid:
                _seen.add(_pid)
            extra_index_recs.append((_e, _rec))

        print("normal_chat tc_rec (symbol mode):", tc_rec)
        try:
            result = summarize_index_tc_and_rag(
                question,
                symbol,
                idx_rec_for_link,
                tc_rec,
                chunks,
                dois,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_metadata=source_meta,
                extra_index_recs=extra_index_recs,
            )
            if idx_rec_for_link:
                _spid = (
                    (idx_rec_for_link.get("Protein_ID") or idx_rec_for_link.get("AthID") or (idx_rec_for_link.get("base_info") or {}).get("pid") or "")
                    .strip()
                )
                if _spid:
                    result = link_symbol_or_pid_to_ptdb(result, symbol, _spid, idx_rec_for_link)
            result = _append_reference_summary_suggestions(result, dois, extra_chips=quick_links)
            result = _append_sources_evidence_marker(result, source_meta)
            _append_normal_cache(question, result, NORMAL_CACHE_PATH)
            return result
        except Exception as e:
            print("summarize_index_tc_and_rag error (symbol mode):", e)
            return (
                "Failed to run full index+TC summarization. Here are the top retrieved literature chunks (JSON):\n"
                + repr(chunks)
            )

    if ent_families:
        family = (ent_families[0] or "").strip()
        if not family:
            family = "NONE"

        # ---- Main line: symbol API (get_doi_by_symbol + get_ptdb_agent_tc_symbol_doi_info) ----
        idx_rec_for_link = None
        tc_rec_for_family = None
        symbol_dois: list[str] = []
        try:
            idx_rec_for_link, _ = fetch_index_and_tc_from_symbol(family)
            if idx_rec_for_link:
                source_meta["api_calls"].append({
                    "api": "ptdb_symbol_index_tc",
                    "params": {"symbol": family},
                    "result_summary": f"Retrieved index record for {family}",
                })
        except Exception:
            idx_rec_for_link = None
        try:
            tc_rec_for_family = fetch_tc_symbol_doi_info(family)
            if tc_rec_for_family:
                source_meta["api_calls"].append({
                    "api": "ptdb_symbol_tc_doi",
                    "params": {"symbol": family},
                    "result_summary": f"Retrieved TC/DOI info for {family}",
                })
        except Exception:
            tc_rec_for_family = None
        try:
            symbol_dois = fetch_dois_by_symbol(family)
            source_meta["api_calls"].append({
                "api": "ptdb_symbol_to_doi",
                "params": {"symbol": family},
                "result_summary": f"Retrieved {len(symbol_dois)} DOIs via symbol API for {family}",
            })
        except Exception:
            symbol_dois = []

        source_meta["api_calls"].append({
            "api": "ptdb_smallfamily",
            "params": {"family": family},
            "result_summary": "Querying PTDB small family API (supplement)",
        })
        rel_list = smallfamily(family)
        source_meta["api_calls"][-1]["result_summary"] = f"Found {len(rel_list)} DOIs via Neo4j for family {family}"

        # ---- Merge DOIs from all sources ----
        combined_sources: dict[str, str] = {}  # normalized DOI -> raw DOI
        for doi in symbol_dois:
            nd = normalize_doi(str(doi).strip())
            if nd:
                combined_sources[nd] = doi
        for r in rel_list or []:
            source_paper = (r.get("properties") or {}).get("source_paper", "")
            nd = normalize_doi(str(source_paper).strip())
            if nd:
                combined_sources[nd] = source_paper
        all_rel_list = [{"properties": {"source_paper": raw}} for raw in combined_sources.values()]
        source_meta["api_calls"].append({
            "api": "merge_doi_sources",
            "params": {"symbol_dois": len(symbol_dois), "neo4j_dois": len(rel_list or [])},
            "result_summary": f"Combined {len(combined_sources)} unique DOIs from all sources",
        })

        if not all_rel_list:
            return _fallback_pure_rag_answer(
                question,
                entity_type="protein_family",
                entity_value=family,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        # ---- RAG retrieval ----

        source_meta["api_calls"].append({
            "api": "vector_bm25_rerank",
            "params": {"family": family, "question": question[:80]},
            "result_summary": "Retrieving literature chunks",
        })
        augmented = f"Target protein family: {family}\nQuestion: {question}"
        try:
            rag_res = local_rag_chunks(augmented, all_rel_list, family)
        except Exception as e:
            print("run_normal_chat retrieval error (protein_family mode):", e)
            return "Backend retrieval failed when running RAG. Please try again later."

        chunks = (rag_res or {}).get("chunks") or []
        dois = (rag_res or {}).get("return_dois") or []
        source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs"
        chunk_title_by_doi = fetch_titles_by_dois(dois, timeout=30) if dois else {}
        for doi in dois:
            nd = normalize_doi(str(doi).strip())
            chunks_for_doi = sum(1 for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
            chunk_texts = [c.get("text", "") for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
            chunk_texts = [t for t in chunk_texts if _filter_chunk_noise(t)]
            source_meta["literature"].append({
                "doi": doi,
                "title": chunk_title_by_doi.get(nd, ""),
                "chunks_used": chunks_for_doi,
                "chunks": chunk_texts,
            })
        if not chunks:
            return _fallback_pure_rag_answer(
                question,
                entity_type="protein_family",
                entity_value=family,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        try:
            result = summarize_index_tc_and_rag(
                question,
                family,
                idx_rec_for_link,
                tc_rec_for_family,
                chunks,
                dois,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_metadata=source_meta,
            )
            if idx_rec_for_link:
                _spid = (
                    (idx_rec_for_link.get("Protein_ID") or idx_rec_for_link.get("AthID") or (idx_rec_for_link.get("base_info") or {}).get("pid") or "")
                    .strip()
                )
                if _spid:
                    result = link_symbol_or_pid_to_ptdb(result, family, _spid, idx_rec_for_link)
            result = _append_reference_summary_suggestions(result, dois, extra_chips=quick_links)
            result = _append_sources_evidence_marker(result, source_meta)
            _append_normal_cache(question, result, NORMAL_CACHE_PATH)
            return result
        except Exception as e:
            print("summarize_index_tc_and_rag error (protein_family mode):", e)
            return (
                "Failed to run literature-only summarization. Here are the top retrieved literature chunks (JSON):\n"
                + repr(chunks)
            )

    if ent_indices:
        pid = (ent_indices[0] or "").strip()
        if not pid:
            pid = "UNKNOWN_INDEX"

        source_meta["api_calls"].append({
            "api": "ptdb_index",
            "params": {"pid": pid},
            "result_summary": "Querying PTDB index for protein annotation",
        })
        idx_rec = fetch_index_info(pid)
        tc_rec = None
        if idx_rec:
            source_meta["api_calls"][-1]["result_summary"] = f"Found record for index {pid}"
            tc_code = (idx_rec.get("base_info") or {}).get("tc") or (idx_rec.get("family_info") or {}).get("tc_code")
            if tc_code:
                source_meta["api_calls"].append({
                    "api": "ptdb_tccode",
                    "params": {"tc_code": tc_code},
                    "result_summary": "Querying PTDB TC code for family info",
                })
                tc_rec = fetch_tc_info(tc_code)
                if tc_rec:
                    source_meta["api_calls"][-1]["result_summary"] = f"Found TC record for {tc_code}"
                else:
                    source_meta["api_calls"][-1]["result_summary"] = f"No TC record found for {tc_code}"
        else:
            source_meta["api_calls"][-1]["result_summary"] = f"No record found for index {pid}"
        print("normal_chat index_rec (index mode):", idx_rec)
        print("normal_chat tc_rec (index mode):", tc_rec)

        if isinstance(idx_rec, dict) and idx_rec.get("error"):
            return f"No matching record found for index {pid} in the PlantTP Agent database."

        if not idx_rec and not tc_rec:
            return _fallback_pure_rag_answer(
                question,
                entity_type="index",
                entity_value=pid,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        rag_symbol = ""
        sym = idx_rec.get("Symbol") or (idx_rec.get("base_info") or {}).get("Symbol")
        if sym is not None:
            rag_symbol = (sym[0] if isinstance(sym, list) and sym else sym) or ""
            rag_symbol = str(rag_symbol).strip() if rag_symbol else ""

        chunks: list[dict] = []
        dois: list[str] = []
        display_symbol = pid
        if rag_symbol:
            display_symbol = rag_symbol
            source_meta["api_calls"].append({
                "api": "ptdb_symbol_tc_doi",
                "params": {"symbol": rag_symbol},
                "result_summary": f"Querying TC/DOI info for symbol {rag_symbol}",
            })
            sym_tc = fetch_tc_symbol_doi_info(rag_symbol)
            rel_list: list[dict] = []
            if sym_tc:
                doi_list = fetch_dois_by_symbol(rag_symbol)
                if doi_list:
                    doi_list = dedupe_preserve_order(doi_list)
                    rel_list = [{"properties": {"source_paper": d}} for d in doi_list]
                source_meta["api_calls"][-1]["result_summary"] = f"Found TC/DOI info for symbol {rag_symbol}"
            else:
                source_meta["api_calls"][-1]["result_summary"] = f"No TC/DOI info for symbol {rag_symbol}"
            if rel_list:
                source_meta["api_calls"].append({
                    "api": "vector_bm25_rerank",
                    "params": {"symbol": rag_symbol, "question": question[:80]},
                    "result_summary": "Retrieving literature chunks",
                })
                augmented = f"Target symbol/gene: {rag_symbol}\nQuestion: {question}"
                try:
                    rag_res = local_rag_chunks(augmented, rel_list, rag_symbol)
                    chunks = (rag_res or {}).get("chunks") or []
                    dois = (rag_res or {}).get("return_dois") or []
                    source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs"
                    chunk_title_by_doi = fetch_titles_by_dois(dois, timeout=30) if dois else {}
                    for doi in dois:
                        nd = normalize_doi(str(doi).strip())
                        chunks_for_doi = sum(1 for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
                        chunk_texts = [c.get("text", "") for c in chunks if normalize_doi(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
                        chunk_texts = [t for t in chunk_texts if _filter_chunk_noise(t)]
                        source_meta["literature"].append({
                            "doi": doi, "title": chunk_title_by_doi.get(nd, ""),
                            "chunks_used": chunks_for_doi, "chunks": chunk_texts,
                        })
                except Exception as e:
                    print("run_normal_chat retrieval error (index→symbol RAG):", e)

        if not chunks:
            source_meta["api_calls"].append({
                "api": "unrestricted_rag",
                "params": {"pid": pid, "question": question[:80]},
                "result_summary": "Running unrestricted vector RAG as fallback",
            })
            try:
                augmented_q = f"Target index: {pid}\nQuestion: {question}"
                rag_res = simple_rag_chunks_unrestricted(augmented_q)
                chunks = (rag_res or {}).get("chunks") or []
                dois = (rag_res or {}).get("return_dois") or []
                source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs (unrestricted)"
            except Exception as e:
                print("run_normal_chat fallback retrieval error (index→pure RAG):", e)

        try:
            result = summarize_index_tc_and_rag(
                question,
                display_symbol,
                idx_rec,
                tc_rec,
                chunks,
                dois,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_metadata=source_meta,
            )
            result = _append_reference_summary_suggestions(result, dois, extra_chips=quick_links)
            result = _append_sources_evidence_marker(result, source_meta)
            _append_normal_cache(question, result, NORMAL_CACHE_PATH)
            return result
        except Exception as e:
            print("summarize_index_tc_and_rag error (index mode):", e)
            return "Failed to summarize index/TC information for the given index."

    if ent_tcs:
        tc_code = (ent_tcs[0] or "").strip()
        if not tc_code:
            tc_code = "UNKNOWN_TC"
        source_meta["api_calls"].append({
            "api": "ptdb_tccode",
            "params": {"tc_code": tc_code},
            "result_summary": "Querying PTDB TC code for family info",
        })
        print("Entering TC info fetch:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        tc_rec = fetch_tc_info(tc_code)
        print("Finished TC info fetch:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print("normal_chat tc_rec (tc mode):", tc_rec)
        if tc_rec:
            source_meta["api_calls"][-1]["result_summary"] = f"Found TC record for {tc_code}"
        else:
            source_meta["api_calls"][-1]["result_summary"] = f"No TC record found for {tc_code}"
        if not tc_rec:
            return _fallback_pure_rag_answer(
                question,
                entity_type="TCCODE",
                entity_value=tc_code,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_meta=source_meta,
            )

        rag_symbol = ""
        raw = tc_rec.get("symbols")
        if isinstance(raw, str) and raw.strip():
            rag_symbol = raw.strip().split(",")[0].strip() if raw.strip() else ""
        elif isinstance(raw, list) and raw:
            rag_symbol = str(raw[0]).strip() if raw else ""

        chunks: list[dict] = []
        dois: list[str] = []
        display_entity = tc_code
        if rag_symbol:
            display_entity = rag_symbol
            source_meta["api_calls"].append({
                "api": "ptdb_symbol_tc_doi",
                "params": {"symbol": rag_symbol},
                "result_summary": f"Querying TC/DOI info for symbol {rag_symbol}",
            })
            print("Entering symbol->Doi:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            sym_tc = fetch_tc_symbol_doi_info(rag_symbol)
            print("Finished symbol->Doi:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            rel_list: list[dict] = []
            if sym_tc:
                doi_list = fetch_dois_by_symbol(rag_symbol)
                if doi_list:
                    doi_list = dedupe_preserve_order(doi_list)
                    rel_list = [{"properties": {"source_paper": d}} for d in doi_list]
                source_meta["api_calls"][-1]["result_summary"] = f"Found TC/DOI info for symbol {rag_symbol}"
            else:
                source_meta["api_calls"][-1]["result_summary"] = f"No TC/DOI info for symbol {rag_symbol}"
            if rel_list:
                source_meta["api_calls"].append({
                    "api": "vector_bm25_rerank",
                    "params": {"symbol": rag_symbol, "question": question[:80]},
                    "result_summary": "Retrieving literature chunks",
                })
                augmented = f"Target symbol/gene: {rag_symbol}\nQuestion: {question}"
                try:
                    rag_res = local_rag_chunks(augmented, rel_list, rag_symbol)
                    chunks = (rag_res or {}).get("chunks") or []
                    dois = (rag_res or {}).get("return_dois") or []
                    source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs"
                except Exception as e:
                    print("run_normal_chat retrieval error (tc→symbol RAG):", e)

        if not chunks:
            source_meta["api_calls"].append({
                "api": "unrestricted_rag",
                "params": {"tc_code": tc_code, "question": question[:80]},
                "result_summary": "Running unrestricted vector RAG as fallback",
            })
            try:
                if not rag_symbol:
                    fam = str((tc_rec.get("family") or "")).strip()
                    superfam = str((tc_rec.get("superfamily") or "")).strip()
                    kw_parts = [p for p in [fam, superfam] if p]
                    family_kw = " ; ".join(kw_parts) if kw_parts else ""

                    if family_kw:
                        augmented_q = (
                            f"TC code: {tc_code}\n"
                            f"TC family keywords: {family_kw}\n"
                            f"Question: {question}"
                        )
                    else:
                        augmented_q = f"Target TC code: {tc_code}\nQuestion: {question}"
                else:
                    augmented_q = f"Target TC code: {tc_code}\nQuestion: {question}"
                rag_res = simple_rag_chunks_unrestricted(augmented_q)
                chunks = (rag_res or {}).get("chunks") or []
                dois = (rag_res or {}).get("return_dois") or []
                source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs (unrestricted)"
            except Exception as e:
                print("run_normal_chat fallback retrieval error (tc→pure RAG):", e)

        try:
            result = summarize_index_tc_and_rag(
                question,
                display_entity,
                None,
                tc_rec,
                chunks,
                dois,
                deepseek_answer=deepseek_answer,
                stream_handler=stream_handler,
                source_metadata=source_meta,
            )
            result = _append_reference_summary_suggestions(result, dois, extra_chips=quick_links)
            result = _append_sources_evidence_marker(result, source_meta)
            _append_normal_cache(question, result, NORMAL_CACHE_PATH)
            return result
        except Exception as e:
            print("summarize_index_tc_and_rag error (tc mode):", e)
            return "Failed to summarize TC information for the given TC code."

    gene_tokens = looks_like_gene_name(question)
    if gene_tokens:
        _NON_ENTITY_STOPWORDS = {
            "which", "what", "list", "identify", "planttpdb", "find", "based", "infer",
            "compare", "how", "many", "report", "show", "describe", "summarize",
            "explain", "provide", "combining", "synthesize", "integrate", "discuss",
            "is", "the", "a", "an", "of", "for", "in", "on", "to", "by", "from",
            "with", "and", "or", "not", "no", "yes", "none", "unknown",
        }
        gene_tokens = [t for t in gene_tokens if t.lower().strip() not in _NON_ENTITY_STOPWORDS]
    if gene_tokens:
        token_str = ", ".join(gene_tokens)
        return _fallback_pure_rag_answer(
            question,
            entity_type="symbol",
            entity_value=gene_tokens[0],
            deepseek_answer=deepseek_answer,
            stream_handler=stream_handler,
            source_meta=source_meta,
        )

    q_lower = question.lower().strip()
    vague_pattern = re.search(
        r'\b(the|this|a)\s+transporter\b',
        q_lower,
    )
    if vague_pattern and not any(
        k in q_lower for k in ("example", "e.g.", "for instance", "such as")
    ):
        return (
            "Your question does not specify which transporter you are asking about. "
            "Please include a specific gene symbol (e.g., ALMT1, NRT1.1), "
            "TC code (e.g., 2.A.1), gene ID (e.g., AT4G12980), or protein family name.\n\n"
            "For example: 'Summarize the biological function and substrates "
            "of the transporter ALMT1 in Arabidopsis.'"
        )

    if not is_transporter_question(question):
        guidance = (
            "Your question does not appear to be about plant membrane or transporter proteins, "
            "which is the specific focus of this system. "
            "Please ask about plant transport proteins, their genes/symbols, TC families, substrates or mechanisms.\n\n"
            "Here are some example questions you can ask:\n"
            "1. Explain the TC classification and general transport mechanism of transporters in TC family 5.B.2.\n"
            "2. Based on available database information, describe the predicted structure, localization and general transporter type of index AT4G12980.\n"
            "3. What is AT3G55130?\n"
            "4. Summarize the reported biological function and substrates of the transporter symbol AZG in Arabidopsis.\n"
            "5. Summarize the biological roles, substrates and regulatory effects of the small amino acid transporter family OsAAP3 in rice tillering and yield.\n"
        )
        suggestions = [
            {
                "display": "TCCODE: TC 5.B.2 family overview",
                "input": "Explain the TC classification and general transport mechanism of transporters in TC family 5.B.2.",
            },
            {
                "display": "Index: AT4G12980 annotation",
                "input": "Based on available database information, describe the predicted structure, localization and general transporter type of index AT4G12980.",
            },
            {
                "display": "Index: AT3G55130 (What is …?)",
                "input": "What is AT3G55130?",
            },
            {
                "display": "Symbol: AZG function & substrates",
                "input": "Summarize the reported biological function and substrates of the transporter symbol AZG in Arabidopsis.",
            },
            {
                "display": "Protein family: OsAAP3 in rice",
                "input": "Summarize the biological roles, substrates and regulatory effects of the small amino acid transporter family OsAAP3 in rice tillering and yield.",
            },
        ]
        guidance += "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(suggestions, ensure_ascii=False)
        _append_normal_cache(question, guidance, NORMAL_CACHE_PATH)
        return guidance

    source_meta["api_calls"].append({
        "api": "unrestricted_rag",
        "params": {"question": question[:80]},
        "result_summary": "Running unrestricted vector RAG (no entities detected)",
    })
    try:
        rag_res = simple_rag_chunks_unrestricted(question)
    except Exception as e:
        print("run_normal_chat retrieval error (unrestricted mode):", e)
        return "Backend retrieval failed when running RAG. Please try again later."

    chunks = (rag_res or {}).get("chunks") or []
    dois = (rag_res or {}).get("return_dois") or []
    source_meta["api_calls"][-1]["result_summary"] = f"Retrieved {len(chunks)} chunks from {len(dois)} DOIs (unrestricted)"
    if not chunks:
        return (
            "No clear transporter-related entities (TC code, index, symbol, or protein family) "
            "could be extracted from your question, and no relevant literature chunks could be retrieved "
            "from the unrestricted corpus. Please try a more specific question."
        )

    resolved_symbol = "NONE"
    for doi in (dois or [])[:5]:
        syms = fetch_symbol_by_doi(doi)
        if syms:
            resolved_symbol = syms[0]
            source_meta["api_calls"].append({
                "api": "ptdb_doi_to_symbol",
                "params": {"doi": doi},
                "result_summary": f"Found symbol {resolved_symbol} for DOI {doi}",
            })
            break

    idx_rec = None
    tc_rec = None
    if resolved_symbol and resolved_symbol != "NONE":
        source_meta["api_calls"].append({
            "api": "ptdb_symbol_tc_doi",
            "params": {"symbol": resolved_symbol},
            "result_summary": f"Querying TC/DOI info for resolved symbol {resolved_symbol}",
        })
        try:
            sym_tc = fetch_tc_symbol_doi_info(resolved_symbol)
            if sym_tc:
                family_intro = (sym_tc.get("family") or "").strip()
                desc = (sym_tc.get("description") or "").strip()
                if family_intro or desc:
                    tc_rec = {
                        "tc_code": sym_tc.get("tc_code") or "",
                        "superfamily": sym_tc.get("superfamily") or "",
                        "family": family_intro,
                        "description": desc,
                    }
                source_meta["api_calls"][-1]["result_summary"] = f"Found TC info for symbol {resolved_symbol}"
            else:
                source_meta["api_calls"][-1]["result_summary"] = f"No TC info for symbol {resolved_symbol}"
        except Exception as e:
            print("fetch_tc_symbol_doi_info error (unrestricted mode):", e)

    try:
        result = summarize_index_tc_and_rag(
            question,
            resolved_symbol,
            idx_rec,
            tc_rec,
            chunks,
            dois,
            deepseek_answer=deepseek_answer,
            stream_handler=stream_handler,
            source_metadata=source_meta,
            guard_note=unmatched_entity_guard(question),
        )
        result = _append_reference_summary_suggestions(result, dois, extra_chips=quick_links)
        result = _append_sources_evidence_marker(result, source_meta)
        _append_normal_cache(question, result, NORMAL_CACHE_PATH)
        return result
    except Exception as e:
        print("summarize_index_tc_and_rag error (unrestricted mode):", e)
        return (
            "Failed to run literature-only summarization. Here are the top retrieved literature chunks (JSON):\n"
            + repr(chunks)
        )
