#!/usr/bin/env python3
"""
Summary tables:
  metrics_by_relation_type.csv  relation per-type + micro
  metrics_by_entity_type.csv    entity per-type + micro
  metrics_by_type.csv           combined per-type metrics
"""
import os
import json, csv, sys
sys.path.insert(0, '.')
from evaluate_one_to_one_matching import compute

REL_META = {
    'transports': {'def': 'Transport activity', 'dir': 'transport_protein → substrate', 'eg': '(OsNRAMP5, transports, Mn)'},
    'location':   {'def': 'Subcellular/membrane localization', 'dir': 'transport_protein → membrane', 'eg': '(GmMATE100, location, vacuole)'},
}
ENT_META = {
    'transport_protein/gene': {'def': 'Transporter or gene', 'norm': 'exact or DOI-scoped evidenced alias', 'eg': 'MGT6, OsHAK5, HKT1'},
    'substrate':  {'def': 'Substrate (ion/metabolite/hormone)', 'norm': 'typed canonical aliases', 'eg': 'Mg2+, K+, sucrose, auxin'},
    'membrane':   {'def': 'Membrane or subcellular localization', 'norm': 'typed canonical aliases', 'eg': 'plasma membrane, tonoplast'},
}
ORDER_REL = ['transports', 'location']
ORDER_ENT = ['transport_protein/gene', 'substrate', 'membrane']

def fmt(x): return f'{x:.4f}'

def build(tp_total, tp_by_key, pred_by_key, gold_by_key, order, meta, mk):
    rows = []
    for k in order:
        if k not in gold_by_key and k not in pred_by_key and k not in tp_by_key:
            continue
        ng = gold_by_key.get(k, 0); np_ = pred_by_key.get(k, 0); tp = tp_by_key.get(k, 0)
        fp = np_ - tp; fn = ng - tp
        P = tp/(tp+fp) if tp+fp else 0; R = tp/(tp+fn) if tp+fn else 0; F = 2*P*R/(P+R) if P+R else 0
        m = meta.get(k, {})
        rows.append([k, m.get(mk[0], ''), m.get(mk[1], ''), ng, np_, tp, fp, fn, fmt(P), fmt(R), fmt(F), m.get('eg', '')])
    sg = sum(gold_by_key.values()); sp = sum(pred_by_key.values()); stp = tp_total
    fp = sp - stp; fn = sg - stp
    P = stp/(stp+fp) if stp+fp else 0; R = stp/(stp+fn) if stp+fn else 0; F = 2*P*R/(P+R) if P+R else 0
    rows.append(['MICRO (micro-average)', '—', '—', sg, sp, stp, fp, fn, fmt(P), fmt(R), fmt(F), '—'])
    return rows, (sg, sp, stp, fp, fn, P, R, F)

def write_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f).writerows([header] + rows)
    print(f'  -> {path}  ({len(rows)} rows)')

def main():
    os.makedirs('results', exist_ok=True)
    pred = json.load(open('predictions/predicted_triples.json', encoding='utf-8'))
    gold = json.load(open('data/gold_triples.json', encoding='utf-8'))
    rt = compute(pred, gold, 'triple'); et = compute(pred, gold, 'entity')
    rel_rows, rel_micro = build(rt[0], rt[1], rt[2], rt[3], ORDER_REL, REL_META, ('def', 'dir'))
    ent_rows, ent_micro = build(et[0], et[1], et[2], et[3], ORDER_ENT, ENT_META, ('def', 'norm'))

    write_csv('results/metrics_by_relation_type.csv',
              ['Relation type', 'Definition', 'Head→Tail', '#Gold', '#Pred', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'F1', 'Example'], rel_rows)
    write_csv('results/metrics_by_entity_type.csv',
              ['Entity type', 'Definition', 'Normalization/matching', '#Gold', '#Pred', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'F1', 'Example'], ent_rows)

    # ===== combined per-type metrics =====
    HDR = ['scope', 'type', 'input(#Gold)', '#Pred', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'F1']
    merged = []
    for r in ent_rows:
        merged.append(['entity', r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]])
    for r in rel_rows:
        merged.append(['relation', r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]])
    write_csv('results/metrics_by_type.csv', HDR, merged)

    print('\n=== core-scope micro averages ===')
    sg, sp, stp, fp, fn, P, R, F = rel_micro
    print(f'  relation: #Gold={sg} #Pred={sp} TP={stp} FP={fp} FN={fn}  P={P:.4f} R={R:.4f} F1={F:.4f}')
    sg, sp, stp, fp, fn, P, R, F = ent_micro
    print(f'  entity: #Gold={sg} #Pred={sp} TP={stp} FP={fp} FN={fn}  P={P:.4f} R={R:.4f} F1={F:.4f}')

if __name__ == '__main__':
    main()
