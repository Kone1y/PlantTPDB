import os
import re
import json
import uuid
import numpy as np
import chromadb
from chromadb.config import Settings
from rank_bm25 import BM25Okapi
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
import requests
from typing import Callable

from .config import LLM_client, VDB_PATH, EMBEDDING_MODEL_PATH, RERANKER_MODEL_PATH
from .ptdb_utils import filter_redundant_ptdb_index_link_chips, link_bare_tair_pids_in_text, ptdb_url_search, ptdb_url_tc_code

from .citation_enrich import (
    enrich_parenthetical_citations_with_ptdb_titles,
    link_symbol_or_pid_to_ptdb,
    wrap_bare_citations,
)

from .guard import inject_guard, user_guard_prefix, intercept, detect_trap_type

try:
    import torch
except Exception:
    torch = None


def _auto_device() -> str:
    forced = (os.getenv("RAG_DEVICE") or "").strip().lower()
    if forced in {"cpu", "cuda"} or re.fullmatch(r"cuda:\d+", forced or ""):
        return forced
    if torch is not None and torch.cuda.is_available():
        return "cuda"
    return "cpu"


_RAG_DEVICE = _auto_device()
print(f"[rag] init device={_RAG_DEVICE}, CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '<unset>')}")

RAG_LOCAL_QUERY_N = int(os.getenv("RAG_LOCAL_QUERY_N", "20"))
RAG_SUMMARY_TOP_CHUNKS = int(os.getenv("RAG_SUMMARY_TOP_CHUNKS", "18"))

# =====================================================
# =====================================================
# Global singleton RAG resources (init once at import)
# =====================================================
DB_PATH = VDB_PATH
COLLECTION_NAME = "papers_rag_md"

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
    print(f"[rag] Embedding model init failed on device={_RAG_DEVICE}: {e}")
    if _RAG_DEVICE != "cpu":
        print("[rag] fallback Embedding model to cpu")
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
    print(f"[rag] CrossEncoder init failed on device={_RAG_DEVICE}: {e}")
    if _RAG_DEVICE != "cpu":
        print("[rag] fallback CrossEncoder to cpu")
        _RERANKER = CrossEncoder(
            RERANKER_MODEL_PATH,
            device="cpu",
        )
    else:
        raise

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "cache")
INDEX_CACHE_PATH = os.path.join(_CACHE_DIR, "index_cache.json")
SYMBOL_CACHE_PATH = os.path.join(_CACHE_DIR, "symbol_cache.json")
TCCODE_CACHE_PATH = os.path.join(_CACHE_DIR, "tccode_cache.json")


def _normalize_for_cache(text: str) -> str:
    if not text:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _load_specified_cache(path: str) -> list[dict]:
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_specified_cache(items: list[dict], path: str) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save specified cache failed:", e)


def _find_specified_cached(question: str, entity_value: str, path: str) -> tuple[str | None, list[str]]:
    qn = _normalize_for_cache(question)
    en = _normalize_for_cache(entity_value)
    if not qn:
        return None, []
    items = _load_specified_cache(path)
    for item in items:
        q = item.get("question_norm") or _normalize_for_cache(item.get("question", ""))
        e = item.get("entity_norm") or _normalize_for_cache(item.get("entity", ""))
        if q == qn and e == en:
            ans = item.get("answer")
            if isinstance(ans, str) and ans.strip():
                ans = _normalize_cached_answer_citations(ans)
                if "DOI: " not in ans:
                    dois = item.get("return_dois")
                    if isinstance(dois, list) and dois:
                        ans = _append_inline_dois(ans, dois)
                return ans, []
    return None, []


def _fetch_titles_by_dois(dois: list[str], timeout: int = 30) -> dict[str, str]:
    cleaned = dedupe_preserve_order(
        [_normalize_doi_for_query(str(d).strip()) for d in (dois or []) if _normalize_doi_for_query(str(d).strip())]
    )
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
                doi = _normalize_doi_for_query(str(rec.get("DOI") or "").strip())
                title = str(rec.get("Title") or "").strip()
                if doi and title:
                    out[doi] = title
        return out
    except Exception as e:
        print("rag.py _fetch_titles_by_dois error:", e)
        return {}


def _build_inline_doi_text(return_dois: list[str]) -> str:
    dois = dedupe_preserve_order([str(d).strip() for d in (return_dois or []) if str(d).strip()])
    if not dois:
        return ""
    return "DOI: " + "; ".join(dois)


def _append_inline_dois(answer: str, return_dois: list[str]) -> str:
    text = str(answer or "").rstrip()
    inline_doi_text = _build_inline_doi_text(return_dois)
    if not text or not inline_doi_text or inline_doi_text in text:
        return text

    marker = "__GUIDING_SUGGESTIONS__"
    if marker not in text:
        return f"{text}\n\n{inline_doi_text}"

    head, tail = text.split(marker, 1)
    head = head.rstrip()
    tail = tail.lstrip("\n")
    return f"{head}\n\n{inline_doi_text}\n\n{marker}\n{tail}"


def _normalize_cached_answer_citations(answer: str) -> str:
    text = str(answer or "").rstrip()
    ref_label = "**References Source:**"
    if ref_label not in text:
        return text

    marker = "__GUIDING_SUGGESTIONS__"
    if marker in text:
        main_text, tail = text.split(marker, 1)
        tail = f"{marker}\n{tail.lstrip(chr(10))}"
    else:
        main_text, tail = text, ""

    body_text, ref_text = main_text.split(ref_label, 1)
    dois = []
    for match in re.findall(r"10\.\d{4,9}/\S+", ref_text):
        doi = match.rstrip(".,;)\]\}")
        if doi:
            dois.append(doi)

    rebuilt = _append_inline_dois(body_text.rstrip(), dois)
    if not tail:
        return rebuilt
    return f"{rebuilt}\n\n{tail}"


def _build_reference_summary_suggestions(return_dois: list[str], max_k: int = 2) -> list[dict]:
    if not return_dois:
        return []
    dois = dedupe_preserve_order([str(d).strip() for d in (return_dois or []) if str(d).strip()])
    if not dois:
        return []
    suggestions: list[dict] = []
    for doi in dois[: max_k]:
        d = str(doi).strip()
        if not d:
            continue
        q = (
            f"Please summarize the paper with DOI {d}. "
            "Briefly describe its biological question, key methods, main findings, and conclusions in plant transporter research."
        )
        suggestions.append(
            {
                "display": f"Summarize: {d}",
                "input": q,
            }
        )
    return suggestions


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
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_base_info_by_id?inid={symbol}"
        elif api_name == "ptdb_smallfamily" and params.get("family"):
            family = params["family"]
            enriched["raw_url"] = f"https://yanglab.hzau.edu.cn/ptdb/Table/queryNeo4j.1 (POST: inid={family}, intype=name)"
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


def _append_specified_cache(
    question: str, entity_value: str, answer: str, return_dois: list[str], path: str
) -> None:
    if not question or not answer or not path:
        return
    answer_with_refs = _append_inline_dois(answer, return_dois or [])
    items = _load_specified_cache(path)
    items.append({
        "id": uuid.uuid4().hex,
        "question": question,
        "question_norm": _normalize_for_cache(question),
        "entity": entity_value,
        "entity_norm": _normalize_for_cache(entity_value),
        "answer": answer_with_refs,
        "return_dois": list(return_dois) if return_dois else [],
    })
    _save_specified_cache(items, path)


def _call_qwen(messages, temperature: float = 0.2) -> str:
    resp = LLM_client(
        model="qwen3-32b",
        messages=messages,
        temperature=temperature,
        extra_body={"enable_thinking": False},
    )
    return (resp.choices[0].message.content or "").strip()


def generate_guiding_suggestions(question: str, symbol: str | None, k: int = 3) -> list[dict]:
    q = (question or "").strip()
    sym = (symbol or "").strip()
    if not q:
        return []

    try:
        k = int(k)
    except Exception:
        k = 3
    if k < 1:
        k = 1
    if k > 5:
        k = 5

    prompt = f"""
You are generating suggested follow-up questions for a Plant Transport Protein QA chat UI.

Return ONLY a JSON array of objects. Each object MUST have exactly two string fields:
- "display": short label for a clickable chip (<= 80 characters)
- "input": the exact English question to fill into the input box

Context:
- Original user question: {q}
- Target transporter symbol (page context): {sym if sym else "NONE"}
- Number of suggestions: {k}

Important: The system data only has three types — TCCODE (e.g. 2.A.1), index (e.g. locus ID), and symbol (e.g. NRT1.1, OsAAP3). Every suggested question must clearly involve one of these. Do NOT suggest generic exploration or "other" unrelated proteins/families.

Rules:
- Output JSON only. No markdown. No extra keys.
- Suggestions must be in scope for plant transport proteins and must mention or imply a concrete TCCODE, index, or symbol.
- Avoid repeating the original question verbatim. Do NOT diverge to unrelated topics.
- Focus all suggestions on the current symbol if provided; otherwise suggest questions that name a specific symbol, or a TC code, or an index. Do NOT add suggestions that explore other transport proteins or unrelated families.
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
        print("❌ generate_guiding_suggestions (rag.py) parse error:", e)
        return []


def generate_guiding_suggestions_protein_family(
    question: str, protein_family: str | None, k: int = 3
) -> list[dict]:
    q = (question or "").strip()
    fam = (protein_family or "").strip()
    if not q:
        return []

    try:
        k = int(k)
    except Exception:
        k = 3
    if k < 1:
        k = 1
    if k > 5:
        k = 5

    prompt = f"""
You are generating suggested follow-up questions for a Plant Transport Protein QA chat UI.

Return ONLY a JSON array of objects. Each object MUST have exactly two string fields:
- "display": short label for a clickable chip (<= 80 characters)
- "input": the exact English question to fill into the input box

Context:
- Original user question: {q}
- Target protein family (page context): {fam if fam else "NONE"}
- Number of suggestions: {k}

Important: This page context is ONLY a protein family name (small family), not an individual symbol.
The system data types supported in this UI include: TCCODE, index, protein_family, and DOI.

Rules:
- Output JSON only. No markdown. No extra keys.
- Suggestions must be in scope for plant transport proteins.
- Prefer questions that explicitly mention the protein family name if available.
- Avoid repeating the original question verbatim. Do NOT diverge to unrelated topics.
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
        print("❌ generate_guiding_suggestions_protein_family (rag.py) parse error:", e)
        return []


# =====================================================
# 1. Utility functions
# =====================================================
def simple_tokenize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return [t for t in text.split() if t.strip()]


# =====================================================
# 2. Query Rewrite (OpenAI API)
# =====================================================
def rewrite_queries(question, k=3):

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
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        extra_body={"enable_thinking": False}
    )

    content = resp.choices[0].message.content.strip()

    try:
        queries = json.loads(content)
    except Exception:
        queries = [question]

    if not isinstance(queries, list):
        queries = [question]

    return queries[:k]


def dedupe_preserve_order(items):
    seen = set()
    out = []
    for x in items or []:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


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

def _normalize_doi_for_query(doi: str) -> str:
    s = (doi or "").strip()
    if not s:
        return s
    if re.match(r"^10/\d{4,9}/", s):
        s = "10." + s[3:]
    return s


# =====================================================
# 3. PTDB symbol → (DOIs + TC family info)
# =====================================================
def fetch_tc_symbol_doi_info(symbol: str, timeout: int = 120) -> dict | None:
    symbol = (symbol or "").strip()
    if not symbol:
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
        print("❌ fetch_tc_symbol_doi_info (rag.py) error:", e)
        return None


def fetch_dois_by_symbol(symbol: str, timeout: int = 120) -> list[str]:
    symbol = (symbol or "").strip()
    if not symbol:
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
        return dedupe_preserve_order(cleaned)
    except Exception as e:
        print("❌ fetch_dois_by_symbol (rag.py) error:", e)
        return []


# =====================================================
# 3.x PTDB helpers for specified index / TCCODE
# =====================================================
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
                return data[0] or None
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            print("❌ fetch_index_info (rag.py) error:", e, "inid=", inid)
    return None


def fetch_tc_info(tc_code: str, timeout: int = 120) -> dict | None:
    tc_code = (tc_code or "").strip().rstrip(".")
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
            return data[0] or None
        return data if isinstance(data, dict) else None
    except Exception as e:
        print("❌ fetch_tc_info (rag.py) error:", e)
        return None


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


def _split_csv_maybe(s: str) -> list[str]:
    txt = (s or "").strip()
    if not txt:
        return []
    parts = [p.strip() for p in txt.split(",")]
    return [p for p in parts if p]


def _compact_json_for_prompt(obj, *, max_chars: int = 8000) -> str:
    try:
        if isinstance(obj, dict):
            o = dict(obj)
        else:
            o = obj
        if isinstance(o, dict):
            base = o.get("base_info")
            if isinstance(base, dict) and "pep" in base:
                base2 = dict(base)
                pep = base2.pop("pep", None)
                if pep:
                    base2["pep__omitted"] = f"(omitted; length={len(str(pep))})"
                o["base_info"] = base2
        s = json.dumps(o, ensure_ascii=False)
    except Exception:
        try:
            s = repr(obj)
        except Exception:
            s = ""
    s = (s or "").strip()
    if max_chars > 0 and len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def fetch_index_and_tc_from_symbol(symbol: str, timeout: int = 1800) -> tuple[dict | None, dict | None]:
    sym = (symbol or "").strip()
    if not sym:
        return None, None
    try:
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_ptdb_agent_symbol_info",
            params={"insymbol": sym},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None, None
        first = data[0] or {}
        pid = (first.get("GeneID") or first.get("AthID") or first.get("Arabidopsis_PID") or "").strip()
        if not pid:
            return None, None
        idx = first if "base_info" in first else fetch_index_info(pid, timeout=timeout)
        if not isinstance(idx, dict) or not idx:
            return None, None
        tc_code = (idx.get("base_info") or {}).get("tc") or (idx.get("family_info") or {}).get("tc_code") or ""
        tc_code = str(tc_code).strip().rstrip(".") if tc_code else ""
        tc = fetch_tc_info(tc_code, timeout=timeout) if tc_code else None
        return idx, tc
    except Exception as e:
        print("❌ fetch_index_and_tc_from_symbol (rag.py) error:", e)
        return None, None


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
        lines.append("    - confidence: our pipeline's overall assessment of how reliable this PDB match is. Possible values: 'Experimentally validated' (self-hit confirmed), 'high' (strong evidence of homology), 'medium' (moderate evidence), 'low' (weak/unreliable match).")
        lines.append("    - is_highly_homologous: True when pident>30% AND high coverage — indicates a usable experimental structural template for comparative modeling.")
        lines.append("  When writing your answer, you MUST explicitly discuss: (1) what the confidence level means for match reliability — for example, 'Experimentally validated' means the query protein itself has a known experimental structure in PDB; 'high' means the PDB hit is a strong structural template; 'medium' means the template is usable but with caution; 'low' means the match is unreliable and should not be used as a structural template. (2) Whether the PDB hit provides a good structural template by discussing pident, e-value, qcov, and confidence together. For example: 'The top PDB hit (6XXX) has pident=42%, e-value=5e-23, qcov=85%, confidence=high, indicating a moderately homologous but statistically significant match with good coverage — a suitable structural template for homology modeling.' If the match is poor, state it clearly and note the limitations.")

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

    substrates: list[str] = []
    substrates_raw = rec.get("substrates")
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
        dois = [d for d in ([_normalize_doi_for_query(str(x).strip()) for x in dois_raw] if dois_raw else []) if d]
    elif isinstance(dois_raw, str) and dois_raw.strip():
        dois = [d for d in ([_normalize_doi_for_query(x.strip()) for x in dois_raw.split(",")] if dois_raw else []) if d]

    lines: list[str] = []
    lines.append("TC/family summary (parsed from PTDB):")
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


def _retrieve_chunks_by_dois(question: str, dois: list[str]) -> tuple[list[dict], list[str]]:
    if not dois:
        return [], []
    dois = dedupe_preserve_order([_normalize_doi_for_query(d) for d in dois if _normalize_doi_for_query(d)])
    if not dois:
        return [], []

    collection = _COLLECTION
    embedding_model = _EMBEDDING_MODEL
    reranker = _RERANKER

    # ---------- rewrite queries ----------
    rewrite_qs = rewrite_queries(question, 3)
    all_candidates = []

    bm25_base = collection.get(
        where={"DOI": {"$in": dois}},
        include=["documents", "metadatas"]
    )
    docs = bm25_base["documents"]
    metas = bm25_base["metadatas"]
    ids = bm25_base["ids"]
    bm25 = None
    if docs:
        tokenized_corpus = [simple_tokenize(d) for d in docs]
        bm25 = BM25Okapi(tokenized_corpus)

    # ---------- retrieval ----------
    for q in rewrite_qs:
        q_emb = embedding_model.embed_query(q)
        vec_res = collection.query(
            query_embeddings=[q_emb],
            n_results=RAG_LOCAL_QUERY_N,
            where={"DOI": {"$in": dois}},
            include=["documents", "metadatas"]
        )

        if vec_res["ids"] and len(vec_res["ids"][0]) > 0:
            for i in range(len(vec_res["ids"][0])):
                all_candidates.append({
                    "id": vec_res["ids"][0][i],
                    "text": vec_res["documents"][0][i],
                    "metadata": vec_res["metadatas"][0][i],
                })

        if bm25 is not None:
            scores = bm25.get_scores(simple_tokenize(q))

            topk = np.argsort(scores)[::-1][:RAG_LOCAL_QUERY_N]
            for idx in topk:
                all_candidates.append({
                    "id": ids[idx],
                    "text": docs[idx],
                    "metadata": metas[idx],
                })

    uniq = {}
    for c in all_candidates:
        uniq[c["id"]] = c
    candidates = list(uniq.values())
    if len(candidates) == 0:
        return [], []

    pairs = [[question, c["text"]] for c in candidates]
    scores = reranker.predict(pairs)
    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])
    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

    top_k = min(RAG_SUMMARY_TOP_CHUNKS, len(candidates))
    final_chunks = candidates[:top_k]
    return_dois = dedupe_preserve_order(
        [c.get("metadata", {}).get("DOI") for c in final_chunks if c.get("metadata", {}).get("DOI")]
    )
    return final_chunks, return_dois


def _collect_symbols_from_index_rec(idx_rec: dict) -> list[str]:
    if not isinstance(idx_rec, dict) or not idx_rec:
        return []
    base = idx_rec.get("base_info") or {}
    sym = idx_rec.get("Symbol") or base.get("Symbol")
    if sym is None:
        return []
    if isinstance(sym, list):
        return [str(s).strip() for s in sym if str(s).strip()]
    return [str(sym).strip()] if str(sym).strip() else []


def rag_index(
    question: str,
    index_value: str,
    stream_handler: Callable[[str], None] | None = None,
) -> dict:
    idx = (index_value or "").strip()
    if not idx:
        return {"final_answer": "Missing parameter: index.", "return_dois": []}

    rejection = intercept(question, mode="index", context=idx)
    if rejection is not None:
        return {"final_answer": rejection, "return_dois": []}

    cached_answer, _ = _find_specified_cached(question, idx, INDEX_CACHE_PATH)
    if cached_answer is not None:
        print("rag_index hit index_cache:", (question[:60] + "..." if len(question) > 60 else question))
        return {"final_answer": cached_answer, "return_dois": []}

    source_meta = _build_source_metadata()
    source_meta["api_calls"].append({
        "api": "ptdb_index",
        "params": {"pid": idx},
        "result_summary": "Queried PTDB index for protein annotation",
    })

    idx_rec = fetch_index_info(idx)
    all_symbols = _collect_symbols_from_index_rec(idx_rec or {})
    if idx_rec:
        source_meta["api_calls"][-1]["result_summary"] = f"Found record with {len(all_symbols)} symbol(s)"
    else:
        source_meta["api_calls"][-1]["result_summary"] = "No record found"

    if not all_symbols:
        return {"final_answer": f"Failed to reverse-lookup symbol for index: {idx}.", "return_dois": []}

    merged_dois = []
    first_sym_tc = None
    for sym in all_symbols:
        sym_tc = fetch_tc_symbol_doi_info(sym)
        if first_sym_tc is None and sym_tc:
            first_sym_tc = sym_tc
        source_meta["api_calls"].append({
            "api": "ptdb_symbol_tc_doi",
            "params": {"symbol": sym},
            "result_summary": f"Retrieved TC/DOI info for symbol {sym}" if sym_tc else f"No TC/DOI info for symbol {sym}",
        })
        if not sym_tc:
            continue
        dois_str = str(sym_tc.get("dois") or "").strip()
        if dois_str:
            merged_dois.extend([d.strip() for d in dois_str.split(",") if d.strip()])
    dois = dedupe_preserve_order(merged_dois)
    if not dois:
        return {
            "final_answer": f"No usable DOIs for any symbol from index: {idx} (checked: {', '.join(all_symbols[:10])}{'...' if len(all_symbols) > 10 else ''}).",
            "return_dois": [],
        }

    mapped_symbol = all_symbols[0]
    idx_parsed = ""
    try:
        idx_parsed = format_index_record(idx_rec or {}) if isinstance(idx_rec, dict) and idx_rec else ""
    except Exception:
        idx_parsed = ""
    family_parsed = ""
    try:
        family_parsed = format_tc_record(first_sym_tc or {}) if first_sym_tc else ""
    except Exception:
        family_parsed = ""
    family_info = {
        "symbol": mapped_symbol,
        "tc_code": (first_sym_tc.get("tc_code") if first_sym_tc else "") or "",
        "putative_substrates": (first_sym_tc.get("putative_substrates") if first_sym_tc else "") or "",
        "dois": (first_sym_tc.get("dois") if first_sym_tc else "") or "",
        "superfamily": (first_sym_tc.get("superfamily") if first_sym_tc else "") or "",
        "family": (first_sym_tc.get("family") if first_sym_tc else "") or "",
        "description": (first_sym_tc.get("description") if first_sym_tc else "") or "",
        "index_parsed": idx_parsed,
        "family_parsed": family_parsed,
        "index_raw": _compact_json_for_prompt(idx_rec or {}) if isinstance(idx_rec, dict) and idx_rec else "",
    }

    final_chunks, return_dois = _retrieve_chunks_by_dois(question, dois)
    if not final_chunks:
        return {"final_answer": "No relevant literature chunks could be retrieved for the mapped symbol/DOIs.", "return_dois": []}

    source_meta["api_calls"].append({
        "api": "vector_bm25_rerank",
        "params": {"dois_count": len(dois), "question": question[:80]},
        "result_summary": f"Retrieved {len(final_chunks)} chunks from {len(return_dois)} DOIs",
    })
    chunk_title_by_doi = _fetch_titles_by_dois(return_dois, timeout=30) if return_dois else {}
    for doi in return_dois:
        nd = _normalize_doi_for_query(str(doi).strip())
        chunks_for_doi = sum(1 for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
        chunk_texts_for_doi = [c.get("text", "") for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
        chunk_texts_for_doi = [t for t in chunk_texts_for_doi if _filter_chunk_noise(t)]
        source_meta["literature"].append({
            "doi": doi,
            "title": chunk_title_by_doi.get(nd, ""),
            "chunks_used": chunks_for_doi,
            "chunks": chunk_texts_for_doi,
        })

    final_answer = generate_final_answer(
        question,
        final_chunks,
        protein_family=mapped_symbol,
        family_info=family_info,
        stream_handler=stream_handler,
    )
    final_answer = link_symbol_or_pid_to_ptdb(final_answer, mapped_symbol, idx, idx_rec)
    final_answer = link_bare_tair_pids_in_text(final_answer)

    source_meta["inference_notes"].append(
        "See the \"AI Inference Note\" paragraph at the end of the answer for details on which parts are AI-inferred."
    )

    suggestions = [
        {
            "display": f"DB summary: {idx}",
            "input": (
                f"Based on available database information, describe the predicted structure, localization and general transporter type of index {idx}."
            ),
        }
    ] + _build_reference_summary_suggestions(return_dois)
    suggestions.append(
        {
            "display": f"Open PTDB index page for {idx}",
            "input": ptdb_url_search(idx, idx_rec),
        }
    )
    suggestions = filter_redundant_ptdb_index_link_chips(final_answer, suggestions)
    if suggestions:
        final_answer += "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(suggestions, ensure_ascii=False)

    final_answer = _append_sources_evidence_marker(final_answer, source_meta)

    _append_specified_cache(question, idx, final_answer, return_dois, INDEX_CACHE_PATH)
    return {"final_answer": final_answer, "return_dois": []}


def _collect_symbols_from_tc_rec(tc_rec: dict) -> list[str]:
    raw_syms = tc_rec.get("symbols")
    if isinstance(raw_syms, str) and raw_syms.strip():
        return [s.strip() for s in raw_syms.split(",") if s.strip()]
    if isinstance(raw_syms, list):
        return [str(s).strip() for s in raw_syms if str(s).strip()]
    return []


def rag_tccode(
    question: str,
    tccode_value: str,
    stream_handler: Callable[[str], None] | None = None,
) -> dict:
    tc = (tccode_value or "").strip().rstrip(".")
    if not tc:
        return {"final_answer": "Missing parameter: tccode.", "return_dois": []}

    rejection = intercept(question, mode="tccode", context=tc)
    if rejection is not None:
        return {"final_answer": rejection, "return_dois": []}

    cached_answer, _ = _find_specified_cached(question, tc, TCCODE_CACHE_PATH)
    if cached_answer is not None:
        print("rag_tccode hit tccode_cache:", (question[:60] + "..." if len(question) > 60 else question))
        return {"final_answer": cached_answer, "return_dois": []}

    source_meta = _build_source_metadata()
    source_meta["api_calls"].append({
        "api": "ptdb_tccode",
        "params": {"tc_code": tc},
        "result_summary": "Queried PTDB TC code for family info",
    })

    tc_rec = fetch_tc_info(tc)
    all_symbols = []
    tc_family_info = None
    if isinstance(tc_rec, dict) and tc_rec:
        all_symbols = _collect_symbols_from_tc_rec(tc_rec)
        source_meta["api_calls"][-1]["result_summary"] = f"Found TC family with {len(all_symbols)} symbol(s)"
    else:
        source_meta["api_calls"][-1]["result_summary"] = "No TC record found"

    first_symbol = (all_symbols[0] if all_symbols else "").strip()
    tc_parsed = ""
    try:
        tc_parsed = format_tc_record(tc_rec)
    except Exception:
        tc_parsed = ""
    tc_family_info = {
        "symbol": first_symbol or f"TCCODE:{tc}",
        "tc_code": (tc_rec.get("tc_code") or tc).rstrip("."),
        "superfamily": tc_rec.get("superfamily") or "",
        "family": tc_rec.get("family") or "",
        "description": tc_rec.get("description") or "",
        "symbols": tc_rec.get("symbols") or "",
        "substrates": tc_rec.get("substrates") or "",
        "tc_parsed": tc_parsed,
        "tc_raw": _compact_json_for_prompt(tc_rec),
    }

    if not all_symbols:
        return {"final_answer": f"Failed to reverse-lookup representative symbol for TCCODE: {tc}.", "return_dois": []}

    merged_dois = []
    for sym in all_symbols:
        doi_list = fetch_dois_by_symbol(sym)
        if doi_list:
            merged_dois.extend(doi_list)
            source_meta["api_calls"].append({
                "api": "ptdb_symbol_to_doi",
                "params": {"symbol": sym},
                "result_summary": f"Retrieved {len(doi_list)} DOIs for symbol {sym}",
            })
        else:
            sym_tc = fetch_tc_symbol_doi_info(sym)
            source_meta["api_calls"].append({
                "api": "ptdb_symbol_tc_doi",
                "params": {"symbol": sym},
                "result_summary": f"Retrieved TC/DOI info for symbol {sym}" if sym_tc else f"No TC/DOI info for symbol {sym}",
            })
            if sym_tc:
                dois_str = str(sym_tc.get("dois") or "").strip()
                if dois_str:
                    merged_dois.extend([d.strip() for d in dois_str.split(",") if d.strip()])
    dois = dedupe_preserve_order(merged_dois)
    if not dois:
        return {
            "final_answer": f"No usable DOIs for any symbol in TCCODE: {tc} (checked: {', '.join(all_symbols[:10])}{'...' if len(all_symbols) > 10 else ''}).",
            "return_dois": [],
        }

    mapped_symbol = all_symbols[0]
    family_info = {
        "symbol": mapped_symbol,
        "tc_code": (tc_family_info.get("tc_code") if tc_family_info else "") or tc,
        "superfamily": (tc_family_info.get("superfamily") if tc_family_info else "") or "",
        "family": (tc_family_info.get("family") if tc_family_info else "") or "",
        "description": (tc_family_info.get("description") if tc_family_info else "") or "",
        "tc_parsed": (tc_family_info.get("tc_parsed") if tc_family_info else "") or "",
        "tc_raw": (tc_family_info.get("tc_raw") if tc_family_info else "") or "",
    }

    final_chunks, return_dois = _retrieve_chunks_by_dois(question, dois)
    if not final_chunks:
        return {"final_answer": "No relevant literature chunks could be retrieved for the mapped symbol/DOIs.", "return_dois": []}

    source_meta["api_calls"].append({
        "api": "vector_bm25_rerank",
        "params": {"dois_count": len(dois), "question": question[:80]},
        "result_summary": f"Retrieved {len(final_chunks)} chunks from {len(return_dois)} DOIs",
    })
    chunk_title_by_doi = _fetch_titles_by_dois(return_dois, timeout=30) if return_dois else {}
    for doi in return_dois:
        nd = _normalize_doi_for_query(str(doi).strip())
        chunks_for_doi = sum(1 for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
        chunk_texts_for_doi = [c.get("text", "") for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
        chunk_texts_for_doi = [t for t in chunk_texts_for_doi if _filter_chunk_noise(t)]
        source_meta["literature"].append({
            "doi": doi,
            "title": chunk_title_by_doi.get(nd, ""),
            "chunks_used": chunks_for_doi,
            "chunks": chunk_texts_for_doi,
        })

    final_answer = generate_final_answer(
        question,
        final_chunks,
        protein_family=mapped_symbol,
        family_info=family_info,
        stream_handler=stream_handler,
    )
    final_answer = link_bare_tair_pids_in_text(final_answer)

    source_meta["inference_notes"].append(
        "See the \"AI Inference Note\" paragraph at the end of the answer for details on which parts are AI-inferred."
    )

    suggestions = _build_reference_summary_suggestions(return_dois)
    suggestions.append(
        {
            "display": f"Open PTDB TC page for {tc}",
            "input": ptdb_url_tc_code(tc),
        }
    )
    if suggestions:
        final_answer += "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(suggestions, ensure_ascii=False)

    final_answer = _append_sources_evidence_marker(final_answer, source_meta)

    _append_specified_cache(question, tc, final_answer, return_dois, TCCODE_CACHE_PATH)
    return {"final_answer": final_answer, "return_dois": []}


# =====================================================
# 3. Document Retrieval (symbol-designated entity page pipeline)
# =====================================================
def docRetrieval(
    question,
    relationship,
    symbol=None,
    stream_handler: Callable[[str], None] | None = None,
):
    symbol_name = (symbol or "").strip()

    rejection = intercept(question, mode="symbol", context=symbol_name)
    if rejection is not None:
        return {"final_answer": rejection, "return_dois": []}

    family_info = None
    source_meta = _build_source_metadata()

    cached_answer, _ = _find_specified_cached(question, symbol_name, SYMBOL_CACHE_PATH)
    if cached_answer is not None:
        print("docRetrieval hit symbol_cache:", (question[:60] + "..." if len(question) > 60 else question))
        return {"final_answer": cached_answer, "return_dois": []}

    # ---------- 3.0 First fetch the literature list via the symbol → DOIs API ----------
    dois: list[str] = []
    if symbol_name:
        dois = fetch_dois_by_symbol(symbol_name)
        source_meta["api_calls"].append({
            "api": "ptdb_symbol_to_doi",
            "params": {"symbol": symbol_name},
            "result_summary": f"Found {len(dois)} DOI(s)" if dois else "No DOIs found",
        })

    if not dois:
        relationships = relationship or []
        for r in relationships or []:
            source_paper = (r.get("properties") or {}).get("source_paper")
            if source_paper:
                dois.append(source_paper)
        if relationships:
            source_meta["api_calls"].append({
                "api": "fallback_relationships",
                "params": {"relationships_count": len(relationships)},
                "result_summary": f"Extracted {len(dois)} DOI(s) from relationships",
            })

    dois = dedupe_preserve_order([_normalize_doi_for_query(d) for d in dois if _normalize_doi_for_query(d)])

    if not dois:
        return {
            "final_answer": (
                f"No usable DOIs could be retrieved for the current symbol ({symbol_name or 'UNKNOWN'}). "
                "Please verify the symbol input or try another question."
            ),
            "return_dois": [],
        }

    # ---------- 3.1 optional: fetch TC / family background information ----------
    sym_tc = fetch_tc_symbol_doi_info(symbol_name) if symbol_name else None
    if sym_tc:
        source_meta["api_calls"].append({
            "api": "ptdb_symbol_tc_doi",
            "params": {"symbol": symbol_name},
            "result_summary": f"Retrieved TC/DOI info: tc_code={sym_tc.get('tc_code', 'N/A')}",
        })
        family_parsed = ""
        try:
            family_parsed = format_tc_record(sym_tc)
        except Exception:
            family_parsed = ""
        family_info = {
            "symbol": symbol_name,
            "tc_code": sym_tc.get("tc_code") or "",
            "putative_substrates": sym_tc.get("putative_substrates") or "",
            "dois": sym_tc.get("dois") or "",
            "superfamily": sym_tc.get("superfamily") or "",
            "family": sym_tc.get("family") or "",
            "description": sym_tc.get("description") or "",
            "family_parsed": family_parsed,
        }

    idx_rec = None
    tc_rec = None
    if symbol_name:
        try:
            idx_rec, tc_rec = fetch_index_and_tc_from_symbol(symbol_name)
            if idx_rec or tc_rec:
                source_meta["api_calls"].append({
                    "api": "ptdb_symbol_index_tc",
                    "params": {"symbol": symbol_name},
                    "result_summary": f"Retrieved index={bool(idx_rec)}, tc={bool(tc_rec)}",
                })
        except Exception:
            idx_rec, tc_rec = None, None
    if family_info is None:
        family_info = {"symbol": symbol_name}
    try:
        if isinstance(idx_rec, dict) and idx_rec:
            family_info["index_parsed"] = format_index_record(idx_rec)
            family_info["index_raw"] = _compact_json_for_prompt(idx_rec)
    except Exception:
        pass
    try:
        if isinstance(tc_rec, dict) and tc_rec:
            family_info["tc_parsed"] = format_tc_record(tc_rec)
            family_info["tc_raw"] = _compact_json_for_prompt(tc_rec)
    except Exception:
        pass

    # ---------- 3.2 use global singleton resources ----------
    collection = _COLLECTION
    embedding_model = _EMBEDDING_MODEL
    reranker = _RERANKER

    # ---------- 3.5 rewrite queries ----------
    rewrite_qs = rewrite_queries(question, 3)

    all_candidates = []

    bm25_base = collection.get(
        where={"DOI": {"$in": dois}},
        include=["documents", "metadatas"]
    )
    docs = bm25_base["documents"]
    metas = bm25_base["metadatas"]
    ids = bm25_base["ids"]
    bm25 = None
    if docs:
        tokenized_corpus = [simple_tokenize(d) for d in docs]
        bm25 = BM25Okapi(tokenized_corpus)

    # ---------- 3.6 retrieval ----------
    for q in rewrite_qs:

        # ===== vector retrieval =====
        q_emb = embedding_model.embed_query(q)

        vec_res = collection.query(
            query_embeddings=[q_emb],
            n_results=RAG_LOCAL_QUERY_N,
            where={"DOI": {"$in": dois}},
            include=["documents", "metadatas"]
        )

        if vec_res["ids"] and len(vec_res["ids"][0]) > 0:
            for i in range(len(vec_res["ids"][0])):
                all_candidates.append({
                    "id": vec_res["ids"][0][i],
                    "text": vec_res["documents"][0][i],
                    "metadata": vec_res["metadatas"][0][i],
                })

        # ===== BM25 retrieval =====
        if bm25 is not None:
            scores = bm25.get_scores(simple_tokenize(q))

            topk = np.argsort(scores)[::-1][:RAG_LOCAL_QUERY_N]
            for idx in topk:
                all_candidates.append({
                    "id": ids[idx],
                    "text": docs[idx],
                    "metadata": metas[idx],
                })

    # ---------- 3.7 dedup ----------
    uniq = {}
    for c in all_candidates:
        uniq[c["id"]] = c
    candidates = list(uniq.values())

    if len(candidates) == 0:
        return {
            "final_answer": "No relevant literature chunks could be retrieved for the current protein family / DOIs. Please verify the protein family relationship configuration or try another question.",
            "return_dois": [],
        }

    # =====================================================
    # 3.8 rerank with local bge-reranker-large
    # =====================================================
    pairs = [[question, c["text"]] for c in candidates]

    scores = reranker.predict(pairs)

    for i, c in enumerate(candidates):
        c["rerank_score"] = float(scores[i])


    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

    # ---------- 3.9 debug print: top retrieved chunks ----------
    dbg_n = min(12, len(candidates))
    print(f"\n===== Retrieved Chunks (Top {dbg_n}) =====")
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
        [c.get("metadata", {}).get("DOI") for c in final_chunks if c.get("metadata", {}).get("DOI")]
    )

    source_meta["api_calls"].append({
        "api": "vector_bm25_rerank",
        "params": {"dois_count": len(dois), "question": question[:80]},
        "result_summary": f"Retrieved {len(final_chunks)} chunks from {len(return_dois)} DOIs",
    })
    chunk_title_by_doi = _fetch_titles_by_dois(return_dois, timeout=30) if return_dois else {}
    for doi in return_dois:
        nd = _normalize_doi_for_query(str(doi).strip())
        chunks_for_doi = sum(1 for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd)
        chunk_texts_for_doi = [c.get("text", "") for c in final_chunks if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip()) == nd]
        chunk_texts_for_doi = [t for t in chunk_texts_for_doi if _filter_chunk_noise(t)]
        source_meta["literature"].append({
            "doi": doi,
            "title": chunk_title_by_doi.get(nd, ""),
            "chunks_used": chunks_for_doi,
            "chunks": chunk_texts_for_doi,
        })

    final_answer = generate_final_answer(
        question,
        final_chunks,
        protein_family=symbol_name or symbol,
        family_info=family_info,
        stream_handler=stream_handler,
    )
    _dpid = ""
    if isinstance(idx_rec, dict) and idx_rec:
        _dpid = (idx_rec.get("Protein_ID") or idx_rec.get("AthID") or (idx_rec.get("base_info") or {}).get("pid") or "").strip()
    final_answer = link_symbol_or_pid_to_ptdb(final_answer, symbol_name, _dpid, idx_rec)
    final_answer = link_bare_tair_pids_in_text(final_answer)

    source_meta["inference_notes"].append(
        "See the \"AI Inference Note\" paragraph at the end of the answer for details on which parts are AI-inferred."
    )

    suggestions = _build_reference_summary_suggestions(return_dois)
    if suggestions:
        final_answer += "\n\n__GUIDING_SUGGESTIONS__\n" + json.dumps(suggestions, ensure_ascii=False)

    final_answer = _append_sources_evidence_marker(final_answer, source_meta)

    _append_specified_cache(question, symbol_name, final_answer, return_dois, SYMBOL_CACHE_PATH)
    return {"final_answer": final_answer, "return_dois": []}


# =====================================================
# 4. Final Answer Generation (OpenAI API)
# =====================================================
def generate_final_answer(
    question,
    final_chunks,
    protein_family=None,
    family_info=None,
    stream_handler: Callable[[str], None] | None = None,
):

    chunk_dois = dedupe_preserve_order(
        [
            _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip())
            for c in final_chunks
            if _normalize_doi_for_query(str((c.get("metadata") or {}).get("DOI") or "").strip())
        ]
    )
    title_by_doi = _fetch_titles_by_dois(chunk_dois, timeout=30) if chunk_dois else {}

    context_blocks = []
    for i, c in enumerate(final_chunks):
        meta = c.get("metadata") or {}
        raw_doi = meta.get("DOI")
        nd = _normalize_doi_for_query(str(raw_doi or "").strip())
        ptdb_title = (title_by_doi.get(nd) or "").strip()
        title_line = f"PTDB title: {ptdb_title}\n" if ptdb_title else ""
        block = f"""
[Chunk {i+1}]
DOI: {raw_doi}
{title_line}Section: {meta.get("Section")}

{c["text"]}
"""
        context_blocks.append(block)

    context = "\n".join(context_blocks)

    family_context = ""
    if family_info:
        parsed_bits = []
        idx_parsed = (family_info.get("index_parsed") or "").strip()
        if idx_parsed:
            parsed_bits.append(idx_parsed)
        tc_parsed = (family_info.get("tc_parsed") or family_info.get("family_parsed") or "").strip()
        if tc_parsed:
            parsed_bits.append(tc_parsed)
        if parsed_bits:
            family_context += "Database-level parsed summaries (from PTDB):\n" + "\n\n".join(parsed_bits) + "\n\n"

        raw_bits = []
        idx_raw = (family_info.get("index_raw") or "").strip()
        if idx_raw:
            raw_bits.append("INDEX record (compact JSON):\n" + idx_raw)
        tc_raw = (family_info.get("tc_raw") or "").strip()
        if tc_raw:
            raw_bits.append("TCCODE record (compact JSON):\n" + tc_raw)
        if raw_bits:
            family_context += "Database raw records (compact; for factual grounding):\n" + "\n\n".join(raw_bits) + "\n\n"

        sym = (family_info.get("symbol") or protein_family or "").strip()
        if sym:
            family_context += f"Target transporter symbol: {sym}\n"
        tc_code = (family_info.get("tc_code") or "").strip()
        superfamily = (family_info.get("superfamily") or "").strip()
        fam_name = (family_info.get("family") or "").strip()
        desc = (family_info.get("description") or "").strip()
        if tc_code or superfamily or fam_name or desc:
            family_context += "Database-level family/TC background (from PTDB):\n"
            if tc_code:
                family_context += f"- TC code: {tc_code}\n"
            if superfamily:
                family_context += f"- Superfamily: {superfamily}\n"
            if fam_name:
                family_context += f"- Family: {fam_name}\n"
            if desc:
                family_context += f"- Description: {desc}\n"
            family_context += "\n"
    elif protein_family:
        family_context = f"Target protein family name: {protein_family}\n"

    rejection = intercept(question, mode="symbol")
    if rejection is not None:
        return rejection

    warn_value = detect_trap_type(question) == "warn_value"
    warn_extra = ""
    if warn_value:
        warn_extra = (
            "\n\n*** CRITICAL: The answer MUST explicitly include the exact value if it is present "
            "in the literature chunks. If the value is NOT explicitly stated in any chunk, "
            "write ONLY: 'This value is not available in the database.' "
            "Do NOT estimate, interpolate, or cite similar-but-different values. ***\n"
        )

    prompt = f"""
{user_guard_prefix(question)}
You are an academic assistant for plant transporter and membrane protein literature interpretation.
{warn_extra}
{family_context}Answer the question STRICTLY based on the database context above (if present) and the literature chunks below.
Do NOT use external knowledge.

Question:
{question}

Literature chunks:
{context}

Writing requirements:
- If database context is present above, naturally integrate the key database annotations early (preferably within the first paragraph) in plain prose. Do NOT use a titled section and do NOT use bullet points for the database info.
- If the database context includes a TC code, you MUST explicitly mention that TC code in the first paragraph, and clearly attribute it to database annotation. Do not guess a TC code when it is absent.
- STRUCTURE EVIDENCE — Always report EXPERIMENTAL and PREDICTED evidence as two clearly separated statements:
  1. If confidence = "Experimentally validated" OR is_same_protein = true: STATE CLEARLY that this protein HAS ITS OWN experimental structure; give the PDB ID. NEVER write "no experimental validation", "no independent experimental structure", or "no external template" — those are WRONG.
  2. AlphaFold pLDDT/pTM are PREDICTED metrics — label them as predicted; never present them as experimental confirmation.
  3. PDB method/resolution/ligand: report only if present; if absent, do NOT mention them at all — simply give the PDB ID.
- Pfam coverage: enumerate EVERY Pfam ID in the input. For each, name the domain, its typical function, and relate it to this transporter's family. Do not silently drop any Pfam ID.
- Then write 3–5 coherent paragraphs synthesizing the literature evidence, and integrate the database annotations when relevant.
- Use a rigorous academic tone and provide a deeper synthesis than a surface-level paraphrase.
- When the chunks contain them, explicitly cover: biological background, experimental approaches/methods, key results, mechanistic interpretation, and biological implications.
- Add 1 short paragraph on limitations/uncertainties grounded in what is missing or ambiguous in the chunks.
- EVIDENCE ROUTING — classify every sentence:
  - [DATABASE-GROUNDED] (TC, Pfam, TM topology, localization, MW/pI/length, AlphaFold, PDB BLAST): back with the DATA link; NEVER attach a DOI.
  - [LITERATURE-GROUNDED] (drawn from a chunk): cite that chunk's DOI after the 2-3 sentences it supports.
  - [AI-inferred] (your own interpretation, not in any DB field or chunk): no DOI. Append [AI-inferred] at the END of every inferred sentence/block.
- RELEVANCE GATE: A DOI may be cited ONLY if its chunk is about the SAME protein, SAME family (matching TC/Pfam), or SAME mechanism as the sentence it supports. If a chunk is about a DIFFERENT protein/family — NEVER cite it. Citing an unrelated chunk is WORSE than leaving a database fact uncited. If NO chunk matches, the answer must rely on PTDB records only.
- After every 2–3 sentences that use literature evidence, append citation(s) in parentheses. Each citation MUST be (10.xxxx/yyyy; concise exact or lightly shortened PTDB title) when that chunk's header includes a PTDB title; if absent, use (10.xxxx/yyyy) only. Never invent titles.
- If the chunk headers show two or more distinct DOI strings, parenthetical citations from at least two different DOIs must appear across the answer.
- Do NOT add a separate references section, a "**References Source:**" block, or a trailing line starting with "DOI:".
- After the core answer, append a "Sources & Evidence" panel as the VERY LAST block:
  **Sources & Evidence**
  - References: ONLY the DOIs actually cited inline above. If none were cited, write: "No literature evidence was used; this answer relies on PTDB records only."
   - Data entries: the data card link(s) with GeneID, Protein_ID, TC code.
  - Structures: Experimental — PDB ID; Predicted — AlphaFold3 model ID with pLDDT/pTM.
  - Uncertainty / inferred: bullet the SPECIFIC inferred statements.
  CONSISTENCY: panel's References MUST equal the DOIs cited inline.
Do NOT add an "AI Inference Note" section.
"""

    messages = [
        {"role": "system", "content": inject_guard("You answer from retrieved literature. When chunk headers include PTDB titles, every citation parenthesis must place the DOI first, then a semicolon, then the title. The final answer MUST end with a \"Sources & Evidence\" panel. The panel's reference list must contain exactly the DOIs cited inline in the body — never more, never fewer.")},
        {"role": "user", "content": prompt},
    ]

    defer_stream = stream_handler is not None

    if stream_handler:
        stream = LLM_client(
            model="deepseek-v4-flash",
            messages=messages,
            temperature=0.2,
            stream=True,
            )
        pieces = []
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if not delta:
                continue
            pieces.append(delta)
            if not defer_stream:
                stream_handler(delta)
        raw = "".join(pieces)
    else:
        resp = LLM_client(
            model="deepseek-v4-flash",
            messages=messages,
            temperature=0.2,
            extra_body={"enable_thinking": False}
        )
        raw = (resp.choices[0].message.content or "").strip()

    raw = wrap_bare_citations(raw)
    out = enrich_parenthetical_citations_with_ptdb_titles(
        raw, title_by_doi, normalize_doi=_normalize_doi_for_query
    )
    out = link_bare_tair_pids_in_text(out)

    if defer_stream and stream_handler:
        stream_handler(out)
    return out


