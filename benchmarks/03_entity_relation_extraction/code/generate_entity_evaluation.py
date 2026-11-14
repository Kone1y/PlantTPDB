#!/usr/bin/env python3
"""
Entity-level detail table -> results/evaluation_entities.csv.
One row per deduplicated evaluation unit, with raw names retained.
Identity mappings and one-to-one matching are shared with the summary scorer.
Columns: source_paper_id (DOI), status, gold_entity, gold_entity_type,
    pred_entity, pred_entity_type, title, abstract
  - TP: both gold_entity and pred_entity filled (may differ under configured name mappings)
  - FP: pred_entity only (gold empty)
  - FN: gold_entity only (pred empty)
"""
import os
import json, csv, sys, re
from collections import defaultdict
sys.path.insert(0, '.')
from evaluate_one_to_one_matching import max_match, collect_entities, is_entity_match, compute, _exact_entity

PRED_FILE = 'predictions/predicted_triples.json'
GOLD_FILE = 'data/gold_triples.json'
ABSTRACT_FILE = 'data/source_abstract_records.json'
OUT_FILE = 'results/evaluation_entities.csv'

def strip_jats(s):
    s = re.sub(r'<jats:[^>]+>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def grp(t):
    t = t.strip()
    return 'transport_protein/gene' if t in ('gene', 'transport_protein') else t

def main():
    os.makedirs('results', exist_ok=True)
    pred = json.load(open(PRED_FILE, encoding='utf-8'))
    gold = json.load(open(GOLD_FILE, encoding='utf-8'))
    ab = json.load(open(ABSTRACT_FILE, encoding='utf-8'))

    title_of, abs_of = {}, {}
    for r in ab:
        title_of[r['doi']] = (r.get('title') or '').strip()
        abs_of[r['doi']] = strip_jats(r.get('abstract') or '')

    pe, ge = defaultdict(list), defaultdict(list)
    for item in collect_entities(pred): pe[item['source_paper_id']].append(item)
    for item in collect_entities(gold): ge[item['source_paper_id']].append(item)

    ent_match = is_entity_match
    HEADER = ['source_paper_id (DOI)', 'status',
              'gold_entity', 'gold_entity_type', 'pred_entity', 'pred_entity_type',
              'title', 'abstract']

    def row(doi, status, g=None, p=None):
        g = g or {}; p = p or {}
        return [doi, status,
                g.get('name',''), g.get('type',''),
                p.get('name',''), p.get('type',''),
                title_of.get(doi,''), abs_of.get(doi,'')]

    rows = []
    tp = fp = fn = 0
    for doi in sorted(set(pe) | set(ge)):
        local = []
        pp = pe[doi]
        gg = ge[doi]
        pk = defaultdict(list); gk = defaultdict(list)
        for i, p in enumerate(pp): pk[grp(p['type'])].append(i)
        for j, g in enumerate(gg): gk[grp(g['type'])].append(j)
        mp, mg = set(), set()
        for k in sorted(set(pk) | set(gk)):
            pidxs = pk.get(k, []); gidxs = gk.get(k, [])
            for pi, gj in max_match([pp[i] for i in pidxs], [gg[j] for j in gidxs], ent_match, _exact_entity):
                gp, ggi = pidxs[pi], gidxs[gj]
                mp.add(gp); mg.add(ggi)
                local.append(('TP', gg[ggi], pp[gp])); tp += 1
        for i, p in enumerate(pp):
            if i not in mp: local.append(('FP', None, p)); fp += 1
        for j, g in enumerate(gg):
            if j not in mg: local.append(('FN', g, None)); fn += 1
        for status, g, p in local:
            rows.append(row(doi, status, g, p))

    with open(OUT_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(HEADER); w.writerows(rows)
    print(f'wrote {len(rows)} rows -> {OUT_FILE}')
    expected = compute(pred, gold, 'entity')
    expected_counts = (expected[0], sum(expected[2].values()) - expected[0],
                       sum(expected[3].values()) - expected[0])
    print(f'  TP={tp} FP={fp} FN={fn}')
    assert (tp, fp, fn) == expected_counts, 'entity detail and summary disagree'
    print('  self-consistency OK')

if __name__ == '__main__':
    main()
