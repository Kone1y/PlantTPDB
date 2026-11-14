#!/usr/bin/env python3
"""One-to-one maximum bipartite matching for P/R/F1 evaluation.

Usage:
  python3 evaluate_one_to_one_matching.py predictions/predicted_triples.json data/gold_triples.json
  python3 evaluate_one_to_one_matching.py <predicted.json> <gold.json> [--diagnostics out.jsonl]

Matching policy (defined in matching_rules.json):
- A prediction unit matches a gold unit only on identical DOI, identical
  relation label, type-compatible endpoints, and *equal canonical entity
  identity*: the normalized literal name, a typed alias group
  (e.g. K+/potassium, tonoplast/vacuolar membrane), or a DOI-scoped
  evidenced protein-name mapping. Generic substring and edit-distance
  similarity is not used.
- Both sides are deduplicated by canonical identity (per DOI, relation,
  endpoint types and canonical head/tail) before matching.
- Matching is a maximum-cardinality, maximum-quality one-to-one assignment
  (min-cost max-flow); each prediction or gold unit is counted at most once,
  so TP/FP/FN are self-consistent.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
from typing import Callable

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'matching_rules.json')
if not os.path.exists(RULES_FILE):
    RULES_FILE = 'matching_rules.json'          # fallback: current dir
with open(RULES_FILE, encoding='utf-8') as _f:
    RULES = json.load(_f)

PROTEIN_TYPES = ('gene', 'transport_protein')


def normalize_name(value: str) -> str:
    """Orthographic normalization; preserves charge, oxidation state and stereochemistry."""
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = re.sub(r'<[^>]*>', ' ', text)
    for char in ('‐', '‑', '‒', '–', '—', '−', '―'):
        text = text.replace(char, '-')
    text = text.replace('·', '.').replace('​', '')
    # Remove spaces only immediately before an ion charge (NH4 +, Cd 2+).
    text = re.sub(r'(?<=[A-Za-z0-9])\s+(?=\d*[+-](?:$|\s))', '', text)
    return re.sub(r'\s+', ' ', text).strip().casefold()


def _type(value: str) -> str:
    return RULES['type_aliases'].get(str(value).strip(), str(value).strip())


def _doi(value: str) -> str:
    return str(value).strip().lower().removeprefix('https://doi.org/')


# Typed aliases: a name is never mapped via an alias of another entity type.
ALIASES: dict[tuple[str, str], str] = {}
for item in RULES['entity_aliases']:
    kind, ident = item['type'], item['id']
    for name in item['names']:
        key = (kind, normalize_name(name))
        if key in ALIASES and ALIASES[key] != ident:
            raise ValueError(f'Conflicting typed alias: {key!r}: {ALIASES[key]} != {ident}')
        ALIASES[key] = ident

PROTEIN_NAME_MAP: dict[tuple[str, str], str] = {}
for group in RULES.get('protein_name_mappings', []):
    if not str(group.get('evidence', '')).strip():
        raise ValueError('Every protein alias requires an evidence string')
    for name in group['names']:
        key = (_doi(group['doi']), normalize_name(name))
        val = normalize_name(group['canonical'])
        if key in PROTEIN_NAME_MAP and PROTEIN_NAME_MAP[key] != val:
            raise ValueError(f'Conflicting protein alias: {key!r}')
        PROTEIN_NAME_MAP[key] = val

UNMATCHED_COMPOSITES = {
    (_doi(group['doi']), normalize_name(name))
    for group in RULES.get('unmatched_protein_composites', [])
    for name in group['names']
}


def entity_identity(doi: str, name: str, entity_type: str) -> str:
    """Canonical identity for within-side deduplication."""
    kind = _type(entity_type)
    norm = normalize_name(name)
    if kind == 'transport_protein':
        return 'protein:' + PROTEIN_NAME_MAP.get((_doi(doi), norm), norm)
    ident = ALIASES.get((kind, norm))
    return f'{kind}:{ident}' if ident is not None else f'{kind}:literal:{norm}'


def match_name(p_name: str, p_type: str, g_name: str, g_type: str, doi: str) -> tuple[int, str]:
    """Match score and reason. Zero = no match. No fuzzy matching."""
    p_type, g_type = _type(p_type), _type(g_type)
    if p_type != g_type:
        return 0, 'type_mismatch'
    a, b = normalize_name(p_name), normalize_name(g_name)
    if not a or not b:
        return 0, 'empty'
    if p_type == 'transport_protein':
        if ((_doi(doi), a) in UNMATCHED_COMPOSITES or
                (_doi(doi), b) in UNMATCHED_COMPOSITES):
            # A composite is not auto-identified with one subunit.
            return (1000, 'exact') if a == b else (0, 'composite_identity_block')
        if a == b:
            return 1000, 'exact'
        ident_a = PROTEIN_NAME_MAP.get((_doi(doi), a), a)
        ident_b = PROTEIN_NAME_MAP.get((_doi(doi), b), b)
        if ident_a == ident_b:
            return 940, 'doi_protein_alias'
    elif a == b:
        return 1000, 'exact'
    alias_a = ALIASES.get((p_type, a))
    alias_b = ALIASES.get((g_type, b))
    if alias_a is not None and alias_a == alias_b:
        return 920, f'typed_alias:{alias_a}'
    return 0, 'not_equivalent'


def prepare_triples(triples: list[dict]) -> list[dict]:
    """Deduplicate DOI-scoped semantic units before matching."""
    out = {}
    for item in triples:
        doi = _doi(item['source_paper_id'])
        key = (doi, _type(item['head_type']), entity_identity(doi, item['head'], item['head_type']),
               item['relation'], _type(item['tail_type']),
               entity_identity(doi, item['tail'], item['tail_type']))
        out.setdefault(key, item)
    return list(out.values())


def collect_entities(triples: list[dict]) -> list[dict]:
    out = {}
    for item in triples:
        doi = _doi(item['source_paper_id'])
        for name_key, type_key in (('head', 'head_type'), ('tail', 'tail_type')):
            name, kind = item[name_key], item[type_key]
            key = (doi, _type(kind), entity_identity(doi, name, kind))
            out.setdefault(key, {'name': name, 'type': kind, 'source_paper_id': item['source_paper_id']})
    return list(out.values())


def type_compatible(a: str, b: str) -> bool:
    return _type(a) == _type(b)


def entity_name_match(n1: str, t1: str, n2: str, t2: str, doi: str | None = None) -> bool:
    return match_name(n1, t1, n2, t2, doi or '')[0] > 0


def is_entity_match(pred: dict, gold: dict) -> bool:
    return (_doi(pred['source_paper_id']) == _doi(gold['source_paper_id'])
            and entity_name_match(pred['name'], pred['type'], gold['name'], gold['type'], pred['source_paper_id']))


def triple_match_score(pred: dict, gold: dict) -> tuple[int, str]:
    doi = _doi(pred['source_paper_id'])
    if doi != _doi(gold['source_paper_id']):
        return 0, 'doi_mismatch'
    if pred['relation'] != gold['relation']:
        return 0, 'relation_mismatch'
    if not (type_compatible(pred['head_type'], gold['head_type']) and
            type_compatible(pred['tail_type'], gold['tail_type'])):
        return 0, 'type_mismatch'
    hs, hr = match_name(pred['head'], pred['head_type'], gold['head'], gold['head_type'], doi)
    if not hs:
        return 0, 'head:' + hr
    ts, tr = match_name(pred['tail'], pred['tail_type'], gold['tail'], gold['tail_type'], doi)
    if not ts:
        return 0, 'tail:' + tr
    # Head identity outweighs tail identity among otherwise equal assignments.
    return hs * 10000 + ts, 'head:' + hr + ';tail:' + tr


def is_triple_match(pred: dict, gold: dict) -> bool:
    return triple_match_score(pred, gold)[0] > 0


def _exact_entity(pred: dict, gold: dict) -> int:
    return match_name(pred['name'], pred['type'], gold['name'], gold['type'], pred['source_paper_id'])[0]


def _exact_triple(pred: dict, gold: dict) -> int:
    return triple_match_score(pred, gold)[0]


@dataclass
class _Edge:
    to: int
    rev: int
    capacity: int
    cost: int


def _edge(graph: list[list[_Edge]], a: int, b: int, capacity: int, cost: int):
    graph[a].append(_Edge(b, len(graph[b]), capacity, cost))
    graph[b].append(_Edge(a, len(graph[a])-1, 0, -cost))


def max_match(pp: list[dict], gg: list[dict], match_fn: Callable,
              is_exact_fn: Callable | None = None) -> list[tuple[int, int]]:
    """Maximum-cardinality, maximum-quality one-to-one matching (min-cost max-flow).

    Match count is maximized first, globally; among maximum-cardinality
    assignments the sum of match qualities (exact > alias) is maximized, with
    deterministic tie-breaking. Each unit is used at most once on either side.
    """
    if not pp or not gg:
        return []
    n, m = len(pp), len(gg)
    source, sink = n + m, n + m + 1
    N = sink + 1
    graph: list[list[_Edge]] = [[] for _ in range(N)]
    for i in range(n):
        _edge(graph, source, i, 1, 0)
    for j in range(m):
        _edge(graph, n + j, sink, 1, 0)
    edge_scores: dict[tuple[int,int], int] = {}
    for i, p in enumerate(pp):
        for j, g in enumerate(gg):
            if match_fn(p, g):
                score = int(is_exact_fn(p, g)) if is_exact_fn else 1000
                edge_scores[i, j] = max(1, score)
    if not edge_scores:
        return []
    # Quality dominates deterministic tie-breaking by construction of the scale.
    maxscore = max(edge_scores.values())
    tie_scale = 1 + min(n,m)*n*m + m
    for (i, j), score in edge_scores.items():
        cost = (maxscore - score)*tie_scale + i*m + j
        _edge(graph, i, n + j, 1, cost)
    potential = [0]*N
    INF = 10**36
    while True:
        dist = [INF]*N
        prev_node = [-1]*N
        prev_idx = [-1]*N
        dist[source] = 0
        heap = [(0, source)]
        while heap:
            d,u = heapq.heappop(heap)
            if d != dist[u]:
                continue
            for k,e in enumerate(graph[u]):
                if not e.capacity:
                    continue
                nd = d + e.cost + potential[u] - potential[e.to]
                if nd < dist[e.to]:
                    dist[e.to] = nd
                    prev_node[e.to] = u
                    prev_idx[e.to] = k
                    heapq.heappush(heap, (nd, e.to))
        if dist[sink] == INF:
            break
        for v in range(N):
            if dist[v] < INF:
                potential[v] += dist[v]
        v = sink
        while v != source:
            u, k = prev_node[v], prev_idx[v]
            e = graph[u][k]
            e.capacity -= 1
            graph[v][e.rev].capacity += 1
            v = u
    return sorted((i, e.to-n) for i in range(n)
                  for e in graph[i] if n <= e.to < n+m and e.capacity == 0)


def _key(item: dict, mode: str) -> str:
    if mode == 'entity':
        return 'transport_protein/gene' if _type(item['type']) == 'transport_protein' else _type(item['type'])
    return item['relation']


def _evaluate(triples_pred: list[dict], triples_gold: list[dict], mode: str) -> tuple[list[dict], dict]:
    if mode not in ('triple', 'entity'):
        raise ValueError('mode must be triple or entity')
    prep = prepare_triples if mode == 'triple' else collect_entities
    matcher = is_triple_match if mode == 'triple' else is_entity_match
    score_fn = _exact_triple if mode == 'triple' else _exact_entity
    pp, gg = prep(triples_pred), prep(triples_gold)
    p_group: dict[tuple[str,str],list[dict]] = defaultdict(list)
    g_group: dict[tuple[str,str],list[dict]] = defaultdict(list)
    for item in pp:
        p_group[(_doi(item['source_paper_id']), _key(item, mode))].append(item)
    for item in gg:
        g_group[(_doi(item['source_paper_id']), _key(item, mode))].append(item)
    rows: list[dict] = []
    tp_by_key = Counter()
    pred_by_key = Counter()
    gold_by_key = Counter()
    for doi, kind in sorted(set(p_group) | set(g_group)):
        pp0, gg0 = p_group[(doi, kind)], g_group[(doi, kind)]
        pairs = max_match(pp0, gg0, matcher, score_fn)
        matched_p, matched_g = set(), set()
        tp_by_key[kind] += len(pairs)
        pred_by_key[kind] += len(pp0)
        gold_by_key[kind] += len(gg0)
        for i,j in pairs:
            matched_p.add(i)
            matched_g.add(j)
            if mode == 'triple':
                grade,why = triple_match_score(pp0[i],gg0[j])
            else:
                grade,why = match_name(pp0[i]['name'], pp0[i]['type'], gg0[j]['name'], gg0[j]['type'], doi)
            rows.append({'doi':doi, 'mode':mode, 'category':kind, 'status':'TP',
                         'gold':gg0[j], 'prediction':pp0[i], 'match_grade':grade, 'match_reason':why})
        for i,p in enumerate(pp0):
            if i not in matched_p:
                rows.append({'doi':doi,'mode':mode,'category':kind,'status':'FP',
                             'gold':None,'prediction':p,'match_grade':0,'match_reason':'unmatched'})
        for j,g in enumerate(gg0):
            if j not in matched_g:
                rows.append({'doi':doi,'mode':mode,'category':kind,'status':'FN',
                             'gold':g,'prediction':None,'match_grade':0,'match_reason':'unmatched'})
    return rows, {'TP':sum(tp_by_key.values()),'FP':len(pp)-sum(tp_by_key.values()),
                  'FN':len(gg)-sum(tp_by_key.values()), 'pred':len(pp),'gold':len(gg),
                  'tp_by_key':tp_by_key,'pred_by_key':pred_by_key,'gold_by_key':gold_by_key}


def compute(triples_pred: list[dict], triples_gold: list[dict], mode: str):
    _, stats = _evaluate(triples_pred, triples_gold, mode)
    return (stats['TP'], stats['tp_by_key'], stats['pred_by_key'], stats['gold_by_key'],
            'relation type' if mode == 'triple' else 'entity type')


def report(tp_total: int, tp_by_key: dict, pred_by_key: dict, gold_by_key: dict, label: str):
    print(f"\n  {label:25} {'#Gold':>6} {'#Pred':>6} {'TP':>5} {'FP':>5} {'FN':>5} {'P':>7} {'R':>7} {'F1':>7}")
    for k in sorted(set(tp_by_key) | set(pred_by_key) | set(gold_by_key)):
        tp=tp_by_key.get(k,0);p=pred_by_key.get(k,0);g=gold_by_key.get(k,0)
        P=tp/p if p else 0;R=tp/g if g else 0;F=2*tp/(p+g) if p+g else 0
        print(f'  {k:25} {g:>6} {p:>6} {tp:>5} {p-tp:>5} {g-tp:>5} {P:>7.4f} {R:>7.4f} {F:>7.4f}')
    p=sum(pred_by_key.values());g=sum(gold_by_key.values());tp=tp_total
    P=tp/p if p else 0;R=tp/g if g else 0;F=2*tp/(p+g) if p+g else 0
    print(f'  {"MICRO":25} {g:>6} {p:>6} {tp:>5} {p-tp:>5} {g-tp:>5} {P:>7.4f} {R:>7.4f} {F:>7.4f}')
    return P,R,F


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pred', nargs='?', default='predictions/predicted_triples.json')
    parser.add_argument('gold', nargs='?', default='data/gold_triples.json')
    parser.add_argument('--diagnostics', default='results/match_diagnostics.jsonl')
    args = parser.parse_args(argv)
    with open(args.pred, encoding='utf-8') as f: pred = json.load(f)
    with open(args.gold, encoding='utf-8') as f: gold = json.load(f)
    print('=== One-to-one typed matching evaluation ===')
    print(f'Pred: {len(pred)}   Gold: {len(gold)}')
    diag = []
    for mode in ('triple', 'entity'):
        rows, stats = _evaluate(pred, gold, mode)
        p, r, f = report(stats['TP'], stats['tp_by_key'], stats['pred_by_key'], stats['gold_by_key'],
                         'relation type' if mode == 'triple' else 'entity type')
        print(f'  {mode}: TP={stats["TP"]} FP={stats["FP"]} FN={stats["FN"]}; micro F1={f:.4f}')
        diag.extend(rows)
    if args.diagnostics:
        path = Path(args.diagnostics)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as fh:
            for row in diag:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        print(f'Match diagnostics: {path}')


if __name__ == '__main__':
    main()
