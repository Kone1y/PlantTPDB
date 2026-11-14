"""
PTDB index ID normalization and PTDB URL builders.
"""

from __future__ import annotations

import re

# Nuclear genes AT[1-5]G#####, mitochondrial ATMG#####, chloroplast ATCG#####
_AT_TAIR_PROTEIN_ISO = re.compile(
    r"^(AT(?:[1-5]G\d{5}|MG\d{5}|CG\d{5}))\.[0-9]+$",
    re.IGNORECASE,
)


def tair_locus_id_from_isoform(protein_id: str) -> str | None:
    m = _AT_TAIR_PROTEIN_ISO.match((protein_id or "").strip())
    return m.group(1).upper() if m else None


def ptdb_index_inid_candidates(protein_id: str) -> list[str]:
    pid = (protein_id or "").strip()
    if not pid:
        return []
    locus = tair_locus_id_from_isoform(pid)
    if not locus:
        return [pid]
    if locus == pid.upper():
        return [pid]
    return [locus, pid]


def ptdb_search_pid_for_url(protein_id: str, index_rec: dict | None = None) -> str:
    if isinstance(index_rec, dict) and index_rec:
        for key in ("GeneID", "query_id"):
            v = (index_rec.get(key) or "").strip()
            if v:
                return v.upper()
    pid = (protein_id or "").strip()
    locus = tair_locus_id_from_isoform(pid)
    return locus if locus else pid


_TAIR_PID_FOR_AUTOLINK = re.compile(
    r"\b((?:AT(?:[1-5]G\d{5}|MG\d{5}|CG\d{5})"
    r"|[A-Z][a-z]{1,3}\d{2,}[gG]\d{5,})"
    r"(?:\.\d+)?(?:_\w+)?)\b",
    re.IGNORECASE,
)

_PTD_ID_FORMAT = re.compile(
    r"^(?:"
    r"AT[1-5]G\d{5}(?:\.\d+)?|"
    r"ATM[GC]\d{5}(?:\.\d+)?|"
    r"[A-Z][a-z]{1,3}\d{2,}[gG]\d{5,}(?:\.\d+)?"
    r")$",
    re.IGNORECASE,
)


def _looks_like_ptd_id(text: str) -> bool:
    s = (text or "").strip()
    return bool(s and _PTD_ID_FORMAT.match(s))


def _paren_depth_for_autolink(text: str) -> list[int]:
    nloc = len(text)
    arr = [0] * nloc
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            arr[i] = depth
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
            arr[i] = depth
        else:
            arr[i] = depth
    return arr


def link_bare_tair_pids_in_text(text: str) -> str:
    s = text or ""
    if not s.strip():
        return s
    while True:
        pd = _paren_depth_for_autolink(s)
        n = len(s)
        best = None
        for m in _TAIR_PID_FOR_AUTOLINK.finditer(s):
            a, b = m.span()
            if any(pd[i] > 0 for i in range(a, min(b, n))):
                continue
            if a >= 1 and s[a - 1] == "[" and b + 2 <= n and s[b : b + 2] == "](":
                continue
            if best is None or m.start() > best.start():
                best = m
        if best is None:
            break
        a, b = best.span()
        display = s[a:b]
        url = ptdb_url_search(display, None)
        link = f"[{display}]({url})"
        s = s[:a] + link + s[b:]
    return s


PTDB_BASE = "https://yanglab.hzau.edu.cn/ptdb/index"

PTDB_INDEX_SEARCH_PREFIX = f"{PTDB_BASE}/search?pid="


def ptdb_url_search_index(alias: str) -> str:
    return f"{PTDB_BASE}/search_index?id={alias}"


def ptdb_url_search(pid: str, index_rec: dict | None = None) -> str:
    page_pid = ptdb_search_pid_for_url(pid, index_rec)
    return f"{PTDB_BASE}/search?pid={page_pid}"


def ptdb_url_tc_code(tc: str) -> str:
    return f"{PTDB_BASE}/tc_code?tc_code={tc}"


def filter_redundant_ptdb_index_link_chips(answer: str | None, chips: list[dict] | None) -> list[dict]:
    ans = answer or ""
    out: list[dict] = []
    for c in chips or []:
        if not isinstance(c, dict):
            continue
        inp = str(c.get("input") or "").strip()
        if inp.startswith(PTDB_INDEX_SEARCH_PREFIX) and inp in ans:
            continue
        out.append(c)
    return out
