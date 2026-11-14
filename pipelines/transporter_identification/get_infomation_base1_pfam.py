#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Evidence integration for transporter prediction (without DeepLoc2).
# Three partially overlapping and complementary homology-based evidence
# sources, two-of-three rule (aligned with
# benchmarks/01_transporter_identification):
#   T - TCDB alignment assigns a TC number (best-hit E-value <= 1e-20)
#   S - DIAMOND BLASTP against a curated seed library matching an
#       annotated transporter (best hit for the A. thaliana library,
#       top-5 rank window for the multi-species library; hit E-value
#       <= 1e-20)
#   P - contains a transporter-characteristic Pfam domain (domain
#       i-Evalue < 1e-5; domain list = transporter_pfam.tsv)
# A protein is called transporter when at least two of the three
# criteria are met. BLAST runtime E-value is 1e-10; the final T/S
# criterion applies the strict 1e-20 filter on top.

import os
import logging
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))

EVALUE_BLAST = 1e-10    # BLAST runtime e-value
EVALUE_STRICT = 1e-20   # final T/S criterion e-value
PFAM_EVALUE = 1e-5      # Pfam domain i-Evalue threshold
SEED_MAX_RANK = 5       # seed-library rank window (multi-species library)


def setup_logging():
    logging.basicConfig(
        format='[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Integrate transporter evidence for a given species (without DeepLoc2)."
    )
    parser.add_argument("spe", help="Species name, e.g. Oryza_sativa")
    parser.add_argument("output_dir", nargs="?", default=".",
                        help="Output directory containing result subdirectories (default: current directory)")
    return parser.parse_args()


def read_seed_tid_info(filepath, layout):
    seed_tid_info_dict = {}
    with open(filepath) as f:
        for line in f:
            if layout == "multi":
                tid = line.split('\t')[1]
                pfam = set(line.split('\t')[3].split(','))
            else:
                tid = line.split('\t')[0]
                pfam = set(line.split('\t')[3].strip('\n').split(','))
            tc = line.split('\t')[2]
            seed_tid_info_dict[tid] = [tc, pfam]
    logging.info(f"Loaded {len(seed_tid_info_dict)} seed_tid info from {filepath}")
    return seed_tid_info_dict


def read_tc_info(filepath, evalue=EVALUE_STRICT):
    tid_tc_info_dict = {}
    seen = set()
    with open(filepath) as f:
        for line in f:
            tid = line.split('\t')[0]
            if tid in seen:
                continue
            seen.add(tid)  # first hit = best hit
            if float(line.split('\t')[10]) > evalue:
                continue
            pident = line.split('\t')[2]
            ev = line.split('\t')[10]
            tid_tc_info_dict[tid] = ['.'.join(line.split('\t')[1].split('|')[-1].split('.')[:3]), pident, ev]
    logging.info(f"Loaded {len(tid_tc_info_dict)} TC info from {filepath}")
    return tid_tc_info_dict


def read_pfam_info(filepath):
    tid_pfam_dict = {}
    tid_pfam_info_dict = {}
    with open(filepath) as f:
        for line in f:
            if not line.startswith('#'):
                pfam_name = line.split()[0]
                pfam_id = line.split()[1].split('.')[0]
                tid = line.split()[3]
                evalue = eval(line.split()[12])
                pfam_des = ' '.join(line.split()[22:]).strip('\n')
                pfam_start = line.split()[19]
                pfam_end = line.split()[20]
                if evalue < PFAM_EVALUE:
                    if tid not in tid_pfam_dict:
                        tid_pfam_dict[tid] = set()
                    tid_pfam_dict[tid].add(pfam_id)
                    if tid not in tid_pfam_info_dict:
                        tid_pfam_info_dict[tid] = []
                    tid_pfam_info_dict[tid].append([pfam_name, pfam_id, evalue, pfam_start, pfam_end, pfam_des])
    logging.info(f"Loaded pfam info from {filepath}")
    return tid_pfam_dict, tid_pfam_info_dict


def read_pfam_transport(filepath):
    """Transporter Pfam-domain list: one Pfam accession per line (first
    column; extra columns ignored)."""
    transport_domains = set()
    with open(filepath) as f:
        for line in f:
            col1 = line.split("\t")[0].strip()
            if col1.startswith("PF"):
                transport_domains.add(col1)
    logging.info(f"Loaded {len(transport_domains)} transporter-characteristic Pfam domains from {filepath}")
    return transport_domains


def read_seed_blast_window(filepath, max_rank):
    """Top-`max_rank` hits per query from a BLAST tabular file
    (unfiltered window; e-value checks happen at criterion time)."""
    hits = {}
    with open(filepath) as f:
        for line in f:
            line_split = line.strip().split('\t')
            if len(line_split) < 2:
                continue
            tid = line_split[0]
            hits.setdefault(tid, [])
            if len(hits[tid]) < max_rank:
                hits[tid].append((line_split[1], float(line_split[10]), line_split[2]))
    return hits


def resolve_seed(tid, ath_window, multi_window, seed_ann, evalue=EVALUE_STRICT):
    """Seed criterion: A. thaliana library best hit first, then the
    multi-species library top-5 window; the hit must match an annotated
    seed and pass the strict e-value. Returns (seed_tid, info) or None."""
    for window in (ath_window, multi_window):
        for subject, ev, _pident in window.get(tid, []):
            base = subject.split('.')[0]
            if base in seed_ann and ev <= evalue:
                return subject, seed_ann[base]
    return None


def build_candidates(tc_info, pfam_dict, pfam_info_dict, transport_domains,
                     ath_window, multi_window, seed_ann):
    candi_tid_dict = {}
    universe = set(tc_info) | set(ath_window) | set(multi_window)
    for tid in universe:
        T = tid in tc_info
        P = bool(pfam_dict.get(tid, set()) & transport_domains)
        seed = resolve_seed(tid, ath_window, multi_window, seed_ann)
        S = seed is not None
        if T + P + S >= 2:
            candi_tid_dict[tid] = {
                'seed_tid': {seed[0]} if S else set(),
                'seed_tc': {seed[1][0]} if S else set(),
                'seed_pfam': set(seed[1][1]) if S else set(),
                'tid_tc': {tc_info[tid][0]} if T else set(),
                'tid_tc_info': tc_info[tid] if T else [],
                'tid_pfam': pfam_dict.get(tid, set()),
                'tid_pfam_info': pfam_info_dict.get(tid, [])
            }
    logging.info(f"Collected {len(candi_tid_dict)} candidate transporter proteins")
    return candi_tid_dict


def fill_tm_data(candi_tid_dict, TM2_file):
    tmhmm2_num = None
    tmhmm2_gff_list = []
    current_tid = None
    with open(TM2_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                if "Number of predicted TMHs:" in line or "Length:" in line:
                    if current_tid and current_tid in candi_tid_dict:
                        candi_tid_dict[current_tid]['TMHMM2_num'] = tmhmm2_num
                        candi_tid_dict[current_tid]['TMHMM2_gff'] = ";".join(tmhmm2_gff_list)
                    tmhmm2_gff_list = []
                    tmhmm2_num = None
                    tid = line.split()[1]
                    current_tid = tid
                    if "Number of predicted TMHs:" in line:
                        try:
                            tmhmm2_num = int(line.split("predicted TMHs:")[1].strip())
                        except:
                            tmhmm2_num = None
            else:
                if current_tid:
                    tmhmm2_gff_list.append(line)
        if current_tid and current_tid in candi_tid_dict:
            candi_tid_dict[current_tid]['TMHMM2_num'] = tmhmm2_num
            candi_tid_dict[current_tid]['TMHMM2_gff'] = ";".join(tmhmm2_gff_list)
    logging.info("Filled TMHMM2 data.")


def init_deeploc2_fields(candi_tid_dict):
    for tid in candi_tid_dict:
        candi_tid_dict[tid]['DeepLoc2_Localizations'] = ''
        candi_tid_dict[tid]['DeepLoc2_Signals'] = ''
        candi_tid_dict[tid]['DeepLoc2_memout'] = ''
    logging.info("Initialized DeepLoc2 fields with empty values.")


def check_keys(candi_tid_dict, required_keys, isNull_keys):
    all_has_required = all(
        all(k in info for k in required_keys) for info in candi_tid_dict.values())
    if all_has_required:
        logging.info("All entries have required_keys.")
    else:
        logging.error("Some entries miss required keys.")
    all_not_null = True
    for tid, info in candi_tid_dict.items():
        for k in isNull_keys:
            v = info.get(k, None)
            if v is None or v == '' or v == [] or v == {} or v == set():
                logging.warning(f"{tid} key empty: {k}")
                all_not_null = False
    if all_not_null:
        logging.info("All entries' isNull_keys fields are non-empty.")


def write_output_files(candi_tid_dict, spe, out_file, out_pfam_file):
    with open(out_file, 'w') as f:
        f.write('\t'.join([
            'speID', 'pid', 'tc', 'tc_pident', 'tc_evalue', 'tid_pfam', 'seed_tid', 'seed_tc', 'seed_pfam',
            'TMHMM2_num', 'TMHMM2_gff'
        ]) + '\n')
        for k, v in candi_tid_dict.items():
            tc = ';'.join(list(v['tid_tc']))
            if len(v['tid_tc_info']) == 3:
                tc_pident, tc_evalue = v['tid_tc_info'][1], v['tid_tc_info'][2]
            else:
                tc_pident, tc_evalue = '', ''
            TMHMM2_gff = ";".join(
                ["|".join(field.strip() for subfield in entry.split('\t') for field in subfield.split())
                 for entry in v.get('TMHMM2_gff', '').split(';')])
            line = [spe, k, tc, tc_pident, tc_evalue,
                    ';'.join(list(v['tid_pfam'])), ';'.join(list(v['seed_tid'])),
                    ';'.join(list(v['seed_tc'])), ';'.join(list(v['seed_pfam'])),
                    str(v.get('TMHMM2_num')), TMHMM2_gff]
            f.write('\t'.join(map(str, line)) + '\n')
    logging.info(f"Wrote {len(candi_tid_dict)} entries to {out_file}.")
    with open(out_pfam_file, 'w') as f:
        f.write('\t'.join(['speID', 'pid', 'pfamName', 'pfamID', 'evalue', 'pstart', 'pend', 'pfamDes']) + '\n')
        cnt = 0
        for k, v in candi_tid_dict.items():
            for pfam_info in v['tid_pfam_info']:
                f.write('\t'.join([spe, k, pfam_info[0], pfam_info[1], str(pfam_info[2]),
                                   pfam_info[3], pfam_info[4], pfam_info[5]]) + '\n')
                cnt += 1
    logging.info(f"Wrote {cnt} pfam entries to {out_pfam_file}.")


def main():
    setup_logging()
    args = parse_args()
    spe = args.spe
    output_dir = args.output_dir
    logging.info(f"Start processing for species: {spe}")
    logging.info(f"Using output directory: {output_dir}")

    # file paths
    seed_info_multi = os.environ.get('PTD_SEED_INFO_ARA', os.path.join(_HERE, 'candi_seed_info_v1_ara.txt'))
    seed_info_ath = os.environ.get('PTD_SEED_INFO_ATH', os.path.join(_HERE, 'candi_seed_info_v2_ath.txt'))
    pfam_transport = os.environ.get('PTD_PFAM_TRANSPORT', os.path.join(_HERE, 'transporter_pfam.tsv'))
    tc_file = os.path.join(output_dir, f'TC_res/{spe}_diamond_TC.txt')
    multi_file = os.path.join(output_dir, f'Seed_res/{spe}_diamond_Seed_9spe.txt')
    ath_file = os.path.join(output_dir, f'Seed_ath_res/{spe}_diamond_ath11.txt')
    pfam_file = os.path.join(output_dir, f'Pfam_res/{spe}.domtblout')
    TM2_file = os.path.join(output_dir, f'TMHMM2/{spe}.tmhmm.txt')

    # main logic (three partially overlapping, complementary evidence sources, two-of-three)
    seed_ann = {}
    if os.path.isfile(seed_info_multi):
        seed_ann.update(read_seed_tid_info(seed_info_multi, 'multi'))
    if os.path.isfile(seed_info_ath):
        seed_ann.update(read_seed_tid_info(seed_info_ath, 'ath'))
    tc_info = read_tc_info(tc_file)
    pfam_dict, pfam_info_dict = read_pfam_info(pfam_file)
    transport_domains = read_pfam_transport(pfam_transport)
    ath_window = read_seed_blast_window(ath_file, max_rank=1)
    multi_window = read_seed_blast_window(multi_file, max_rank=SEED_MAX_RANK)
    candi_tid_dict = build_candidates(tc_info, pfam_dict, pfam_info_dict,
                                      transport_domains, ath_window, multi_window, seed_ann)
    fill_tm_data(candi_tid_dict, TM2_file)
    init_deeploc2_fields(candi_tid_dict)

    required_keys = [
        'seed_tid', 'seed_tc', 'seed_pfam', 'tid_tc', 'tid_tc_info',
        'tid_pfam', 'tid_pfam_info', 'TMHMM2_num', 'TMHMM2_gff',
        'DeepLoc2_Localizations', 'DeepLoc2_Signals', 'DeepLoc2_memout'
    ]
    isNull_keys = ['TMHMM2_num', 'TMHMM2_gff']
    check_keys(candi_tid_dict, required_keys, isNull_keys)

    write_output_files(candi_tid_dict, spe, f"{spe}_information_base1.txt", f"{spe}_pfam_info.txt")
    logging.info("All done.")


if __name__ == "__main__":
    main()
