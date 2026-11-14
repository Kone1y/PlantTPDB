#!/usr/bin/env python3
"""
Gene symbol normalization map and literature index builder (symbol_literature_mapper)

Post-processing of the screening results from auto_plant_transporter_pipeline.py:
  Stage A - deterministic matching (always runs, zero cost)
      Loads the curated symbol.list library and token-matches titles/abstracts/
      filter reasons, producing a "canonical symbol -> literature" index
      (symbol_literature_map.json / .tsv).
  Stage B - LLM agent extraction (--use-llm; API usage mirrors
      literature_filter_llm_api_v2.py)
      Calls an LLM API in batches to extract the gene symbols mentioned in each
      paper (with main-gene flags); extractions that hit the library are merged
      into the main map.
  ath normalization (on by default; disable with --no-ath-filter)
      Uses ath_symbol_tc_final_v2.tsv (Ath_id/Symbol/tc) as the sole canonical
      system: the library is first filtered down to entries mappable to ath
      symbols plus 223 family names; every hit is rewritten to its ath symbol
      (multi-label expansion, e.g. HAK5 -> HAK/KT/KUP); non-synonymous symbols
      outside the ath system are not recorded. A paper yielding no symbols at
      all is a normal, allowed outcome.

LLM config resolution order: CLI args > environment variables > pipeline.env >
built-in defaults:
  LLM_API_URL, LLM_API_KEY, LLM_MODEL, USE_LLM_API

Usage examples:
  # Aggregate all historical runs, deterministic matching only
  python3 symbol_literature_mapper.py

  # Process specific runs with LLM agent extraction (env may come from pipeline.env)
  python3 symbol_literature_mapper.py --runs run_20260909 --use-llm

  # Full-text papers mode (symbol_literature_map.tsv is produced by this mode)
  python3 symbol_literature_mapper.py --papers-json papers_metadata_md.json

  # Full set + include rejected papers + list articles with zero hits
  python3 symbol_literature_mapper.py --include-rejected --list-unmatched

  # Disable species-prefix matching (strict boundaries; OsHAK5 no longer maps to HAK5)
  python3 symbol_literature_mapper.py --strict

  # Disable ath normalization filtering (legacy behavior with the full symbol.list library)
  python3 symbol_literature_mapper.py --no-ath-filter
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Reuse ath table loading / mapping construction (standalone script in the same
# directory; no circular dependency)
from ath_symbol_normalizer import load_ath_table, load_araport, build_symbol_mapping

# tqdm is optional (same fallback as literature_filter_llm_api_v2.py)
try:
    from tqdm import tqdm
except ImportError:
    class tqdm:
        """Minimal fallback when tqdm is absent: print every 5% or at least 10 items"""
        def __init__(self, total, desc="", unit="it"):
            self.total = total
            self.desc = desc
            self.unit = unit
            self.n = 0
            self.last_print = 0

        def update(self, n=1):
            self.n += n
            if self.n - self.last_print >= max(1, self.total // 20) or self.n == self.total:
                pct = (self.n / self.total * 100) if self.total > 0 else 0
                print(f"  {self.desc}: {self.n}/{self.total} ({pct:.0f}%)")
                self.last_print = self.n

        def close(self):
            if self.n == self.total and self.n > 0:
                print(f"  {self.desc}: done")

SCRIPT_VERSION = "1.0"

# ============================ LLM configuration (aligned with literature_filter_llm_api_v2.py) ============================

DEFAULT_API_URL = "https://api.deepseek.com"  # DeepSeek API
DEFAULT_API_KEY = "sk-your-api-key-here"      # placeholder; configure via env vars or pipeline.env
DEFAULT_MODEL = "deepseek-chat"               # DeepSeek model

REQUEST_TIMEOUT = 60   # API request timeout (seconds); batch extraction output is long
MAX_RETRIES = 3        # maximum retry attempts
RETRY_DELAY = 2        # retry delay (seconds)
DEFAULT_LLM_BATCH_SIZE = 10   # papers processed per batch
DEFAULT_MAX_WORKERS = 5       # maximum concurrent worker threads

# ============================ Matching constants ============================

VALID_FIELDS = ('title', 'authors', 'journal', 'abstract', 'filter_reason', 'type', 'sections')
DEFAULT_FIELDS = ('title', 'abstract', 'filter_reason')
PAPERS_DEFAULT_FIELDS = ('title', 'abstract', 'sections')   # papers mode includes full text by default

# Candidate token charset (symbols may contain the separators . / ; -)
TOKEN_RE = re.compile(r'[A-Za-z0-9./;-]+')
SEGMENT_RE = re.compile(r'[./;-]')
# Strip HTML tags (<sub>, <i>, ...) so CO<sub>2</sub> stays a single CO2 token
HTML_TAG_RE = re.compile(r'</?[A-Za-z][^>]*>')
# Ion-notation guard: skip a token directly followed by + or a sub/superscript
# digit (optionally after a closing ) or ]; e.g. Ca2+, (K+, CO2, Ca2+).
# Whitespace is deliberately not allowed, so "HAK5 + NRT1" is unaffected.
ION_TAIL_RE = re.compile(r'[)\]]*[+⁺₀-₉⁰¹²³⁴-⁹]')
# Ionic element forms (CA2, AL1, ...) excluded from matching by default
ION_FORM_RE = re.compile(r'^(?:Ca|Al|Fe|Zn|Mn|Cu|Mg|Na|K|H|Cl|NO|NH|PO|SO|CO|Si|B|Mo|Ni)\d+$', re.I)
# Allowed symbol charset
SYMBOL_OK_RE = re.compile(r'^[A-Za-z0-9./;-]+$')
# Brassica locus prefixes (BnaC6. / BnaA03 / BraO ...)
BNA_RE = re.compile(r'^(?:Bna|Bra|Bo)[A-Z]?[0-9]*\.?')
# Common species-genus abbreviation prefixes (case-sensitive: At matches AtHKT1 but not ATP)
DEFAULT_SPECIES_PREFIXES = (
    'At', 'Os', 'Zm', 'Gm', 'Sl', 'Mt', 'Hv', 'Sb', 'Bd', 'Cs',
    'Vu', 'Nt', 'Pt', 'Vv', 'Ca', 'Lu', 'Si', 'Ta', 'Pp', 'Cr',
)

# The 9 columns of filtered.tsv (mirrors write_filtered_tsv in
# literature_filter_llm_api_v2.py; parsing is positional)
TSV_COLUMNS = ['PMID/ID', 'Title', 'Authors', 'Journal', 'Pub date', 'DOI', 'Abstract',
               'AI summary', 'Filter reason']

NEW_SYMBOLS_PREFIX = 'new_symbols_for_curation'
FAMILY_MAP_PREFIX = 'symbol_family_map'

# ============================ Family merging ============================

# Trailing numeric group (with optional ./;- separator): NPF2.13 -> NPF2 -> NPF; ACLA-1 -> ACLA
FAMILY_STRIP_RE = re.compile(r'[./;-]?\d+$')


def family_key(sym: str) -> str:
    """Family merge key: repeatedly strip trailing numeric groups; the result must
    still contain a letter and be >= 2 chars, otherwise fall back to the last valid form"""
    key = sym
    while True:
        stripped = FAMILY_STRIP_RE.sub('', key)
        if stripped == key or len(stripped) < 2 or not any(c.isalpha() for c in stripped):
            break
        key = stripped
    cleaned = key.rstrip('./;-')  # defensive cleanup of leftover separators
    if len(cleaned) >= 2 and any(c.isalpha() for c in cleaned):
        key = cleaned
    return key


# ============================ Environment configuration ============================

def load_pipeline_env(script_dir: Path) -> None:
    """If not already set in the environment, parse export KEY=VALUE lines from
    pipeline.env (script dir -> parent dir fallback)"""
    env_file = script_dir / 'pipeline.env'
    if not env_file.is_file():
        env_file = script_dir.parent / 'pipeline.env'  # script lives in scripts/symbol/, env in scripts/
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text(encoding='utf-8').splitlines()
    except OSError as e:
        print(f"[WARN] Cannot read {env_file}: {e}")
        return
    loaded = 0
    for line in lines:
        m = re.match(r'\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        print(f"[CONFIG] Loaded {loaded} environment variables from pipeline.env (unset keys only)")


def resolve_llm_config(args) -> Dict:
    """LLM config resolution: CLI args > environment variables > defaults"""
    return {
        'api_url': args.api_url or os.environ.get('LLM_API_URL', DEFAULT_API_URL),
        'api_key': args.api_key or os.environ.get('LLM_API_KEY', DEFAULT_API_KEY),
        'model': args.model or os.environ.get('LLM_MODEL', DEFAULT_MODEL),
    }


# ============================ Curated library ============================

class SymbolLibrary:
    """The symbol.list curated library: loading, tiered classification, normalized lookup.

    Tiering rules (to avoid false matches on common English words):
      SAFE   contains a digit / a separator (. / ; -) / an internal lower->upper
             boundary (e.g. LecRK) -> case-insensitive matching
      CAPS   letters only, all uppercase (e.g. GORK, SKOR) -> exact-case matching only
      WORDY  letters only, no digits, all lowercase or Titlecase (e.g. This,
             Arabidopsis) -> excluded by default
    Ionic element forms (CA2, AL1, ...) are excluded by default (Ca2+ notation is
    ubiquitous in abstracts). Case-duplicate pairs (Cand1/CAND1): on a hit, prefer
    the entry whose case exactly matches the text, otherwise the first in file order.
    """

    def __init__(self, include_wordy: bool = False, include_ion: bool = False,
                 full_case_insensitive: bool = False, exclude_symbols: Optional[List[str]] = None):
        self.include_wordy = include_wordy
        self.include_ion = include_ion
        self.full_ci = full_case_insensitive
        self.user_excludes = {s.strip() for s in (exclude_symbols or []) if s.strip()}

        self.entries: List[str] = []            # all entries in file order (deduplicated)
        self.tier: Dict[str, str] = {}          # symbol -> SAFE/CAPS/WORDY (matchable entries only)
        self.lower_map: Dict[str, List[str]] = {}  # lowercase -> library entries (file order)
        self.skipped_unmatchable: List[str] = []
        self.excluded_wordy: List[str] = []
        self.excluded_ion: List[str] = []
        self.excluded_by_user: List[str] = []
        self.excluded_non_ath: List[str] = []   # entries dropped by the ath filter (outside the transporter system)
        self.case_dup_groups: Dict[str, List[str]] = {}
        self.symbol_lists: List[str] = []
        # ath normalization context (filled by apply_ath_filter; stays empty in legacy mode)
        self.ath_tcs: Dict[str, List[str]] = {}      # ath Symbol -> [tc]
        self.ath_ids_map: Dict[str, List[str]] = {}  # ath Symbol -> [Ath_id]

    # ---------- loading ----------

    def load(self, paths: List[Path]) -> None:
        seen = set()
        for path in paths:
            self.symbol_lists.append(str(path.resolve()))
            with open(path, 'r', encoding='utf-8-sig') as f:
                for line in f:
                    sym = line.strip()
                    if not sym or sym in seen:
                        continue
                    seen.add(sym)
                    self.entries.append(sym)
        self._build_index()

    def _build_index(self) -> None:
        """Rebuild the tier index and exclusion stats from entries (shared by load and apply_ath_filter)"""
        self.tier = {}
        self.lower_map = {}
        self.skipped_unmatchable = []
        self.excluded_wordy = []
        self.excluded_ion = []
        self.excluded_by_user = []
        for sym in self.entries:
            if not SYMBOL_OK_RE.fullmatch(sym):
                self.skipped_unmatchable.append(sym)
                continue
            t = self._classify(sym)
            if t == 'WORDY' and not self.include_wordy:
                self.excluded_wordy.append(sym)
                continue
            if ION_FORM_RE.fullmatch(sym) and not self.include_ion:
                self.excluded_ion.append(sym)
                continue
            if sym in self.user_excludes:
                self.excluded_by_user.append(sym)
                continue
            self.tier[sym] = t
            self.lower_map.setdefault(sym.lower(), []).append(sym)

        # Case-duplicate report (over all matchable entries)
        self.case_dup_groups = {low: group for low, group in self.lower_map.items()
                                if len(group) > 1}

        missing = sorted(self.user_excludes - set(self.entries))
        if missing:
            print(f"[WARN] {len(missing)} symbols from --exclude-symbols are not in the library (ignored): "
                  f"{', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}")

    def apply_ath_filter(self, sym_to_ath: Dict[str, set], ath_symbols: set,
                         sym_to_tcs: Dict[str, set], sym_to_ids: Dict[str, set]) -> None:
        """ath normalization filter: keep only entries mappable to an ath symbol, and
        add every ath family name (not already present) as a matchable entry mapped to
        itself. Updates sym_to_ath in place.

        Dropped entries are recorded in excluded_non_ath (outside the transporter
        system; excluded from matching and output).
        """
        kept = []
        for sym in self.entries:
            if sym in sym_to_ath:
                kept.append(sym)
            else:
                self.excluded_non_ath.append(sym)
        # ath family names enter the library themselves so umbrella mentions like
        # "SWEET transporters" can hit directly
        existing = set(self.entries)
        added = [s for s in sorted(ath_symbols) if s not in existing]
        for s in added:
            sym_to_ath[s] = {s}
        self.entries = kept + added
        self._build_index()
        self.ath_tcs = {s: sorted(sym_to_tcs.get(s, ())) for s in ath_symbols}
        self.ath_ids_map = {s: sorted(sym_to_ids.get(s, ())) for s in ath_symbols}

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def matchable(self) -> int:
        return len(self.tier)

    @staticmethod
    def _classify(sym: str) -> str:
        if any(c.isdigit() for c in sym):
            return 'SAFE'
        if any(c in sym for c in './;-'):
            return 'SAFE'
        if re.search(r'[a-z][A-Z]', sym):  # internal lower->upper boundary, e.g. LecRK / AtRabE1b
            return 'SAFE'
        if sym.isupper():
            return 'CAPS'
        return 'WORDY'

    # ---------- lookup ----------

    def lookup(self, form: str) -> Optional[str]:
        """Return the canonical symbol for form (after canonical selection), or None on no hit"""
        candidates = self.lower_map.get(form.lower())
        if not candidates:
            return None
        # 1) exact same case first
        for sym in candidates:
            if sym == form:
                return sym
        # 2) SAFE tier matches case-insensitively; CAPS/WORDY need exact case
        #    (full_ci is the escape hatch)
        if self.full_ci:
            return candidates[0]
        for sym in candidates:
            if self.tier.get(sym) == 'SAFE':
                return sym
        return None


# ============================ Deterministic matcher ============================

def strip_species_prefix(form: str, prefixes: Tuple[str, ...]) -> Optional[str]:
    """Strip a species/locus prefix: BnaC6.HAK5 / BnaC6HAK5 -> HAK5; OsHAK5 -> HAK5.

    The split point is only a candidate generator: the remainder must be >= 3
    chars to look like a real symbol, and the library lookup is the final arbiter.
    """
    m = BNA_RE.match(form)
    if m and len(form) - m.end() >= 3:
        return form[m.end():]
    for p in prefixes:  # prefixes are sorted longest-first
        if form.startswith(p) and len(form) - len(p) >= 3:
            return form[len(p):]
    return None


class Matcher:
    """Deterministic matcher: tokenization + lookup against a prebuilt dictionary.

    For each token an ordered variant list is produced: (form, via)
      exact   the token itself
      sub     sub-segments split on ./;- (AKT5-like -> AKT5; AtHAK5. -> AtHAK5)
      prefix  form with the species prefix stripped (OsHAK5 -> HAK5); disabled by --strict
      plural  trailing plural s removed (AtAMTs -> AMT)
    Once a base form itself hits the library, its prefix/plural variants are
    skipped (sub segments are evaluated independently).
    """

    def __init__(self, library: SymbolLibrary, extra_prefixes: Optional[List[str]] = None,
                 use_prefix: bool = True, use_plural: bool = True):
        self.library = library
        self.use_prefix = use_prefix
        self.use_plural = use_plural
        merged = list(DEFAULT_SPECIES_PREFIXES) + [p for p in (extra_prefixes or []) if p]
        # Deduplicate and sort longest-first (a short prefix must not shadow a longer one)
        self.prefixes = tuple(sorted(set(merged), key=lambda p: (-len(p), p)))

    def _base_variants(self, base: str, base_via: str, out: List[Tuple[str, str]]) -> None:
        out.append((base, base_via))
        if self.library.lookup(base) is not None:
            return  # base already matched; skip its prefix/plural variants
        bases = [base]
        if self.use_prefix:
            stripped = strip_species_prefix(base, self.prefixes)
            if stripped and len(stripped) >= 2:
                bases.append(stripped)
        for b in bases:
            if b is not base:
                out.append((b, 'prefix'))
            if self.use_plural and len(b) >= 4 and b.endswith('s'):
                out.append((b[:-1], 'plural'))

    def _token_variants(self, tok: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        self._base_variants(tok, 'exact', out)
        for seg in SEGMENT_RE.split(tok):
            if seg and seg != tok:
                self._base_variants(seg, 'sub', out)
        seen, result = set(), []
        for form, via in out:
            if len(form) < 2 or (form, via) in seen:
                continue
            seen.add((form, via))
            result.append((form, via))
        return result

    def match_field(self, text: Optional[str]) -> Dict[str, Dict]:
        """Match within a single field; returns {canonical symbol: {forms, vias, count}}"""
        hits: Dict[str, Dict] = {}
        if not text:
            return hits
        clean = HTML_TAG_RE.sub('', str(text))
        for m in TOKEN_RE.finditer(clean):
            if ION_TAIL_RE.match(clean, m.end()):
                continue  # ion-notation guard: Ca2+, (K+
            for form, via in self._token_variants(m.group(0)):
                if via != 'exact' and ION_FORM_RE.fullmatch(form):
                    continue  # ionic forms are only allowed via the exact channel
                sym = self.library.lookup(form)
                if sym is None:
                    continue
                h = hits.setdefault(sym, {'forms': set(), 'vias': Counter(), 'count': 0})
                h['forms'].add(form)
                h['vias'][via] += 1
                h['count'] += 1
        return hits

    @staticmethod
    def _section_field_name(name: str) -> str:
        """Section name -> field name: ';' is replaced by ',' to avoid ambiguity with
        the '; ' joiner used in the TSV matched-fields column"""
        return 'sec:' + name.replace(';', ',')

    def match_article(self, article: Dict, fields: List[str]) -> Dict[str, Dict]:
        """Match the given fields of one article; returns
        {canonical symbol: {forms, vias, field_counts}}.
        The virtual 'sections' field: matched per section, recorded as sec:<section name>"""
        article_hits: Dict[str, Dict] = {}

        def merge(field: str, hits: Dict[str, Dict]) -> None:
            for sym, h in hits.items():
                a = article_hits.setdefault(sym, {'forms': set(), 'vias': Counter(),
                                                  'field_counts': Counter()})
                a['forms'].update(h['forms'])
                a['vias'].update(h['vias'])
                a['field_counts'][field] += h['count']

        for field in fields:
            if field == 'sections':
                sections = article.get('sections')
                if isinstance(sections, dict):
                    for name, text in sections.items():
                        merge(self._section_field_name(str(name)), self.match_field(text))
                continue
            merge(field, self.match_field(article.get(field)))
        return article_hits


# ============================ Run discovery / loading / dedup ============================

def discover_runs(results_dir: Path, runs_filter: Optional[List[str]]) -> List[Path]:
    """Discover runs by run_* directory names (directories only, which naturally
    excludes _run_* and root-level filtered_*.tsv)"""
    runs = sorted(p for p in results_dir.glob('run_*') if p.is_dir())
    if runs_filter:
        wanted = {r if r.startswith('run_') else f'run_{r}' for r in runs_filter}
        runs = [p for p in runs if p.name in wanted]
        missing = wanted - {p.name for p in runs}
        if missing:
            print(f"[WARN] Requested runs not found: {', '.join(sorted(missing))}")
    return runs


def load_tsv_articles(path: Path) -> List[Dict]:
    """Fallback parser for filtered.tsv (9 columns, per the write_filtered_tsv convention)"""
    articles = []
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader, None)
        if not header or header[0] != 'PMID/ID':
            print(f"[WARN] {path.name} unexpected header: {header[:3] if header else 'empty file'}")
        for cols in reader:
            if len(cols) < 9:
                cols = cols + [''] * (9 - len(cols))
            articles.append({
                'doi': cols[5] or cols[0],
                'title': cols[1],
                'authors': cols[2],
                'journal': cols[3],
                'pub_date': cols[4],
                'abstract': cols[6],
                'filter_reason': cols[8],
                '_source': '',
            })
    return articles


def load_run_articles(run_dir: Path, include_rejected: bool,
                      tsv_only: bool = False) -> Optional[Tuple[List[Dict], int, int]]:
    """Load the articles of one run. Returns (articles, n_loaded_files, n_missing_files)
    or None when every file is missing.

    tsv_only=True skips the JSON and reads only filtered.tsv (the scheduled job
    treats TSV as authoritative).
    """
    articles: List[Dict] = []
    specs = [('filtered.json', 'filtered.tsv', 'approved')]
    if include_rejected:
        specs.append(('filtered_rejected.json', 'filtered_rejected.tsv', 'rejected'))

    n_loaded = n_missing = 0
    for json_name, tsv_name, status in specs:
        json_path, tsv_path = run_dir / json_name, run_dir / tsv_name
        batch = None
        if json_path.is_file() and not tsv_only:
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    batch = json.load(f)
                n_loaded += 1
            except (json.JSONDecodeError, OSError) as e:
                print(f"[WARN] Failed to parse {json_path}: {e}")
        if batch is None and tsv_path.is_file():
            try:
                batch = load_tsv_articles(tsv_path)
                n_loaded += 1
            except OSError as e:
                print(f"[WARN] Failed to parse {tsv_path}: {e}")
        if batch is None:
            n_missing += 1
            continue
        for a in batch:
            a['_status'] = status
        articles.extend(batch)

    if n_loaded == 0:
        return None
    return articles, n_loaded, n_missing


def load_papers_articles(path: Path) -> List[Dict]:
    """Load papers_metadata_md.json (top-level list[dict]) and map it to the internal article schema"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot parse papers JSON: {e}")
        sys.exit(1)
    if not isinstance(data, list):
        print(f"Error: papers JSON top level must be a list, got {type(data).__name__}")
        sys.exit(1)

    articles: List[Dict] = []
    bad = 0
    n_missing_meta = 0
    for item in data:
        if not isinstance(item, dict):
            bad += 1
            continue
        if not str(item.get('title') or '').strip() or not str(item.get('abstract') or '').strip():
            n_missing_meta += 1
        articles.append({
            'doi': str(item.get('DOI') or '').strip(),
            'pmid': '',
            'id': str(item.get('filename') or ''),   # identity fallback when DOI is missing
            'title': str(item.get('title') or ''),
            'authors': str(item.get('author') or ''),
            'journal': '',
            'pub_date': '',
            'abstract': str(item.get('abstract') or ''),
            'filter_reason': '',
            'doc_type': str(item.get('doc_type') or ''),
            'filename': str(item.get('filename') or ''),
            'sections': item.get('sections') if isinstance(item.get('sections'), dict) else {},
            '_source': path.name,
            '_status': 'approved',
        })
    if bad:
        print(f"[WARN] {path.name}: skipped {bad} non-dict records")
    if n_missing_meta:
        print(f"[WARN] {path.name}: {n_missing_meta} records missing title/abstract (kept with empty values)")
    return articles


def normalize_doi(doi) -> str:
    if not doi:
        return ''
    d = str(doi).strip().lower()
    for pref in ('https://doi.org/', 'https://dx.doi.org/', 'http://dx.doi.org/',
                 'http://doi.org/', 'doi:', 'doi.org/'):
        if d.startswith(pref):
            d = d[len(pref):]
    return d


def article_identity(article: Dict) -> str:
    """Article unique identity: normalized DOI > PMID > id > title sha1 (defensive fallback)"""
    doi = normalize_doi(article.get('doi'))
    if doi:
        return f'doi:{doi}'
    pmid = str(article.get('pmid') or '').strip()
    if pmid:
        return f'pmid:{pmid}'
    aid = str(article.get('id') or '').strip()
    if aid:
        return f'id:{aid}'
    title = re.sub(r'[^a-z0-9]', '', str(article.get('title') or '').lower())
    return f'title:sha1:{hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]}'


def dedupe_articles(run_names: List[str], articles_by_run: Dict[str, List[Dict]]) -> Dict[str, Dict]:
    """Cross-run dedup merge: one record per identity, runs are unioned, approved wins"""
    records: Dict[str, Dict] = {}
    for run_name in run_names:  # already sorted for determinism
        for a in articles_by_run.get(run_name, []):
            identity = article_identity(a)
            rec = records.get(identity)
            if rec is None:
                records[identity] = {
                    'identity': identity, 'article': a, 'status': a.get('_status', 'approved'),
                    'runs': [run_name],
                }
            else:
                if run_name not in rec['runs']:
                    rec['runs'].append(run_name)
                if rec['status'] != 'approved' and a.get('_status') == 'approved':
                    rec['status'] = 'approved'
                    rec['article'] = a  # the approved version represents the record
    return records


# ============================ LLM agent extraction ============================

LLM_SYSTEM_PROMPT = """# Role
You are an expert in plant gene nomenclature. Your task is to precisely extract every gene symbol mentioned in the titles and abstracts of botanical papers.

## Extraction requirements
1. Extract all plant gene symbols mentioned in the text, always preserving the original written form, including:
   - forms carrying a species/locus prefix (e.g. OsHAK5, AtHKT1, BnaC6.HAK5, CsNRAMP5)
   - forms with isoform/allele numbers (e.g. PIP2;1, NRT1;1, akt5-1)
2. For each paper, flag its main study genes with is_main=true (there may be several); genes mentioned only in passing get is_main=false
3. Do NOT extract: species names (Arabidopsis, rice, ...), full English names or umbrella terms of protein classes (aquaporin, transporter, channel family), chemicals and ions (Ca2+, ABA, nitrate), phenotype or treatment descriptors
4. If a paper mentions no concrete gene symbol, return an empty symbols array for it

## Response format
Must be a valid JSON array with one element per paper, carrying the id in input order:
[{"id": 1, "symbols": [{"symbol": "AKT5", "is_main": true}, {"symbol": "AtPIP2;1", "is_main": false}]}]"""


class LLMSymbolExtractor:
    """LLM gene symbol extractor (OpenAI SDK; compatible with DeepSeek and others)"""

    def __init__(self, api_url: str, api_key: str, model: str):
        from openai import OpenAI  # lazy import; not required when the LLM is disabled
        self.client = OpenAI(api_key=api_key, base_url=api_url)
        self.model = model

    def _create(self, user_prompt: str, max_tokens: int) -> Optional[str]:
        messages = [
            {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ]
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages,
                    temperature=0.1, max_tokens=max_tokens, timeout=REQUEST_TIMEOUT,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                error_str = str(e).lower()
                if ('rate' in error_str or '429' in error_str) and attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAY * (attempt + 1)
                    print(f"  [WARN] API rate limited, waiting {wait}s before retry...")
                    time.sleep(wait)
                    continue
                if ('timeout' in error_str or 'timed out' in error_str) and attempt < MAX_RETRIES - 1:
                    print(f"  [WARN] API request timed out, retrying ({attempt + 1}/{MAX_RETRIES})...")
                    time.sleep(RETRY_DELAY)
                    continue
                if attempt < MAX_RETRIES - 1:
                    print(f"  [WARN] API request error: {e}; retrying ({attempt + 1}/{MAX_RETRIES})...")
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"  [ERROR] API request failed after {MAX_RETRIES} attempts: {e}")
                return None
        return None

    # ---------- prompt construction ----------

    @staticmethod
    def _batch_prompt(items: List[Tuple[str, Dict]]) -> str:
        prompt = f"Extract gene symbols from the titles and abstracts of the {len(items)} papers below.\n\n"
        prompt += ("Response format: a valid JSON array where each element is "
                   "{\"id\": <number>, \"symbols\": [{\"symbol\": \"...\", \"is_main\": true/false}]}; "
                   "the id matches the paper numbers below.\n")
        for i, (_, article) in enumerate(items, 1):
            title = article.get('title', '')
            abstract = (article.get('abstract') or '')[:1000]  # abstract truncated to bound length
            prompt += f"\n--- Paper {i} ---\nTitle: {title}\nAbstract: {abstract}\n"
        prompt += ("\nReturn the extraction results for all papers as a JSON array "
                   "(empty symbols array for papers that mention no genes).")
        return prompt

    # ---------- parsing ----------

    def _parse(self, content: str, items: List[Tuple[str, Dict]]) -> Optional[Dict[str, list]]:
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        parsed = None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r'\[.*\]', cleaned, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        if parsed is None:
            return None
        if isinstance(parsed, dict):
            parsed = parsed.get('results') or parsed.get('data') or [parsed]
        if not isinstance(parsed, list):
            return None

        by_id: Dict[int, list] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = item.get('id')
            syms = item.get('symbols', [])
            if isinstance(syms, list):
                if isinstance(idx, int):
                    by_id[idx] = syms
                elif isinstance(idx, str) and idx.isdigit():
                    by_id[int(idx)] = syms

        result: Dict[str, list] = {}
        for pos, (identity, _) in enumerate(items, 1):
            syms = by_id.get(pos)
            if syms is None and pos - 1 < len(parsed) and isinstance(parsed[pos - 1], dict):
                syms = parsed[pos - 1].get('symbols', [])  # positional fallback when id is absent
            result[identity] = syms if isinstance(syms, list) else []
        return result

    # ---------- public API ----------

    def extract_batch(self, items: List[Tuple[str, Dict]]) -> Optional[Dict[str, list]]:
        """Batch extraction; returns None on overall failure (the caller decides
        whether to retry article by article)"""
        if not items:
            return {}
        content = self._create(self._batch_prompt(items), max_tokens=2500)
        if content is None:
            return None
        result = self._parse(content, items)
        if result is None:
            print(f"  [WARN] Failed to parse the batch response ({len(items)} papers)")
        return result

    def extract_single(self, identity: str, article: Dict) -> Optional[list]:
        """Single-article extraction (fallback path after a batch failure)"""
        content = self._create(self._batch_prompt([(identity, article)]), max_tokens=600)
        if content is None:
            return None
        result = self._parse(content, [(identity, article)])
        return None if result is None else result.get(identity, [])


class LLMCache:
    """Per-DOI LLM extraction cache; a model change invalidates the whole cache"""

    def __init__(self, path: Path, model: str):
        self.path = path
        self.model = model
        self.entries: Dict[str, list] = {}
        if path.is_file():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('model') == model and isinstance(data.get('entries'), dict):
                    self.entries = data['entries']
            except (json.JSONDecodeError, OSError) as e:
                print(f"[WARN] Cache file corrupted, ignoring it: {e}")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'model': self.model, 'entries': self.entries}, f, ensure_ascii=False)
        tmp.replace(self.path)


def run_llm_extraction(extractor: LLMSymbolExtractor, records: Dict[str, Dict],
                       cache: LLMCache, refresh: bool,
                       batch_size: int, max_workers: int) -> Tuple[Dict[str, list], Dict]:
    """Concurrent batch extraction with caching and per-article fallback.
    Returns (identity -> symbol list, stats)"""
    items_all = [(identity, rec['article']) for identity, rec in records.items()]
    results: Dict[str, list] = {}
    cache_hits = 0
    todo: List[Tuple[str, Dict]] = []
    if not refresh:
        for identity, article in items_all:
            if identity in cache.entries:
                results[identity] = cache.entries[identity]
                cache_hits += 1
            else:
                todo.append((identity, article))
    else:
        todo = items_all

    stats = {'cache_hits': cache_hits, 'api_calls': 0, 'failed': 0, 'batches': 0}
    if not todo:
        return results, stats

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    stats['batches'] = len(batches)
    lock = threading.Lock()
    pbar = tqdm(total=len(todo), desc='LLM extraction', unit='paper')

    def handle(batch: List[Tuple[str, Dict]]) -> Tuple[Dict[str, list], int]:
        resolved: Dict[str, list] = {}
        failed = 0
        batch_result = extractor.extract_batch(batch)
        if batch_result is not None:
            resolved = batch_result
        else:  # batch failed -> per-article fallback
            for identity, article in batch:
                single = extractor.extract_single(identity, article)
                if single is None:
                    failed += 1
                else:
                    resolved[identity] = single
        return resolved, failed

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(handle, b) for b in batches]
        for future in as_completed(futures):
            try:
                resolved, failed = future.result()
            except Exception as e:  # one crashed future must not kill the whole run
                print(f"  [ERROR] LLM batch exception: {e}")
                continue
            with lock:
                stats['api_calls'] += 1
                stats['failed'] += failed
                for identity, syms in resolved.items():
                    results[identity] = syms
                    cache.entries[identity] = syms
                if resolved or failed:
                    cache.save()  # incremental save; completed calls survive a crash
            pbar.update(len(resolved) + failed)
    pbar.close()
    return results, stats


def clean_llm_symbols(raw_symbols: list, library: SymbolLibrary) -> List[Tuple[str, bool]]:
    """Clean LLM extractions: charset/length validation, ambiguous English-word filtering,
    dedup by lowercase (is_main is OR-ed)"""
    wordy_lower = {s.lower() for s in library.excluded_wordy}
    cleaned: Dict[str, Tuple[str, bool]] = {}
    for item in raw_symbols:
        if isinstance(item, str):
            raw, is_main = item, False
        elif isinstance(item, dict):
            raw, is_main = item.get('symbol'), bool(item.get('is_main', False))
        else:
            continue
        raw = str(raw or '').strip()
        if len(raw) < 2 or not SYMBOL_OK_RE.fullmatch(raw):
            continue
        if raw.lower() in wordy_lower:
            continue
        key = raw.lower()
        if key in cleaned:
            prev_form, prev_main = cleaned[key]
            cleaned[key] = (prev_form, prev_main or is_main)
        else:
            cleaned[key] = (raw, is_main)
    return list(cleaned.values())


def normalize_new_symbol(raw: str, prefixes: Tuple[str, ...]) -> str:
    """Normalized form of a newly discovered symbol: strip the species prefix and the
    trailing plural s (same rules as deterministic matching)"""
    form = raw
    stripped = strip_species_prefix(form, prefixes)
    if stripped and len(stripped) >= 2:
        form = stripped
    if len(form) >= 4 and form.endswith('s'):
        form = form[:-1]
    return form


def convert_hits_to_ath(article_hits: Dict[str, Dict[str, Dict]],
                        sym_to_ath: Dict[str, set]) -> Dict[str, Dict[str, Dict]]:
    """Rewrite hit keys from library symbols to ath symbols (multi-label expansion,
    counts merged).

    A hit on library HAK5 becomes the three entries HAK / KT / KUP (ath v2 keeps
    synonyms separate); forms/field_counts/vias are merged into each ath entry;
    matched_forms keeps the original text forms.
    """
    for identity, hits in article_hits.items():
        converted: Dict[str, Dict] = {}
        for sym, h in hits.items():
            targets = sym_to_ath.get(sym) or {sym}
            for target in sorted(targets):
                c = converted.setdefault(target, {'forms': set(), 'vias': Counter(),
                                                  'field_counts': Counter(), 'is_main': False,
                                                  'members': set()})
                c['forms'].update(h['forms'])
                c['vias'].update(h['vias'])
                c['members'].add(sym)
                for field, n in h['field_counts'].items():
                    c['field_counts'][field] += n
                c['is_main'] = c['is_main'] or h.get('is_main', False)
        article_hits[identity] = converted
    return article_hits


def merge_llm_results(records: Dict[str, Dict], article_hits: Dict[str, Dict[str, Dict]],
                      llm_results: Dict[str, list], matcher: Matcher, library: SymbolLibrary,
                      record_new: bool = True, dropped: Optional[Dict[str, int]] = None
                      ) -> Dict[str, Dict]:
    """Merge LLM extractions into the canonical map; misses go to new-symbol aggregation.

    record_new=False (ath mode): extractions missing the library are not recorded
    (non-synonymous symbols are dropped); only dropped['llm_new_dropped'] is counted.
    Returns: {norm_key: {'suggested', 'forms': Counter, 'articles': {identity: is_main}}}
    """
    new_symbols: Dict[str, Dict] = {}

    def add_new(raw: str, norm_key: str, identity: str, is_main: bool) -> None:
        entry = new_symbols.setdefault(norm_key, {'forms': Counter(), 'articles': {}})
        entry['forms'][raw] += 1
        entry['articles'][identity] = entry['articles'].get(identity, False) or is_main

    for identity, raw_symbols in llm_results.items():
        rec = records.get(identity)
        if rec is None:
            continue
        hits = article_hits.setdefault(identity, {})
        for raw, is_main in clean_llm_symbols(raw_symbols, library):
            # try the raw form first, then the prefix-stripped form (same rules as
            # deterministic matching)
            sym = library.lookup(raw)
            if sym is None and matcher.use_prefix:
                stripped = strip_species_prefix(raw, matcher.prefixes)
                if stripped and len(stripped) >= 2:
                    sym = library.lookup(stripped)
            if sym is not None:
                a = hits.setdefault(sym, {'forms': set(), 'vias': Counter(),
                                          'field_counts': Counter(), 'is_main': False})
                a['forms'].add(raw)
                a['vias']['llm'] += 1
                a['field_counts']['llm'] += 1
                a['is_main'] = a.get('is_main', False) or is_main
            elif record_new:
                norm_key = normalize_new_symbol(raw, matcher.prefixes if matcher.use_prefix else ()).lower()
                add_new(raw, norm_key, identity, is_main)
            elif dropped is not None:
                dropped['llm_new_dropped'] = dropped.get('llm_new_dropped', 0) + 1
    return new_symbols


# ============================ Output ============================

def escape(value) -> str:
    """TSV escaping (write_filtered_tsv convention: tabs/newlines become spaces)"""
    if value is None:
        return ""
    return str(value).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


def build_output(records: Dict[str, Dict], article_hits: Dict[str, Dict[str, Dict]],
                 new_symbols: Dict[str, Dict], run_names: List[str], library: SymbolLibrary,
                 args, llm_stats: Optional[Dict], fields: Optional[List[str]] = None,
                 papers_json_path: Optional[str] = None, ath_mode: bool = False) -> Dict:
    """Build the main output JSON structure"""
    symbols_index: Dict[str, Dict] = {}
    articles_index: Dict[str, Dict] = {}

    for identity in sorted(records):
        rec = records[identity]
        a = rec['article']
        hits = article_hits.get(identity, {})
        articles_index[identity] = {
            'doi': a.get('doi', ''),
            'pmid': str(a.get('pmid') or ''),
            'title': a.get('title', ''),
            'journal': a.get('journal', ''),
            'pub_date': a.get('pub_date', ''),
            'filename': a.get('filename', ''),
            'doc_type': a.get('doc_type', ''),
            '_source': a.get('_source', ''),
            'status': rec['status'],
            'runs': sorted(rec['runs']),
            'matched_symbols': sorted(hits),
        }
        for sym, h in hits.items():
            entry = symbols_index.setdefault(sym, {'articles': []})
            entry['articles'].append({
                'doi': a.get('doi', ''),
                'pmid': str(a.get('pmid') or ''),
                'title': a.get('title', ''),
                'journal': a.get('journal', ''),
                'pub_date': a.get('pub_date', ''),
                'filename': a.get('filename', ''),
                '_source': a.get('_source', ''),
                'status': rec['status'],
                'matched_forms': sorted(h['forms']),
                'matched_fields': sorted(h['field_counts']),
                'match_counts': dict(sorted(h['field_counts'].items())),
                'match_via': dict(sorted(h['vias'].items())),
                'is_main': h.get('is_main', False),
                'matched_members': sorted(h.get('members', ())),
                'runs': sorted(rec['runs']),
                '_identity': identity,
            })

    # symbol summary + ordering (by symbol name for determinism)
    ordered_symbols: Dict[str, Dict] = {}
    for sym in sorted(symbols_index):
        arts = symbols_index[sym]['articles']
        # display order: publication date descending, then DOI
        arts.sort(key=lambda x: (x['pub_date'] or '', x['doi'] or ''), reverse=True)
        vias = Counter()
        for art in arts:
            vias.update(art['match_via'])
        ordered_symbols[sym] = {
            'canonical_symbol': sym,
            # in ath mode the symbol already is family-level (ath symbols are not
            # truncated; EDS5/ERD6 keep their form)
            'family': sym if ath_mode else family_key(sym),
            'tc': library.ath_tcs.get(sym, []),
            'ath_ids': library.ath_ids_map.get(sym, []),
            'member_symbols': sorted({m for a in arts for m in a.get('matched_members', ())}),
            'library_case_variants': library.lower_map.get(sym.lower(), [sym]),
            'article_count': len(arts),
            'match_via_total': dict(sorted(vias.items())),
            'articles': arts,
        }

    families = build_families_index(ordered_symbols)

    articles_with = sum(1 for identity in records if article_hits.get(identity))
    pair_count = sum(len(v['articles']) for v in symbols_index.values())
    stats = {
        'article_rows_scanned': None,  # filled by main (raw row count across runs)
        'unique_articles': len(records),
        'articles_with_symbols': articles_with,
        'articles_without_symbols': len(records) - articles_with,
        'symbols_matched': len(ordered_symbols),
        'symbol_article_pairs': pair_count,
        'families': len(families),
    }
    if llm_stats is not None:
        stats['llm'] = llm_stats
    if new_symbols:
        stats['new_symbols'] = {
            'count': len(new_symbols),
            'articles_covered': len({i for v in new_symbols.values() for i in v['articles']}),
        }

    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'script_version': SCRIPT_VERSION,
        'symbol_lists': library.symbol_lists,
        'results_dir': str(Path(args.results_dir).resolve()),
        'papers_json': papers_json_path,
        'runs_processed': list(run_names),
        'options': {
            'include_rejected': args.include_rejected,
            'fields': list(fields) if fields is not None else list(args.fields),
            'prefix_matching': not args.strict,
            'prefixes': None,  # filled by main (species prefixes actually used)
            'use_plural': not args.no_plural,
            'include_wordy': args.include_wordy,
            'include_ion_symbols': args.include_ion_symbols,
            'full_case_insensitive': args.full_case_insensitive,
            'exclude_symbols': sorted(library.user_excludes),
            'use_llm': args.use_llm,
        },
        'library': {
            'total_symbols': library.total,
            'matchable_symbols': library.matchable,
            'skipped_unmatchable': library.skipped_unmatchable,
            'excluded_wordy': library.excluded_wordy,
            'excluded_ion_forms': library.excluded_ion,
            'excluded_by_user': library.excluded_by_user,
            'excluded_non_ath_count': len(library.excluded_non_ath),
            'excluded_non_ath_sample': sorted(library.excluded_non_ath)[:20],
            'case_duplicate_groups': library.case_dup_groups,
        },
        'stats': stats,
        'symbols': ordered_symbols,
        'families': families,
        'articles': articles_index,
    }
    return payload


def write_map_tsv(payload: Dict, path: Path) -> int:
    """Main map TSV: one row per (canonical symbol x article); in ath mode the
    canonical symbol is the ath symbol"""
    ath_mode = payload.get('options', {}).get('ath_filter', False)
    if ath_mode:
        headers = ['ath_symbol', 'tc', 'member_symbols', 'matched_forms', 'DOI', 'PMID',
                   'title', 'journal', 'pub_date', 'matched_fields', 'source_runs',
                   'status', 'source_file']
    else:
        headers = ['symbol', 'matched_forms', 'DOI', 'PMID', 'title', 'journal', 'pub_date',
                   'matched_fields', 'source_runs', 'status', 'source_file']
    rows = 0
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')
        for sym in sorted(payload['symbols']):
            info = payload['symbols'][sym]
            for art in info['articles']:
                if ath_mode:
                    row = [
                        escape(sym),
                        escape('; '.join(info.get('tc', ()))),
                        escape('; '.join(art.get('matched_members', ()))),
                        escape('; '.join(art['matched_forms'])),
                        escape(art['doi']),
                        escape(art['pmid']),
                        escape(art['title']),
                        escape(art['journal']),
                        escape(art['pub_date']),
                        escape('; '.join(art['matched_fields'])),
                        escape('; '.join(art['runs'])),
                        escape(art['status']),
                        escape(art.get('filename', '')),
                    ]
                else:
                    row = [
                        escape(sym),
                        escape('; '.join(art['matched_forms'])),
                        escape(art['doi']),
                        escape(art['pmid']),
                        escape(art['title']),
                        escape(art['journal']),
                        escape(art['pub_date']),
                        escape('; '.join(art['matched_fields'])),
                        escape('; '.join(art['runs'])),
                        escape(art['status']),
                        escape(art.get('filename', '')),
                    ]
                f.write('\t'.join(row) + '\n')
                rows += 1
    return rows


def build_families_index(ordered_symbols: Dict[str, Dict]) -> Dict[str, Dict]:
    """Derive a family index from the member-symbol index: grouped by family_key,
    articles unioned, with per-article member hits recorded"""
    grouped: Dict[str, Dict] = {}
    for sym, info in ordered_symbols.items():
        fam = info['family']
        entry = grouped.setdefault(fam, {'member_symbols': [], 'articles': {}})
        entry['member_symbols'].append(sym)
        for art in info['articles']:
            a = entry['articles'].setdefault(art['_identity'], {'article': art,
                                                                 'member_symbols': set()})
            a['member_symbols'].add(sym)

    families: Dict[str, Dict] = {}
    for fam in sorted(grouped):
        entry = grouped[fam]
        articles = []
        for identity, v in entry['articles'].items():
            art = v['article']
            articles.append({
                'member_symbols': sorted(v['member_symbols']),
                'doi': art.get('doi', ''),
                'pmid': art.get('pmid', ''),
                'title': art.get('title', ''),
                'journal': art.get('journal', ''),
                'pub_date': art.get('pub_date', ''),
                'filename': art.get('filename', ''),
                'status': art.get('status', ''),
                'runs': art.get('runs', []),
                '_identity': identity,
            })
        articles.sort(key=lambda x: (x['pub_date'] or '', x['doi'] or ''), reverse=True)
        families[fam] = {
            'family': fam,
            'member_symbols': sorted(entry['member_symbols']),
            'article_count': len(articles),
            'articles': articles,
        }
    return families


def write_family_tsv(families: Dict[str, Dict], path: Path) -> int:
    """Family map TSV: one row per (family x article)"""
    headers = ['family', 'member_symbols', 'DOI', 'PMID', 'title', 'journal', 'pub_date',
               'source_file', 'source_runs', 'status']
    rows = 0
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')
        for fam in sorted(families):
            for art in families[fam]['articles']:
                row = [
                    escape(fam),
                    escape('; '.join(art['member_symbols'])),
                    escape(art['doi']),
                    escape(art['pmid']),
                    escape(art['title']),
                    escape(art['journal']),
                    escape(art['pub_date']),
                    escape(art['filename']),
                    escape('; '.join(art['runs'])),
                    escape(art['status']),
                ]
                f.write('\t'.join(row) + '\n')
                rows += 1
    return rows


def build_new_symbols_output(new_symbols: Dict[str, Dict], records: Dict[str, Dict],
                             prefixes: Tuple[str, ...]) -> Tuple[Dict, List[Dict]]:
    """New symbols: aggregated, article count descending; the suggested name is the
    prefix-stripped most common raw form"""
    entries = []
    for norm_key, entry in new_symbols.items():
        most_common_form, _count = entry['forms'].most_common(1)[0]
        suggested = normalize_new_symbol(most_common_form, prefixes)
        articles = []
        for identity, is_main in entry['articles'].items():
            rec = records.get(identity)
            if rec is None:
                continue
            a = rec['article']
            articles.append({
                'doi': a.get('doi', ''),
                'pmid': str(a.get('pmid') or ''),
                'title': a.get('title', ''),
                'journal': a.get('journal', ''),
                'pub_date': a.get('pub_date', ''),
                'filename': a.get('filename', ''),
                'runs': sorted(rec['runs']),
                'is_main': is_main,
            })
        articles.sort(key=lambda x: (x['pub_date'] or '', x['doi'] or ''), reverse=True)
        entries.append({
            'suggested_symbol': suggested,
            'family': family_key(suggested),
            'original_forms': sorted(entry['forms'], key=lambda f: (-entry['forms'][f], f)),
            'article_count': len(articles),
            'ever_main_gene': any(x['is_main'] for x in articles),
            'articles': articles,
        })
    entries.sort(key=lambda e: (-e['article_count'], e['suggested_symbol'].lower()))
    payload = {
        'purpose': 'Gene symbols found in the literature but absent from the curated library; '
                   'review manually and update symbol.list',
        'total_new_symbols': len(entries),
        'total_article_pairs': sum(e['article_count'] for e in entries),
        'main_gene_symbols': sum(1 for e in entries if e['ever_main_gene']),
        'new_symbols': entries,
    }
    return payload, entries


def write_new_symbols_tsv(entries: List[Dict], path: Path) -> int:
    headers = ['suggested_symbol', 'original_forms', 'DOI', 'PMID', 'title', 'journal',
               'pub_date', 'is_main', 'family', 'source_runs', 'source_file']
    rows = 0
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')
        for entry in entries:
            for art in entry['articles']:
                row = [
                    escape(entry['suggested_symbol']),
                    escape('; '.join(entry['original_forms'])),
                    escape(art['doi']),
                    escape(art['pmid']),
                    escape(art['title']),
                    escape(art['journal']),
                    escape(art['pub_date']),
                    escape('yes' if art['is_main'] else 'no'),
                    escape(entry.get('family', '')),
                    escape('; '.join(art['runs'])),
                    escape(art.get('filename', '')),
                ]
                f.write('\t'.join(row) + '\n')
                rows += 1
    return rows


# ============================ Summary output ============================

def print_summary(payload: Dict, args, unmatched: List[Dict]) -> None:
    lib = payload['library']
    stats = payload['stats']
    print("\n" + "=" * 60)
    print("Symbol normalization map summary")
    print("=" * 60)
    print(f"Runs processed: {len(payload['runs_processed'])}")
    print(f"Article rows: {stats['article_rows_scanned']} / unique articles: {stats['unique_articles']}")
    if payload.get('options', {}).get('ath_filter'):
        print(f"ath_symbols matched: {stats['symbols_matched']}"
              " (ath normalization enabled; all output symbols follow the ath_symbol_tc_final_v2 system)")
    else:
        print(f"Canonical symbols matched: {stats['symbols_matched']}")
    print(f"Families matched: {stats.get('families', 0)}")
    print(f"Articles with at least one symbol: {stats['articles_with_symbols']} / {stats['unique_articles']}"
          + (" (a paper yielding no symbols is a normal outcome)" if payload.get('options', {}).get('ath_filter') else ""))
    print(f"Symbol x article pairs: {stats['symbol_article_pairs']}")

    top = sorted(payload['symbols'].items(), key=lambda kv: (-kv[1]['article_count'], kv[0]))[:args.top_n]
    if top:
        print(f"\nTop {len(top)} symbols (by article count):")
        for sym, info in top:
            tc = f" tc={'; '.join(info['tc'])}" if info.get('tc') else ""
            print(f"  {sym:<20} {info['article_count']} papers{tc}")

    if unmatched:
        print(f"\nArticles matching no symbol: {len(unmatched)}"
              + (" (use --list-unmatched to see them)" if not args.list_unmatched else ":"))
        if args.list_unmatched:
            for a in unmatched[:50]:
                print(f"  {a['doi'] or a['pmid'] or a['title'][:40]}")

    warn_parts = []
    if lib['skipped_unmatchable']:
        warn_parts.append(f"{len(lib['skipped_unmatchable'])} unmatchable")
    if lib['excluded_wordy']:
        warn_parts.append(f"{len(lib['excluded_wordy'])} English-word exclusions")
    if lib['excluded_ion_forms']:
        warn_parts.append(f"{len(lib['excluded_ion_forms'])} ion-form exclusions")
    if lib['excluded_by_user']:
        warn_parts.append(f"{len(lib['excluded_by_user'])} user exclusions")
    if lib.get('excluded_non_ath_count'):
        warn_parts.append(f"{lib['excluded_non_ath_count']} dropped as non-ath")
    if lib['case_duplicate_groups']:
        warn_parts.append(f"{len(lib['case_duplicate_groups'])} case-duplicate groups")
    if warn_parts:
        print(f"Library notes: {' | '.join(warn_parts)}")

    if stats.get('llm'):
        ls = stats['llm']
        print("\n" + "-" * 60)
        print(f"LLM agent extraction: model={ls['model']} | batches={ls['batches']} | "
              f"cache hits={ls['cache_hits']} | failed={ls['failed']}")
        if ls.get('llm_new_dropped'):
            print(f"LLM-extracted symbols outside ath synonyms dropped (ath mode): {ls['llm_new_dropped']}")
        if stats.get('new_symbols'):
            ns = stats['new_symbols']
            print(f"New symbols: {ns['count']} (covering {ns['articles_covered']} papers), "
                  f"written to {NEW_SYMBOLS_PREFIX}.tsv for manual review and symbol.list updates")


# ============================ Main flow ============================

def default_symbol_list(script_dir: Path) -> str:
    """Default symbol.list path: script dir -> parent dir fallback (the script lives
    in scripts/symbol/, the library in scripts/)"""
    for cand in (script_dir / 'symbol.list', script_dir.parent / 'symbol.list'):
        if cand.is_file():
            return str(cand)
    return str(script_dir / 'symbol.list')


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description='Gene symbol normalization map and literature index builder '
                    '(post-processing script for auto_plant_transporter_pipeline)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""Examples:
  {Path(sys.argv[0]).name}                                    # aggregate all runs, deterministic matching only
  {Path(sys.argv[0]).name} --runs run_20260909                # process only the given runs
  {Path(sys.argv[0]).name} --runs run_20260909 --use-llm      # enable LLM agent extraction
  {Path(sys.argv[0]).name} --use-llm --include-rejected       # full set + rejected papers
  {Path(sys.argv[0]).name} --papers-json papers_metadata_md.json  # build the index from a papers JSON
  {Path(sys.argv[0]).name} --use-llm --output-dir symbol_map_results  # write all results to a new folder
  {Path(sys.argv[0]).name} --strict                           # strict boundaries (no species-prefix stripping)
""")
    parser.add_argument('--results-dir', default='./results',
                        help='base directory holding the run directories (default ./results); '
                             'the LLM cache is also written here')
    parser.add_argument('--output-dir', default=None,
                        help='output directory (default: same as --results-dir; when set, '
                             'symbol_literature_map.* and new_symbols_for_curation.* are all '
                             'written there and it is created automatically)')
    parser.add_argument('--symbol-list', default=default_symbol_list(script_dir),
                        help='path to the curated symbol library (default symbol.list next to '
                             'the script, with a parent-dir fallback)')
    parser.add_argument('--ath-table', default=str(script_dir / 'ath_symbol_tc_final_v2.tsv'),
                        help='ath normalization table (Ath_id/Symbol/tc); when the file exists '
                             'the ath filter is enabled: hits are rewritten to ath symbols and '
                             'symbols outside the ath system are not recorded')
    parser.add_argument('--araport', default=str(script_dir / 'Araport11_mRNA_info.tsv'),
                        help='Araport11 annotation table (symbol -> Ath_id bridge, used by the ath filter)')
    parser.add_argument('--no-ath-filter', action='store_true',
                        help='disable ath normalization filtering (legacy behavior with the full symbol.list library)')
    parser.add_argument('--extra-symbols', nargs='+', metavar='FILE',
                        help='additional symbol library files (multiple allowed, merged into the curated library)')
    parser.add_argument('--runs', nargs='+', metavar='RUN',
                        help='process only the given runs (accepts run_20260909 or 20260909)')
    parser.add_argument('--papers-json', default=None, metavar='FILE',
                        help='load papers from papers_metadata_md.json (skips run discovery; '
                             'fields map to DOI/author/filename, sections are matched section by section)')
    parser.add_argument('--include-rejected', action='store_true',
                        help='also process filtered_rejected.* (status column marked rejected)')
    parser.add_argument('--tsv-only', action='store_true',
                        help='read only filtered.tsv (skip filtered.json; used by the daily scheduled job)')
    parser.add_argument('--fields', nargs='+', default=None,
                        choices=VALID_FIELDS, metavar='FIELD',
                        help='fields for deterministic matching (run mode defaults to '
                             'title abstract filter_reason; papers mode to title abstract sections)')
    parser.add_argument('--strict', action='store_true',
                        help='disable species-prefix stripping (OsHAK5 no longer maps to HAK5)')
    parser.add_argument('--extra-prefixes', nargs='+', metavar='PREFIX',
                        help='extra species-genus abbreviation prefixes (case-sensitive)')
    parser.add_argument('--exclude-symbols', nargs='+', metavar='SYM',
                        help='exclude the given symbols from matching (e.g. ambiguous short symbols like ER, TIP)')
    parser.add_argument('--include-wordy', action='store_true',
                        help='enable English-word-like symbols excluded by default (exact case only)')
    parser.add_argument('--include-ion-symbols', action='store_true',
                        help='enable ionic element-form symbols (CA2, AL1, ...)')
    parser.add_argument('--full-case-insensitive', action='store_true',
                        help='case-insensitive matching over the whole library (noisy; escape hatch)')
    parser.add_argument('--no-plural', action='store_true',
                        help='disable the trailing plural-s variant (AtAMTs -> AMT)')
    parser.add_argument('--use-llm', action='store_true',
                        help='enable LLM agent extraction (also controlled by the USE_LLM_API env var)')
    parser.add_argument('--api-url', help='LLM API URL (overrides LLM_API_URL)')
    parser.add_argument('--api-key', help='LLM API Key (overrides LLM_API_KEY)')
    parser.add_argument('--model', help='LLM model name (overrides LLM_MODEL)')
    parser.add_argument('--llm-batch-size', type=int, default=DEFAULT_LLM_BATCH_SIZE,
                        help=f'papers per LLM batch (default {DEFAULT_LLM_BATCH_SIZE})')
    parser.add_argument('--max-workers', type=int, default=DEFAULT_MAX_WORKERS,
                        help=f'LLM worker threads (default {DEFAULT_MAX_WORKERS})')
    parser.add_argument('--refresh-llm', action='store_true',
                        help='ignore the cache and force fresh LLM calls')
    parser.add_argument('--output-prefix', default='symbol_literature_map',
                        help='main output file prefix (default symbol_literature_map)')
    parser.add_argument('--top-n', type=int, default=20,
                        help='show Top-N symbols in the summary (default 20)')
    parser.add_argument('--list-unmatched', action='store_true',
                        help='list the articles that matched no symbol')
    return parser.parse_args()


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    load_pipeline_env(script_dir)

    results_dir = Path(args.results_dir)
    papers_path = Path(args.papers_json).resolve() if args.papers_json else None

    # ---------- load the curated library ----------
    symbol_list_paths = [Path(args.symbol_list)] + [Path(p) for p in (args.extra_symbols or [])]
    for p in symbol_list_paths:
        if not p.is_file():
            print(f"Error: symbol library file not found: {p}")
            sys.exit(1)
    library = SymbolLibrary(include_wordy=args.include_wordy,
                            include_ion=args.include_ion_symbols,
                            full_case_insensitive=args.full_case_insensitive,
                            exclude_symbols=args.exclude_symbols)
    library.load(symbol_list_paths)
    print(f"[LIB] Loaded {library.total} symbols (matchable {library.matchable}; "
          f"unmatchable {len(library.skipped_unmatchable)}, English-word exclusions {len(library.excluded_wordy)}, "
          f"ion-form exclusions {len(library.excluded_ion)})")

    # ---------- ath normalization (on by default; disable with --no-ath-filter) ----------
    ath_enabled = False
    sym_to_ath: Dict[str, set] = {}
    if args.no_ath_filter:
        print("[ath] Normalization disabled via --no-ath-filter; matching against the full symbol.list library")
    else:
        ath_path = Path(args.ath_table)
        if not ath_path.is_file():
            print(f"[WARN] ath table not found ({ath_path}); falling back to the full symbol.list library")
        else:
            araport_path = Path(args.araport)
            if araport_path.is_file():
                araport_map = load_araport(araport_path)
            else:
                print(f"[WARN] Araport11 annotation missing ({araport_path}); bridging degrades to direct name matches only")
                araport_map = {}
            ath_id_to_syms, sym_to_tcs, sym_to_ids = load_ath_table(ath_path)
            mapping = build_symbol_mapping(set(library.entries), set(sym_to_ids),
                                           araport_map, ath_id_to_syms)
            sym_to_ath = {s: v['targets'] for s, v in mapping.items()}
            library.apply_ath_filter(sym_to_ath, set(sym_to_ids), sym_to_tcs, sym_to_ids)
            ath_enabled = True
            n_family_added = len(library.entries) - len(mapping)
            print(f"[ath] Normalization enabled ({ath_path.name}, Symbols {len(sym_to_ids)} / "
                  f"tc {len({t for v in sym_to_tcs.values() for t in v})}): "
                  f"mappable entries {len(mapping)}, dropped as non-ath {len(library.excluded_non_ath)}, "
                  f"family names added {n_family_added}; library matchable {library.matchable}")

    # ---------- discover and load articles (run mode / papers mode) ----------
    fields = list(args.fields) if args.fields else (
        list(PAPERS_DEFAULT_FIELDS) if papers_path else list(DEFAULT_FIELDS))

    articles_by_run: Dict[str, List[Dict]] = {}
    article_rows = 0
    if papers_path is not None:
        if not papers_path.is_file():
            print(f"Error: papers JSON not found: {papers_path}")
            sys.exit(1)
        articles = load_papers_articles(papers_path)
        stem = papers_path.stem
        articles_by_run[stem] = articles
        article_rows = len(articles)
        run_names = [stem]
        print(f"[DATA] papers mode: loaded {len(articles)} papers from {papers_path.name}, "
              f"match fields: {' '.join(fields)}")
    else:
        if not results_dir.is_dir():
            print(f"Error: results directory not found: {results_dir}")
            sys.exit(1)
        runs = discover_runs(results_dir, args.runs)
        if not runs:
            print(f"Error: no run directories found ({results_dir}/run_*)")
            sys.exit(1)
        loaded_runs, failed_runs = 0, 0
        for run_dir in runs:
            result = load_run_articles(run_dir, args.include_rejected, tsv_only=args.tsv_only)
            if result is None:
                print(f"[WARN] run {run_dir.name} has no filtered.json/filtered.tsv, skipped")
                failed_runs += 1
                continue
            articles, n_loaded, n_missing = result
            articles_by_run[run_dir.name] = articles
            article_rows += len(articles)
            loaded_runs += 1
            if n_missing:
                print(f"[WARN] run {run_dir.name}: {n_missing} screening files missing")
        if loaded_runs == 0:
            print("Error: all runs failed to load")
            sys.exit(1)
        run_names = [p.name for p in runs]
        print(f"[DATA] runs loaded {loaded_runs}/{len(runs)}; article rows {article_rows}")

    records = dedupe_articles(run_names, articles_by_run)
    print(f"Unique articles after dedup: {len(records)}")

    # ---------- Stage A: deterministic matching ----------
    matcher = Matcher(library, extra_prefixes=args.extra_prefixes,
                      use_prefix=not args.strict, use_plural=not args.no_plural)
    article_hits: Dict[str, Dict[str, Dict]] = {}
    pbar = tqdm(total=len(records), desc='Deterministic matching', unit='paper')
    for identity in sorted(records):
        article_hits[identity] = matcher.match_article(records[identity]['article'], fields)
        pbar.update()
    pbar.close()

    # ---------- Stage B: LLM agent extraction ----------
    use_llm = args.use_llm or os.environ.get('USE_LLM_API', 'false').lower() == 'true'
    llm_stats = None
    new_symbols: Dict[str, Dict] = {}
    data_dir = papers_path.parent if papers_path is not None else results_dir
    if use_llm:
        try:
            config = resolve_llm_config(args)
            print("\n" + "=" * 60)
            print("LLM agent extraction (Stage B)")
            print("=" * 60)
            print(f"API URL: {config['api_url']}")
            print(f"API Key: {config['api_key'][:10]}...{config['api_key'][-4:]}")
            print(f"Model: {config['model']}")
            extractor = LLMSymbolExtractor(**config)
            cache = LLMCache(data_dir / 'llm_symbol_cache.json', config['model'])
            if cache.entries and not args.refresh_llm:
                print(f"[CACHE] {len(cache.entries)} papers hit (model={config['model']})")
            llm_results, ext_stats = run_llm_extraction(
                extractor, records, cache, args.refresh_llm,
                args.llm_batch_size, args.max_workers)
            dropped: Dict[str, int] = {}
            new_symbols = merge_llm_results(records, article_hits, llm_results, matcher, library,
                                            record_new=not ath_enabled, dropped=dropped)
            llm_stats = {'model': config['model'], **ext_stats, **dropped}
            print(f"[LLM] Extraction done: cache hits {ext_stats['cache_hits']} | "
                  f"API batches {ext_stats['batches']} | failed {ext_stats['failed']} papers")
        except ImportError:
            print("[WARN] openai package missing (pip install openai); LLM extraction skipped, deterministic results only")
        except Exception as e:
            print(f"[WARN] LLM extraction failed ({e}); degraded to deterministic results only")

    # ath mode: rewrite hit keys from library symbols to ath symbols (multi-label
    # expansion; must run after the LLM merge)
    if ath_enabled:
        convert_hits_to_ath(article_hits, sym_to_ath)

    # ---------- build and write outputs ----------
    payload = build_output(records, article_hits, new_symbols, run_names, library, args, llm_stats,
                           fields=fields,
                           papers_json_path=str(papers_path) if papers_path else None,
                           ath_mode=ath_enabled)
    payload['stats']['article_rows_scanned'] = article_rows
    payload['options']['prefixes'] = list(matcher.prefixes) if not args.strict else []
    payload['options']['ath_filter'] = ath_enabled

    output_dir = Path(args.output_dir) if args.output_dir else data_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    map_json = output_dir / f"{args.output_prefix}.json"
    map_tsv = output_dir / f"{args.output_prefix}.tsv"
    with open(map_json, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    n_rows = write_map_tsv(payload, map_tsv)
    if n_rows != payload['stats']['symbol_article_pairs']:
        print(f"[WARN] TSV rows {n_rows} disagree with counted pairs {payload['stats']['symbol_article_pairs']}")

    fam_tsv = output_dir / f"{FAMILY_MAP_PREFIX}.tsv"
    fam_rows = write_family_tsv(payload['families'], fam_tsv)
    if fam_rows != sum(f['article_count'] for f in payload['families'].values()):
        print(f"[WARN] Family TSV rows {fam_rows} disagree with the stats")

    new_entries: List[Dict] = []
    if new_symbols:
        ns_payload, new_entries = build_new_symbols_output(new_symbols, records, matcher.prefixes)
        ns_json = output_dir / f"{NEW_SYMBOLS_PREFIX}.json"
        ns_tsv = output_dir / f"{NEW_SYMBOLS_PREFIX}.tsv"
        with open(ns_json, 'w', encoding='utf-8') as f:
            json.dump(ns_payload, f, ensure_ascii=False, indent=2)
        write_new_symbols_tsv(new_entries, ns_tsv)

    unmatched = [payload['articles'][i] for i in sorted(payload['articles'])
                 if not payload['articles'][i]['matched_symbols']]
    print_summary(payload, args, unmatched)

    print("\n" + "=" * 60)
    print("Output files:")
    print(f"  {map_json}")
    print(f"  {map_tsv}")
    print(f"  {fam_tsv}")
    if new_symbols:
        print(f"  {output_dir / (NEW_SYMBOLS_PREFIX + '.json')}")
        print(f"  {output_dir / (NEW_SYMBOLS_PREFIX + '.tsv')}")
    print("=" * 60)
    sys.exit(0)


if __name__ == '__main__':
    main()
