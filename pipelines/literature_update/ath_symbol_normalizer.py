#!/usr/bin/env python3
"""
ath_symbol family-level normalizer (ath_symbol_normalizer)

Using the authoritative table ath_symbol_tc_final_v2.tsv (Ath_id -> canonical
Symbol -> TC class), maps the canonical symbols in symbol_literature_map.tsv
onto the ath normalized family Symbol system, building an
"ath_symbol (+tc) -> literature" retrieval mapping.

Mapping levels (data-driven; no hand-enumerated special cases):
  L1 direct match     literature symbol == ath Symbol
  L2 Araport bridged  symbol -> Araport11 gene_symbol -> Ath_id(s) -> ath Symbol(s)
                      (a symbol mapping to several ath Symbols keeps all of them,
                      flagged in the report)
  L3 unmapped         written to a classification report for manual review:
                      - araport_annotated_non_transporter  Arabidopsis gene not in
                        the ath transporter table
                      - no_araport_annotation              no Araport annotation
                        (family variants, non-Arabidopsis spellings, ...)

Inputs:
  --literature-map  symbol_literature_map.tsv   (canonical symbol -> article row map)
  --ath-table       ath_symbol_tc_final_v2.tsv  (Ath_id/Symbol/tc authority table)
  --araport         Araport11_mRNA_info.tsv     (gene_symbol -> Ath_id bridge)

Outputs (written next to --literature-map, prefixed with --output-prefix):
  ath_symbol_literature_map.tsv   one row per (ath_symbol x source row), ready
                                  for direct filtering/retrieval
  ath_symbol_literature_map.json  structured index: ath_symbol/tc -> literature
                                  plus the mapping decision report

Examples:
  python3 ath_symbol_normalizer.py
  python3 ath_symbol_normalizer.py --ath-table ath_symbol_tc_final_v2.tsv --output-prefix ath_symbol_literature_map
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_VERSION = "1.0"


# ============================ data loading ============================

def load_ath_table(path: Path):
    """Load the ath authority table: Ath_id -> {Symbol}, Symbol -> {tc}, Symbol -> {Ath_id}.

    One Ath_id may carry several Symbols (cross-references such as
    DND1=AT5G15410=CNGC2) and one Symbol several tc values (a subfamily
    spanning TC classes); both are kept as sets.
    """
    ath_id_to_syms = defaultdict(set)
    sym_to_tcs = defaultdict(set)
    sym_to_ids = defaultdict(set)
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            sym = (row.get('Symbol') or '').strip()
            ath_id = (row.get('Ath_id') or '').strip().upper()
            tc = (row.get('tc') or '').strip()
            if not sym or not ath_id:
                continue
            ath_id_to_syms[ath_id].add(sym)
            sym_to_ids[sym].add(ath_id)
            if tc:
                sym_to_tcs[sym].add(tc)
    return dict(ath_id_to_syms), dict(sym_to_tcs), dict(sym_to_ids)


def load_araport(path: Path):
    """Load Araport11 annotations: gene_symbol -> {Ath_id} (parent_gene column)."""
    sym_to_ids = defaultdict(set)
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            gs = (row.get('gene_symbol') or '').strip()
            locus = (row.get('parent_gene') or '').strip().upper()
            if gs and locus:
                sym_to_ids[gs].add(locus)
    return dict(sym_to_ids)


def load_literature_map(path: Path):
    """Load the rows of symbol_literature_map.tsv (columns passed through as-is)."""
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


# ============================ mapping construction ============================

def build_symbol_mapping(lit_symbols, ath_syms, araport, ath_id_to_syms):
    """Build the mapping decisions: literature symbol -> ath Symbol.

    Returns {symbol: {'level': 'L1'/'L2', 'targets': set(ath Symbol),
    'ath_ids': set, 'note': str}}. Unmapped symbols are omitted here and
    reported separately by classify_unmapped.
    """
    mapping = {}
    for sym in lit_symbols:
        # L1: literature symbol is itself an ath Symbol
        if sym in ath_syms:
            mapping[sym] = {'level': 'L1', 'targets': {sym},
                            'ath_ids': set(), 'note': 'direct match'}
            continue
        # L2: bridge through Araport11 gene_symbol -> Ath_id -> ath Symbol
        araport_ids = araport.get(sym)
        if not araport_ids:
            continue
        targets = set()
        for ath_id in araport_ids:
            targets.update(ath_id_to_syms.get(ath_id, ()))
        if not targets:
            continue
        note = 'Araport11 bridged'
        if len(araport_ids) > 1:
            note += f'; multi-locus ({len(araport_ids)})'
        if len(targets) > 1:
            note += f'; multi-family ({"/".join(sorted(targets))})'
        mapping[sym] = {'level': 'L2', 'targets': targets,
                        'ath_ids': araport_ids, 'note': note}
    return mapping


def classify_unmapped(lit_symbols, mapped, araport):
    """Classify unmapped symbols: annotated Arabidopsis non-transporter vs no Araport annotation."""
    non_transporter, no_annotation = [], []
    for sym in sorted(lit_symbols):
        if sym in mapped:
            continue
        (non_transporter if sym in araport else no_annotation).append(sym)
    return non_transporter, no_annotation


# ============================ output ============================

def escape(value) -> str:
    if value is None:
        return ""
    return str(value).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


def write_map_tsv(rows, mapping, sym_to_tcs, path: Path) -> int:
    """Flattened TSV: one row per (ath_symbol x source row)."""
    headers = ['ath_symbol', 'tc', 'member_symbol', 'symbol', 'matched_forms',
               'DOI', 'PMID', 'title', 'journal', 'pub_date', 'matched_fields',
               'source_runs', 'status', 'source_file']
    n = 0
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')
        for row in rows:
            # accept both mapper output variants: non-ath mode writes `symbol`,
            # ath mode writes `ath_symbol` (values are already canonical ath symbols)
            sym = (row.get('symbol') or row.get('ath_symbol') or '').strip()
            entry = mapping.get(sym)
            if not entry:
                continue
            for target in sorted(entry['targets']):
                out = [
                    escape(target),
                    escape('; '.join(sorted(sym_to_tcs.get(target, ())))),
                    escape(sym),
                    escape(sym),
                    escape(row.get('matched_forms', '')),
                    escape(row.get('DOI', '')),
                    escape(row.get('PMID', '')),
                    escape(row.get('title', '')),
                    escape(row.get('journal', '')),
                    escape(row.get('pub_date', '')),
                    escape(row.get('matched_fields', '')),
                    escape(row.get('source_runs', '')),
                    escape(row.get('status', '')),
                    escape(row.get('source_file', '')),
                ]
                f.write('\t'.join(out) + '\n')
                n += 1
    return n


def build_json_output(rows, mapping, sym_to_tcs, sym_to_ids, araport,
                      non_transporter, no_annotation, n_tsv_rows, meta) -> dict:
    """Structured output: ath_symbol index + tc index + mapping decision report."""
    ath_index = {}
    for row in rows:
        sym = (row.get('symbol') or '').strip()
        entry = mapping.get(sym)
        if not entry:
            continue
        doi = (row.get('DOI') or '').strip().lower()
        for target in sorted(entry['targets']):
            idx = ath_index.setdefault(target, {
                'tc': sorted(sym_to_tcs.get(target, ())),
                'ath_ids': sorted(sym_to_ids.get(target, ())),
                'row_count': 0, 'articles': {},
            })
            idx['row_count'] += 1
            art = idx['articles'].setdefault(doi or row.get('PMID', '') or row.get('source_file', ''), {
                'doi': row.get('DOI', ''), 'pmid': row.get('PMID', ''),
                'journal': row.get('journal', ''), 'pub_date': row.get('pub_date', ''),
                'status': row.get('status', ''), 'member_symbols': set(), 'runs': set(),
            })
            art['member_symbols'].add(sym)
            art['runs'].add(row.get('source_runs', ''))
    # sets -> sorted lists so the JSON output is deterministic
    for target, idx in ath_index.items():
        for art in idx['articles'].values():
            art['member_symbols'] = sorted(art['member_symbols'])
            art['runs'] = sorted(r for r in art['runs'] if r)
        idx['article_count'] = len(idx['articles'])
        idx['articles'] = [idx['articles'][k] for k in sorted(idx['articles'])]
        idx['member_symbols'] = sorted({s for a in idx['articles'] for s in a['member_symbols']})

    # tc index (tc -> ath symbols -> counts); symbols without tc fall under 'unclassified'
    tc_index = defaultdict(lambda: {'symbols': {}, 'row_count': 0})
    for target, idx in ath_index.items():
        tcs = idx['tc'] or ['unclassified']
        for tc in tcs:
            slot = tc_index[tc]
            slot['symbols'][target] = {'article_count': idx['article_count'],
                                       'row_count': idx['row_count']}
            slot['row_count'] += idx['row_count']
    tc_index = {tc: {'row_count': v['row_count'],
                     'symbols': {s: v['symbols'][s] for s in sorted(v['symbols'])}}
                for tc, v in sorted(tc_index.items())}

    mapped_syms = sorted(mapping)
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'script_version': SCRIPT_VERSION,
        **meta,
        'stats': {
            'literature_symbols_total': len(mapped_syms) + len(non_transporter) + len(no_annotation),
            'symbols_mapped': len(mapped_syms),
            'symbols_L1_direct': sum(1 for v in mapping.values() if v['level'] == 'L1'),
            'symbols_L2_araport': sum(1 for v in mapping.values() if v['level'] == 'L2'),
            'symbols_multi_target': sum(1 for v in mapping.values() if len(v['targets']) > 1),
            'unmapped_non_transporter': len(non_transporter),
            'unmapped_no_annotation': len(no_annotation),
            'literature_rows_total': len(rows),
            'literature_rows_mapped': sum(1 for r in rows
                                          if (r.get('symbol') or '').strip() in mapping),
            'ath_symbols_hit': len(ath_index),
            'ath_symbols_total': len(sym_to_ids),
            'ath_tsv_rows': n_tsv_rows,
        },
        'mapping_report': {
            'note': 'L1=direct match; L2=Araport11 bridged (symbol->Ath_id->ath Symbol); '
                    'unmapped classifications are for manual review only and are '
                    'excluded from the output mapping',
            'mapped': {s: {'level': v['level'], 'targets': sorted(v['targets']),
                           'ath_ids': sorted(v['ath_ids']), 'note': v['note']}
                       for s, v in ((k, mapping[k]) for k in mapped_syms)},
            'unmapped_non_transporter': non_transporter,
            'unmapped_no_annotation': no_annotation,
        },
        'tc_index': tc_index,
        'ath_symbols': ath_index,
    }


# ============================ main ============================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Family-level normalization against ath_symbol_tc_final_v2: '
                    'build the ath_symbol(+tc) -> literature mapping')
    parser.add_argument('--literature-map', default='symbol_literature_map.tsv',
                        help='row-level mapping TSV produced by symbol_literature_mapper')
    parser.add_argument('--ath-table', default='ath_symbol_tc_final_v2.tsv',
                        help='Ath_id/Symbol/tc authority table')
    parser.add_argument('--araport', default='Araport11_mRNA_info.tsv',
                        help='Araport11 annotation table (gene_symbol -> parent_gene bridge)')
    parser.add_argument('--output-prefix', default='ath_symbol_literature_map',
                        help='output file prefix (files are written next to --literature-map)')
    return parser.parse_args()


def main():
    args = parse_args()
    lit_path = Path(args.literature_map)
    ath_path = Path(args.ath_table)
    araport_path = Path(args.araport)
    for p in (lit_path, ath_path, araport_path):
        if not p.is_file():
            print(f"Error: file not found: {p}")
            sys.exit(1)

    ath_id_to_syms, sym_to_tcs, sym_to_ids = load_ath_table(ath_path)
    araport = load_araport(araport_path)
    rows = load_literature_map(lit_path)
    lit_symbols = {(r.get('symbol') or r.get('ath_symbol') or '').strip() for r in rows
                   if (r.get('symbol') or r.get('ath_symbol') or '').strip()}
    print(f"[input] literature map rows {len(rows)} | unique symbols {len(lit_symbols)}")
    print(f"[input] ath table Symbols {len(sym_to_ids)} / tc classes {len({t for v in sym_to_tcs.values() for t in v})} | "
          f"Araport gene_symbols {len(araport)}")

    mapping = build_symbol_mapping(lit_symbols, set(sym_to_ids), araport, ath_id_to_syms)
    non_transporter, no_annotation = classify_unmapped(lit_symbols, mapping, araport)

    rows_mapped = sum(1 for r in rows
                      if (r.get('symbol') or r.get('ath_symbol') or '').strip() in mapping)
    print(f"[map] mappable symbols {len(mapping)} (L1 direct "
          f"{sum(1 for v in mapping.values() if v['level'] == 'L1')} / L2 Araport bridged "
          f"{sum(1 for v in mapping.values() if v['level'] == 'L2')}, multi-target "
          f"{sum(1 for v in mapping.values() if len(v['targets']) > 1)})")
    print(f"[map] row coverage {rows_mapped}/{len(rows)} ({rows_mapped / len(rows):.1%}) | "
          f"unmapped symbols: non-transporter {len(non_transporter)} / no annotation {len(no_annotation)}")

    out_prefix = Path(args.output_prefix)
    if out_prefix.parent != Path('.'):
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_prefix = lit_path.parent / out_prefix.name
    tsv_path = Path(f"{out_prefix}.tsv")
    json_path = Path(f"{out_prefix}.json")

    n_tsv_rows = write_map_tsv(rows, mapping, sym_to_tcs, tsv_path)
    payload = build_json_output(rows, mapping, sym_to_tcs, sym_to_ids, araport,
                                non_transporter, no_annotation, n_tsv_rows,
                                meta={'inputs': {'literature_map': str(lit_path.resolve()),
                                                 'ath_table': str(ath_path.resolve()),
                                                 'araport': str(araport_path.resolve())}})
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    hit = payload['stats']['ath_symbols_hit']
    print("\n" + "=" * 60)
    print("ath_symbol normalization mapping summary")
    print("=" * 60)
    print(f"ath_symbols with literature hits: {hit}/{payload['stats']['ath_symbols_total']}")
    print(f"TSV rows (ath_symbol x literature rows): {n_tsv_rows}")
    top = sorted(payload['ath_symbols'].items(),
                 key=lambda kv: (-kv[1]['article_count'], kv[0]))[:15]
    print(f"\nTop 15 ath_symbol (by unique article count):")
    for sym, idx in top:
        print(f"  {sym:<10} tc={'; '.join(idx['tc']) or '-':<18} "
              f"{idx['article_count']} articles / {idx['row_count']} rows / {len(idx['member_symbols'])} member symbols")
    print(f"\nOutput files:\n  {tsv_path}\n  {json_path}")
    sys.exit(0)


if __name__ == '__main__':
    main()
