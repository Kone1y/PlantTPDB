"""
Deterministic citation enrichment of answer text: bare DOIs become (DOI; PTDB title);
legacy (Title; DOI) model output is normalized.
"""

from __future__ import annotations

import re
from typing import Callable

from .ptdb_utils import link_bare_tair_pids_in_text, ptdb_search_pid_for_url, ptdb_url_search, ptdb_url_search_index, _looks_like_ptd_id

_BARE_DOI = re.compile(r"^10\.\d{4,9}/[^\s)]+$")


def _norm_doi(s: str) -> str:
    s = (s or "").strip().rstrip(".,;)")
    if not s:
        return s
    if re.match(r"^10/\d{4,9}/", s):
        s = "10." + s[3:]
    return s


def _is_bare_doi(seg: str) -> bool:
    return bool(_BARE_DOI.match((seg or "").strip()))


def _short_title(t: str, max_len: int = 160) -> str:
    t = (t or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3].rstrip() + "..."


def enrich_parenthetical_citations_with_ptdb_titles(
    text: str,
    title_by_doi: dict[str, str],
    *,
    normalize_doi: Callable[[str], str] | None = None,
) -> str:
    if not (text or "").strip() or not title_by_doi:
        return text or ""

    norm = normalize_doi or _norm_doi
    tm: dict[str, str] = {}
    for k, v in title_by_doi.items():
        nk = norm(str(k).strip())
        vv = (str(v) or "").strip()
        if nk and vv:
            tm[nk] = vv
    if not tm:
        return text

    paren = re.compile(r"\(([^)]*10\.\d{4,9}/[^)]*)\)")

    def repl(m: re.Match) -> str:
        inner = (m.group(1) or "").strip()
        parts_semi = [x.strip() for x in inner.split(";")]
        parts_semi = [p for p in parts_semi if p]
        if not parts_semi:
            return m.group(0)

        if (
            len(parts_semi) == 2
            and not _is_bare_doi(parts_semi[0])
            and _is_bare_doi(parts_semi[1])
        ):
            nd = norm(parts_semi[1])
            tit = tm.get(nd)
            if tit:
                return f"({nd}; {_short_title(tit)})"
            return m.group(0)

        if not _is_bare_doi(parts_semi[0]):
            return m.group(0)

        if len(parts_semi) >= 2 and _is_bare_doi(parts_semi[0]) and not _is_bare_doi(parts_semi[1]):
            return m.group(0)

        if not all(_is_bare_doi(p) for p in parts_semi):
            return m.group(0)

        out_parts: list[str] = []
        for seg in parts_semi:
            nd = norm(seg)
            tit = tm.get(nd)
            if tit:
                out_parts.append(f"{nd}; {_short_title(tit)}")
            else:
                out_parts.append(nd)
        return "(" + "; ".join(out_parts) + ")"

    return paren.sub(repl, text)


def wrap_bare_citations(text: str) -> str:
    if not text:
        return text

    text = re.sub(r'([^\s(;])(10\.\d{4,9}/)', r'\1; \2', text)

    _DOI_START = re.compile(r'10\.\d{4,9}/')
    result: list[str] = []
    pos = 0
    depth = 0
    n = len(text)

    while pos < n:
        ch = text[pos]
        if ch == '(':
            depth += 1
            result.append(ch)
            pos += 1
        elif ch == ')':
            depth = max(0, depth - 1)
            result.append(ch)
            pos += 1
        elif depth == 0 and _DOI_START.match(text[pos:]):
            doi_end = pos
            while doi_end < n and text[doi_end] not in ' \t\n;)':
                doi_end += 1
            while doi_end > pos and text[doi_end - 1] in ".…,;:":
                doi_end -= 1
            cite_end = doi_end

            if doi_end < n and text[doi_end] == ';':
                title_pos = doi_end + 1
                if title_pos < n and text[title_pos] == ' ':
                    title_pos += 1
                j = title_pos
                while j < n:
                    if (text[j] == '.' and j + 2 < n
                            and text[j + 1] == ' ' and text[j + 2].isupper()):
                        break
                    if text[j] == '\n':
                        break
                    j += 1
                cite_end = j

            if cite_end > pos and text[cite_end - 1] == '.':
                cite_end -= 1

            result.append('(')
            result.append(text[pos:cite_end])
            result.append(')')
            pos = cite_end
        else:
            result.append(ch)
            pos += 1

    return ''.join(result)


def link_pid_in_text(text: str, pid: str, index_rec: dict | None = None) -> str:
    if not text or not pid:
        return text
    pid = pid.strip()
    if not pid or f"[{pid}]" in text:
        return text

    n = len(text)
    paren_depth = [0] * n
    depth = 0
    for i, ch in enumerate(text):
        if ch == '(':
            paren_depth[i] = depth
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
            paren_depth[i] = depth
        else:
            paren_depth[i] = depth

    pos = 0
    pid_lower = pid.lower()
    text_lower = text.lower()
    while True:
        idx = text_lower.find(pid_lower, pos)
        if idx == -1:
            return text
        pid_end = idx + len(pid)
        if any(paren_depth[j] > 0 for j in range(idx, min(pid_end, n))):
            pos = pid_end
            continue
        if (
            idx >= 1
            and text[idx - 1] == "["
            and pid_end + 2 <= n
            and text[pid_end : pid_end + 2] == "]("
        ):
            pos = idx + 1
            continue
        if (
            "." not in pid
            and pid_end < n
            and text[pid_end] == "."
            and pid_end + 1 < n
            and text[pid_end + 1].isdigit()
        ):
            pos = idx + 1
            continue
        url = ptdb_url_search(pid, index_rec)
        display_text = text[idx:pid_end]
        link = f"[{display_text}]({url})"
        return text[:idx] + link + text[pid_end:]
    return text


def link_ptdb_index_id_variants_in_text(
    text: str, pid: str, index_rec: dict | None = None
) -> str:
    pid = (pid or "").strip()
    t = text or ""
    if not pid or not t:
        return t
    cands: list[str] = []
    for s in (pid, ptdb_search_pid_for_url(pid, index_rec)):
        s = str(s).strip()
        if s and s.upper() not in {x.upper() for x in cands}:
            cands.append(s)
    if isinstance(index_rec, dict):
        for key in ("Protein_ID", "AthID", "GeneID", "query_id"):
            v = str(index_rec.get(key) or "").strip()
            if v and v.upper() not in {x.upper() for x in cands}:
                cands.append(v)
        bi = index_rec.get("base_info") or {}
        v = str(bi.get("pid") or "").strip()
        if v and v.upper() not in {x.upper() for x in cands}:
            cands.append(v)
    cands.sort(key=len, reverse=True)
    out = t
    for cand in cands:
        out = link_pid_in_text(out, cand, index_rec)
    return out


def link_symbol_or_pid_to_ptdb(
    text: str, symbol: str, pid: str, index_rec: dict | None = None
) -> str:
    sym = (symbol or "").strip()
    pid = (pid or "").strip()
    t = text or ""
    if not pid or not t:
        return t
    if sym.upper() in ("", "NONE") or sym == pid:
        return link_ptdb_index_id_variants_in_text(t, pid, index_rec)

    def _paren_depth(s: str) -> list[int]:
        nloc = len(s)
        arr = [0] * nloc
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                arr[i] = depth
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
                arr[i] = depth
            else:
                arr[i] = depth
        return arr

    def _pos_ok(s: str, start: int, end: int) -> bool:
        if end < len(s) and s[end] == "." and end + 1 < len(s) and s[end + 1].isdigit() and "." not in sym:
            return False
        if start >= 1 and s[start - 1] == "[" and end + 2 <= len(s) and s[end : end + 2] == "](":
            return False
        return True

    pat = re.compile(r"(?<!\w)" + re.escape(sym) + r"(?!\w)", re.IGNORECASE)
    out = t
    changed_any = False
    while True:
        pd = _paren_depth(out)
        nloc = len(out)
        replaced = False
        for m in pat.finditer(out):
            start, end = m.span()
            if not _pos_ok(out, start, end):
                continue
            if any(pd[j] > 0 for j in range(start, min(end, nloc))):
                continue
            matched = out[start:end]
            if _looks_like_ptd_id(matched):
                url = ptdb_url_search(pid, index_rec)
            else:
                url = ptdb_url_search_index(matched)
            link = f"[{matched}]({url})"
            out = out[:start] + link + out[end:]
            changed_any = True
            replaced = True
            break
        if not replaced:
            break

    if changed_any:
        return link_ptdb_index_id_variants_in_text(out, pid, index_rec)
    return link_ptdb_index_id_variants_in_text(t, pid, index_rec)
