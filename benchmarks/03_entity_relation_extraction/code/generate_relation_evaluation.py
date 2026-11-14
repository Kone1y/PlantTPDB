#!/usr/bin/env python3
"""
Generate results/evaluation_relations.csv — symmetric gold_*/pred_* column layout.
Columns: source_paper_id (DOI), status, gold_head, gold_head_type, gold_relation,
    gold_tail, gold_tail_type, pred_head, pred_head_type, pred_relation,
    pred_tail, pred_tail_type, title, abstract
  - TP: both gold_* and pred_* filled (raw names; relation types use the evaluation scope)
  - FP: pred_* only (gold_* empty)
  - FN: gold_* only (pred_* empty)
Matching reuses evaluate_one_to_one_matching (1-to-1 maximum bipartite matching)
and is self-consistent with the final F1.
"""
import os
import json, csv, sys, re
from collections import defaultdict
sys.path.insert(0, '.')
from evaluate_one_to_one_matching import is_triple_match, max_match, prepare_triples, _exact_triple

PRED_FILE = 'predictions/predicted_triples.json'
GOLD_FILE = 'data/gold_triples.json'
ABSTRACT_FILE = 'data/source_abstract_records.json'
OUT_FILE = 'results/evaluation_relations.csv'

def strip_jats(s):
    s = re.sub(r'<jats:[^>]+>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)          # residual tags
    s = re.sub(r'\s+', ' ', s).strip()      # collapse whitespace
    return s

def main():
    os.makedirs('results', exist_ok=True)
    pred = json.load(open(PRED_FILE, encoding='utf-8'))
    gold = json.load(open(GOLD_FILE, encoding='utf-8'))
    ab = json.load(open(ABSTRACT_FILE, encoding='utf-8'))

    pred, gold = prepare_triples(pred), prepare_triples(gold)

    title_of, abs_of = {}, {}
    for r in ab:
        d = r.get('doi')
        title_of[d] = (r.get('title') or '').strip()
        abs_of[d] = strip_jats(r.get('abstract') or '')

    pred_g = defaultdict(list)
    gold_g = defaultdict(list)
    for p in pred: pred_g[p['source_paper_id']].append(p)
    for g in gold: gold_g[g['source_paper_id']].append(g)

    HEADER = ['source_paper_id (DOI)', 'status',
              'gold_head', 'gold_head_type', 'gold_relation', 'gold_tail', 'gold_tail_type',
              'pred_head', 'pred_head_type', 'pred_relation', 'pred_tail', 'pred_tail_type',
              'title', 'abstract']

    def row(doi, status, g=None, p=None):
        g = g or {}; p = p or {}
        return [doi, status,
                g.get('head',''), g.get('head_type',''), g.get('relation',''), g.get('tail',''), g.get('tail_type',''),
                p.get('head',''), p.get('head_type',''), p.get('relation',''), p.get('tail',''), p.get('tail_type',''),
                title_of.get(doi,''), abs_of.get(doi,'')]

    allrows = []
    tp = fp = fn = 0
    for doi in sorted(set(pred_g) | set(gold_g)):
        local = []
        pp = pred_g.get(doi, []); gg = gold_g.get(doi, [])
        pk = defaultdict(list); gk = defaultdict(list)
        for i, p in enumerate(pp): pk[p['relation']].append(i)
        for j, g in enumerate(gg): gk[g['relation']].append(j)
        matched_p, matched_g = set(), set()
        for rel in sorted(set(pk) | set(gk)):
            pidxs = pk.get(rel, []); gidxs = gk.get(rel, [])
            sub_p = [pp[i] for i in pidxs]
            sub_g = [gg[j] for j in gidxs]
            for pi, gj in max_match(sub_p, sub_g, is_triple_match, _exact_triple):
                gp, ggi = pidxs[pi], gidxs[gj]
                matched_p.add(gp); matched_g.add(ggi)
                local.append(('TP', gg[ggi], pp[gp])); tp += 1
        for i, p in enumerate(pp):
            if i not in matched_p:
                local.append(('FP', None, p)); fp += 1
        for j, g in enumerate(gg):
            if j not in matched_g:
                local.append(('FN', g, None)); fn += 1
        for status, g, p in local:
            allrows.append(row(doi, status, g, p))

    with open(OUT_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(HEADER); w.writerows(allrows)
    print(f'wrote {len(allrows)} rows -> {OUT_FILE}')
    print(f'TP={tp} FP={fp} FN={fn}   (gold={len(gold)}  pred={len(pred)})')
    # self-consistency checks
    assert tp + fp == len(pred), f'pred row mismatch: {tp+fp} vs {len(pred)}'
    assert tp + fn == len(gold), f'gold row mismatch: {tp+fn} vs {len(gold)}'
    print('self-consistency OK: TP+FP==#pred, TP+FN==#gold')

if __name__ == '__main__':
    main()
