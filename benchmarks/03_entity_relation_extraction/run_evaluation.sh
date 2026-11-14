#!/bin/bash
# Reproduce the entity–relation extraction benchmark tables from the released data.
#
# Usage:  bash run_evaluation.sh [workdir]        (default workdir: eval_output)
#
# Produces, inside the workdir:
#   results/evaluation_relations.csv       relation-level detail table
#   results/evaluation_entities.csv        entity-level detail table
#   results/metrics_by_relation_type.csv   relation per-type + micro summary
#   results/metrics_by_entity_type.csv     entity per-type + micro summary
# and checks the generated tables against results/reference/.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
WORK="${1:-eval_output}"
mkdir -p "$WORK"
cp code/*.py code/matching_rules.json "$WORK"/
cp -r data predictions "$WORK"/
mkdir -p "$WORK/results"
cd "$WORK"

echo "== [1/4] Entity and relation metrics =="
python3 evaluate_one_to_one_matching.py predictions/predicted_triples.json data/gold_triples.json | tee f1_full_scope.txt

echo "== [2/4] Relation-level detail table =="
python3 generate_relation_evaluation.py

echo "== [3/4] Entity-level detail table =="
python3 generate_entity_evaluation.py

echo "== [4/4] Summary tables =="
python3 summarize_extraction_metrics.py

cd "$SCRIPT_DIR"
echo
echo "== Checking regenerated tables =="
python3 - "$WORK" <<'EOF'
import csv, sys
from collections import Counter

work = sys.argv[1]

def read_csv(path):
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.reader(f))

def read_tsv(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.reader(f, delimiter='\t'))

ok = True

# Compare both source-side multisets, including DOI, independently of pair ordering.
def detail_inv_csv(path, gold_cols, pred_cols):
    rows = read_csv(path)
    hdr = rows[0]
    gi = [hdr.index(c) for c in gold_cols]
    pi = [hdr.index(c) for c in pred_cols]
    st = Counter()
    gold, pred = Counter(), Counter()
    for r in rows[1:]:
        st[r[1]] += 1
        g = tuple(r[i] for i in gi)
        pr = tuple(r[i] for i in pi)
        if any(g):
            gold[(r[0],) + g] += 1
        if any(pr):
            pred[(r[0],) + pr] += 1
    return st, gold, pred

def detail_inv_tsv(path, ncols, n_gold):
    # TSV layout: [DOI, status, gold...(n_gold), pred...(rest)], no title col
    rows = read_tsv(path)
    st = Counter()
    gold, pred = Counter(), Counter()
    for r in rows[2:]:
        if not (r and r[0]):
            continue
        r = r[:ncols]
        st[r[1]] += 1
        g = tuple(r[2:2 + n_gold])
        pr = tuple(r[2 + n_gold:ncols])
        if any(g):
            gold[(r[0],) + g] += 1
        if any(pr):
            pred[(r[0],) + pr] += 1
    return st, gold, pred

for csv_name, tsv_name, gold_cols, pred_cols, ncols, n_gold in [
    ('evaluation_relations.csv', 'results/reference/relation_evaluation.tsv',
     ['gold_head', 'gold_head_type', 'gold_relation', 'gold_tail', 'gold_tail_type'],
     ['pred_head', 'pred_head_type', 'pred_relation', 'pred_tail', 'pred_tail_type'], 12, 5),
    ('evaluation_entities.csv', 'results/reference/entity_evaluation.tsv',
     ['gold_entity', 'gold_entity_type'], ['pred_entity', 'pred_entity_type'], 6, 2),
]:
    a = detail_inv_csv(f'{work}/results/{csv_name}', gold_cols, pred_cols)
    b = detail_inv_tsv(tsv_name, ncols, n_gold)
    match = a == b
    print(f'  {"MATCH" if match else "MISMATCH"}  {csv_name} == {tsv_name} '
          f'(status {dict(a[0])}; gold items {sum(a[1].values())})')
    ok &= match

# --- metrics tables: TP/FP/FN per type + micro row ------------------------
def metrics_csv(path):
    rows = read_csv(path)
    hdr = rows[0]
    i = {h: n for n, h in enumerate(hdr)}
    out = {}
    for r in rows[1:]:
        if r and r[0]:
            out[r[0].split(' ')[0]] = (r[i['TP']], r[i['FP']], r[i['FN']])
    return out

def metrics_tsv(path, keymap):
    rows = read_tsv(path)
    out = {}
    for r in rows[2:]:
        if r and r[0]:
            k = keymap(r[0])
            if k:
                out[k] = (r[3], r[4], r[5])
    return out

RELKEY = lambda s: {'Transports (transport protein → substrate)': 'transports',
                    'Location (transport protein → membrane)': 'location'}.get(s)
ENTKEY = lambda s: {'Transport protein': 'transport_protein/gene', 'Substrate': 'substrate',
                    'Membrane': 'membrane'}.get(s)
for csv_name, tsv_name, keyf in [('metrics_by_relation_type.csv', 'results/reference/relation_metrics.tsv', RELKEY),
                                 ('metrics_by_entity_type.csv', 'results/reference/entity_metrics.tsv', ENTKEY)]:
    a = metrics_csv(f'{work}/results/{csv_name}')
    b = metrics_tsv(tsv_name, keyf)
    missing = {k: v for k, v in a.items() if k not in ('MICRO',) and b.get(k) != v}
    micro_a = a.get('MICRO')
    micro_b = None
    rows = read_tsv(tsv_name)
    for r in rows:
        if r and r[0] == 'Overall (micro-average)':
            micro_b = (r[3], r[4], r[5])
    match = not missing and micro_a == micro_b
    print(f'  {"MATCH" if match else "MISMATCH"}  {csv_name} == {tsv_name} (per-type TP/FP/FN + micro {micro_a})')
    ok &= match

sys.exit(0 if ok else 1)
EOF
echo
echo "Done. Regenerated outputs are in $WORK/."
