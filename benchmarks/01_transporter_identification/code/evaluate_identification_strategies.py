#!/usr/bin/env python3
"""
evaluate_identification_strategies.py — Evaluate transporter prediction
strategies with a standalone Pfam-transport-domain criterion (P).

P criterion (--pfam_transport FILE, required): P = (query Pfam domains ∩
transporter-domain file) ≠ ∅. Evaluated on its own — no T gating, no
TC-family lookup. FILE has one Pfam accession per line (first column;
extra columns such as TCDB support counts are ignored).

Three criteria:
  T - TC database match (Diamond BLAST vs TCDB, best hit e-value ≤ strict threshold)
  P - Pfam transporter domain (hmmscan vs Pfam-A, i-Evalue < --evalue_pfam)
  S - Seed homology (Diamond BLAST vs seed DB, with rank constraints)

Strategies:
  1 — any 1 of 3 (most sensitive)
  2 — at least 2 of 3 (current pipeline)
  3 — all 3 of 3 (most specific)

Strict e-value thresholds for T/S are configurable per strategy (see --evalue_strict*).

Usage example (this release; see run_benchmark.sh for the full reproduction):
  python3 code/evaluate_identification_strategies.py \
      --positive_fa data/benchmark_set/benchmark_positive.fasta \
      --negative_fa data/benchmark_set/benchmark_negative.fasta --strategy all \
      --db_tc data/reference_db/tcdb.dmnd \
      --db_seed data/reference_db/9_all_spe_add_16osa_v1_dedup.dmnd \
      --db_seed_ath data/reference_db/ath_dedup.dmnd \
      --db_pfam data/reference_db/Pfam-A.hmm \
      --pfam_transport data/reference_db/transporter_pfam.tsv \
      --seed_info data/seed_info/candi_seed_info_v1_ara.txt \
                 data/seed_info/candi_seed_info_v2_ath.txt \
      --evalue_strict 1e-20 --evalue_pfam 1e-5 \
      --include_neg_family --output_dir ./eval_output --threads 10
"""

import argparse
import csv
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# Default unified e-value threshold (used when no per-strategy override is given)
DEFAULT_EVALUE_STRICT = 1e-20

# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Evaluate transporter prediction strategies with customizable e-value thresholds"
    )
    p.add_argument("--positive_fa", required=True, help="Positive-set FASTA (known transporters)")
    p.add_argument("--negative_fa", required=True, help="Negative-set FASTA (non-transporters)")
    p.add_argument("--strategy", required=True, choices=["1", "2", "3", "all"],
                    help="Strategy: 1 (any), 2 (≥2), 3 (all 3), or all")
    p.add_argument("--db_tc", required=True, help="TCDB Diamond database path")
    p.add_argument("--db_seed", nargs="+", default=[],
                    help="Seed transporter Diamond database path(s) for non-Arabidopsis species. Top-5 rank required.")
    p.add_argument("--db_seed_ath", nargs="+", default=[],
                    help="Arabidopsis seed Diamond database path(s). Best hit (rank 1) required.")
    p.add_argument("--db_pfam", required=True, help="Pfam-A.hmm path")
    p.add_argument("--pfam_transport", required=True,
                   help="Transporter Pfam-domain file for criterion P: one Pfam accession "
                        "per line (first column; extra columns ignored). "
                        "P = (query domains ∩ file) ≠ ∅, evaluated without reference to T and S.")
    p.add_argument("--seed_info", required=True, action="append", nargs="+",
                    help="Candidate seed info TSV file(s). Can repeat --seed_info, "
                         "or give multiple files per flag: --seed_info a.txt b.txt")
    p.add_argument("--output_dir", default="./eval_output_custom", help="Output directory")
    p.add_argument("--prefix", default="eval", help="Output file prefix")
    p.add_argument("--threads", type=int, default=10, help="Threads for BLAST/hmmscan")
    p.add_argument("--evalue_blast", type=float, default=1e-10,
                    help="E-value threshold for BLAST runtime (kept loose; strict filtering happens in build_evidence)")
    p.add_argument("--evalue_pfam", type=float, default=1e-5,
                   help="Domain i-Evalue threshold for Pfam (1e-5: the calibration "
                        "threshold of the shipped transporter_pfam.tsv)")
    # Customizable strict thresholds
    p.add_argument("--evalue_strict", type=float, default=DEFAULT_EVALUE_STRICT,
                    help=f"Unified strict e-value threshold for TC and Seed criteria in ALL strategies "
                         f"(default: {DEFAULT_EVALUE_STRICT:.0e}). Use 1e-10 to effectively disable strict filtering.")
    p.add_argument("--evalue_strict_k1", type=float, default=None,
                    help="Override strict e-value for k=1 strategy (default: uses --evalue_strict)")
    p.add_argument("--evalue_strict_k2", type=float, default=None,
                    help="Override strict e-value for k=2 strategy (default: uses --evalue_strict)")
    p.add_argument("--evalue_strict_k3", type=float, default=None,
                    help="Override strict e-value for k=3 strategy (default: uses --evalue_strict)")
    p.add_argument("--skip_run", action="store_true",
                    help="Skip running tools; reuse existing result files in output_dir")
    p.add_argument("--include_neg_family", action="store_true",
                    help="Also output eval_family_metrics_with_neg.tsv including negative-set "
                         "proteins' TC families (with FP/TN/Specificity columns). Default eval_family_metrics.tsv "
                         "(pos-only) is always written.")
    args = p.parse_args()
    # Flatten nested list from action="append" + nargs="+"
    if args.seed_info and isinstance(args.seed_info[0], list):
        args.seed_info = [item for sublist in args.seed_info for item in sublist]
    return args


def resolve_thresholds(args):
    """Resolve per-strategy e-value thresholds from CLI args."""
    return {
        1: args.evalue_strict_k1 if args.evalue_strict_k1 is not None else args.evalue_strict,
        2: args.evalue_strict_k2 if args.evalue_strict_k2 is not None else args.evalue_strict,
        3: args.evalue_strict_k3 if args.evalue_strict_k3 is not None else args.evalue_strict,
    }

# ── Run external tools ────────────────────────────────────────────────────────

def run_diamond(query_fa, db, output, threads, evalue=1e-10, max_target_seqs=5):
    cmd = [
        "diamond", "blastp",
        "-d", db,
        "-q", query_fa,
        "-o", output,
        "--evalue", str(evalue),
        "--outfmt", "6",
        "--threads", str(threads),
        "--max-target-seqs", str(max_target_seqs),
        "--more-sensitive",
    ]
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def run_hmmscan(query_fa, pfam_db, output, threads):
    cmd = [
        "hmmscan",
        "--cpu", str(threads),
        "--domtblout", output,
        pfam_db,
        query_fa,
    ]
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


# ── Parse results ─────────────────────────────────────────────────────────────

def parse_diamond(filepath):
    """Parse Diamond outfmt 6. Returns dict: query -> [(subject, pident, evalue, bitscore)]"""
    hits = defaultdict(list)
    with open(filepath) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 12:
                continue
            query = parts[0]
            subject = parts[1]
            pident = float(parts[2])
            evalue = float(parts[10])
            bitscore = float(parts[11])
            hits[query].append((subject, pident, evalue, bitscore))
    return dict(hits)


def parse_domtblout(filepath, evalue_threshold=1e-5):
    """Parse HMMER domtblout. Returns dict: query -> set of pfam_id"""
    domains = defaultdict(set)
    domain_info = defaultdict(list)
    with open(filepath) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 22:
                continue
            target_acc = parts[1]
            query_name = parts[3]
            evalue_dom = float(parts[12])

            if target_acc == "-" or target_acc == ".":
                continue
            pfam_base = target_acc.split(".")[0]

            if evalue_dom < evalue_threshold:
                domains[query_name].add(pfam_base)
                domain_info[query_name].append((pfam_base, parts[0], evalue_dom, int(parts[17]), int(parts[18])))
    return dict(domains), dict(domain_info)


def parse_seed_info(filepath):
    """Parse seed info TSV. Returns:
       seed_dict: seed_tid -> {tc, pfam_set}
       transporter_pfam_set: set of all Pfam IDs found in seeds
       valid_seed_ids: set of all valid seed protein IDs (for BLAST subject matching)
    """
    seed_dict = {}
    transporter_pfam_set = set()
    valid_seed_ids = set()

    with open(filepath) as f:
        lines = f.readlines()

    if not lines:
        return seed_dict, transporter_pfam_set, valid_seed_ids

    first_parts = lines[0].strip().split("\t")
    pfam_col = None
    for i, v in enumerate(first_parts):
        token = v.strip().split(",")[0].strip()
        if token.startswith("PF") and len(token) >= 7:
            pfam_col = i
            break

    if pfam_col is None:
        print(f"[WARN] Cannot detect Pfam column in {filepath}, skipping")
        return seed_dict, transporter_pfam_set, valid_seed_ids

    tc_col = pfam_col - 1
    id_start = 1 if first_parts[0].strip().isdigit() else 0

    print(f"[DIAG] {os.path.basename(filepath)}: detected pfam_col={pfam_col}, "
          f"tc_col={tc_col}, id_start={id_start}, "
          f"first_line_pfan={first_parts[pfam_col]}, tc={first_parts[tc_col]}")

    for line in lines:
        parts = line.strip().split("\t")
        if len(parts) <= pfam_col:
            continue

        tc = parts[tc_col].strip()
        pfam_str = parts[pfam_col].strip()

        for i in range(id_start, tc_col):
            pid = parts[i].strip()
            if pid:
                valid_seed_ids.add(pid)

        if tc_col > id_start:
            primary_id = parts[tc_col - 1].strip()
        else:
            primary_id = parts[id_start].strip()

        pfam_set = set()
        if pfam_str:
            for p in pfam_str.split(","):
                p = p.strip()
                if p:
                    pfam_set.add(p)
                    transporter_pfam_set.add(p)

        if primary_id:
            seed_dict[primary_id] = {"tc": tc, "pfam_set": pfam_set}

    return seed_dict, transporter_pfam_set, valid_seed_ids


def parse_pfam_transport(filepath):
    """Parse the transporter Pfam-domain file. One Pfam accession per line
    (first column; extra columns such as TCDB support counts are ignored)."""
    transport_domains = set()
    with open(filepath) as f:
        for line in f:
            col1 = line.split("\t")[0].strip()
            if col1.startswith("PF"):
                transport_domains.add(col1)
    return transport_domains

# ── Evidence assembly ─────────────────────────────────────────────────────────

def build_evidence(protein_ids, tc_hits, seed_hits, ath_seed_hits,
                   pfam_domains, pfam_domain_info, pfam_transport, valid_seed_ids,
                   evalue_strict=None):
    """For each protein, determine which of the 3 criteria are met.

    evalue_strict: if set (e.g. 1e-20), TC and Seed only pass when their best hit
      e-value <= this threshold. If None, no strict filtering (uses BLAST runtime
      threshold which is already applied to the input data).

    Pfam criterion is standalone: domain set ∩ pfam_transport ≠ ∅.
    Seed criterion has rank constraints:
      - Arabidopsis (ath) DB: only the best hit (rank 1) counts
      - Other species DB: only top 5 hits count
    """
    evidence = {}
    for pid in protein_ids:
        ev = {
            "TC": False, "Pfam": False, "Seed": False,
            "tc_subject": "", "tc_evalue": "", "tc_pident": "",
            "pfam_domains": [], "seed_subject": "", "seed_evalue": "", "seed_pident": "",
            "tc_family": "",
        }

        # Criterion 1: TC match
        if pid in tc_hits:
            best = tc_hits[pid][0]
            ev["tc_subject"] = best[0]
            ev["tc_pident"] = best[1]
            ev["tc_evalue"] = best[2]
            tc_raw = best[0]
            tc_parts = tc_raw.split("|")
            if tc_parts:
                tc_code_str = tc_parts[-1]
                ev["tc_family"] = ".".join(tc_code_str.split(".")[:3])
            tc_eval_ok = evalue_strict is None or best[2] <= evalue_strict
            if tc_eval_ok:
                ev["TC"] = True

        # Criterion 2: Pfam transporter domain (no T / TC-family gating)
        matched = pfam_domains.get(pid, set()) & pfam_transport
        if matched:
            ev["Pfam"] = True
            ev["pfam_domains"] = sorted(matched)

        # Criterion 3: Seed homology with rank constraints
        # Arabidopsis DB: rank 1 only
        if pid in ath_seed_hits:
            best = ath_seed_hits[pid][0]
            if best[0] in valid_seed_ids:
                seed_eval_ok = evalue_strict is None or best[2] <= evalue_strict
                if seed_eval_ok:
                    ev["Seed"] = True
                    ev["seed_subject"] = best[0]
                    ev["seed_pident"] = best[1]
                    ev["seed_evalue"] = best[2]

        # Other species DB: top 5 hits
        if not ev["Seed"] and pid in seed_hits:
            for hit in seed_hits[pid][:5]:
                subject = hit[0]
                if subject in valid_seed_ids:
                    seed_eval_ok = evalue_strict is None or hit[2] <= evalue_strict
                    if seed_eval_ok:
                        ev["Seed"] = True
                        ev["seed_subject"] = subject
                        ev["seed_pident"] = hit[1]
                        ev["seed_evalue"] = hit[2]
                        break

        evidence[pid] = ev
    return evidence


# ── Strategy application ──────────────────────────────────────────────────────

def apply_strategy(evidence, k, positive_ids, negative_ids):
    """Apply strategy k (1/2/3). Returns list of result dicts."""
    results = []
    all_ids = positive_ids | negative_ids

    for pid in all_ids:
        ev = evidence.get(pid, {
            "TC": False, "Pfam": False, "Seed": False,
            "tc_subject": "", "tc_evalue": "", "tc_pident": "",
            "pfam_domains": [], "seed_subject": "", "seed_evalue": "", "seed_pident": "",
            "tc_family": "",
        })

        n_met = sum([ev["TC"], ev["Pfam"], ev["Seed"]])
        passed = n_met >= k

        code = ("T" if ev["TC"] else "-") + ("P" if ev["Pfam"] else "-") + ("S" if ev["Seed"] else "-")

        if n_met == 3:
            confidence = "High"
        elif n_met == 2:
            confidence = "Medium"
        elif n_met == 1:
            confidence = "Low"
        else:
            confidence = "None"

        label = "pos" if pid in positive_ids else "neg"
        pred = "pred_tp" if passed else "not_tp"

        results.append({
            "protein_id": pid,
            "label": label,
            "pred": pred,
            "strategy": k,
            "evidence_code": code,
            "confidence": confidence,
            "TC_hit": ev["TC"],
            "Pfam_hit": ev["Pfam"],
            "Seed_hit": ev["Seed"],
            "tc_subject": ev["tc_subject"],
            "tc_evalue": ev["tc_evalue"],
            "tc_pident": ev["tc_pident"],
            "tc_family": ev["tc_family"],
            "pfam_domains": ",".join(ev["pfam_domains"]) if ev["pfam_domains"] else "",
            "seed_subject": ev["seed_subject"],
            "seed_evalue": ev["seed_evalue"],
            "seed_pident": ev["seed_pident"],
        })
    return results


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_classic_metrics(results):
    tp = sum(1 for r in results if r["label"] == "pos" and r["pred"] == "pred_tp")
    fp = sum(1 for r in results if r["label"] == "neg" and r["pred"] == "pred_tp")
    tn = sum(1 for r in results if r["label"] == "neg" and r["pred"] == "not_tp")
    fn = sum(1 for r in results if r["label"] == "pos" and r["pred"] == "not_tp")

    sensitivity = tp / (tp + fn) if (tp + fn) > 0 else 0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    f1 = 2 * precision * sensitivity / (precision + sensitivity) if (precision + sensitivity) > 0 else 0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else 0

    return {
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "Sensitivity": round(sensitivity, 4),
        "Specificity": round(specificity, 4),
        "Precision": round(precision, 4),
        "F1": round(f1, 4),
        "Accuracy": round(accuracy, 4),
    }


def compute_family_metrics(results, include_neg=False):
    """Per-TC-family metrics.

    By default (include_neg=False): only positive-set proteins are counted.
      - For each TC family, TP = pos proteins predicted as transporter,
        FN = pos proteins missed.
      - Sensitivity = TP / (TP + FN).

    When include_neg=True: also counts negative-set proteins (using each neg
    protein's TC BLAST best-hit family, or 'Unknown' if no TC hit). Adds FP, TN,
    Specificity columns.
      - FP = neg proteins predicted as transporter in this family,
        TN = neg proteins correctly rejected in this family.
      - Specificity = TN / (TN + FP).
    """
    families = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
    for r in results:
        is_pos = r["label"] == "pos"
        if not include_neg and not is_pos:
            continue
        fam = r["tc_family"] if r["tc_family"] else "Unknown"
        pred_tp = r["pred"] == "pred_tp"
        if is_pos:
            if pred_tp:
                families[fam]["tp"] += 1
            else:
                families[fam]["fn"] += 1
        else:
            if pred_tp:
                families[fam]["fp"] += 1
            else:
                families[fam]["tn"] += 1

    rows = []
    for fam, counts in sorted(families.items(), key=lambda x: -(x[1]["tp"] + x[1]["fn"] + x[1]["fp"] + x[1]["tn"])):
        if include_neg:
            total = counts["tp"] + counts["fn"] + counts["fp"] + counts["tn"]
            sn = counts["tp"] / (counts["tp"] + counts["fn"]) if (counts["tp"] + counts["fn"]) > 0 else 0
            sp = counts["tn"] / (counts["tn"] + counts["fp"]) if (counts["tn"] + counts["fp"]) > 0 else 0
            rows.append({
                "TC_family": fam,
                "total": total,
                "TP": counts["tp"],
                "FN": counts["fn"],
                "FP": counts["fp"],
                "TN": counts["tn"],
                "Sensitivity": round(sn, 4),
                "Specificity": round(sp, 4),
            })
        else:
            total = counts["tp"] + counts["fn"]
            sn = counts["tp"] / total if total > 0 else 0
            rows.append({
                "TC_family": fam,
                "total": total,
                "TP": counts["tp"],
                "FN": counts["fn"],
                "Sensitivity": round(sn, 4),
            })
    return rows


def compute_evidence_distribution(results):
    dist = defaultdict(lambda: {"pos_tp": 0, "pos_fn": 0, "neg_fp": 0, "neg_tn": 0})
    for r in results:
        code = r["evidence_code"]
        if r["label"] == "pos":
            if r["pred"] == "pred_tp":
                dist[code]["pos_tp"] += 1
            else:
                dist[code]["pos_fn"] += 1
        else:
            if r["pred"] == "pred_tp":
                dist[code]["neg_fp"] += 1
            else:
                dist[code]["neg_tn"] += 1
    return dict(dist)


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_pr_roc(all_metrics, output_dir, prefix, thr_summary=""):
    if not HAS_MPL:
        print("[WARN] matplotlib not available, skipping plots")
        return

    subtitle = f" ({thr_summary})" if thr_summary else ""

    strategies = sorted(all_metrics.keys())
    precision_pts = []
    recall_pts = []
    fpr_pts = []
    tpr_pts = []
    labels = []

    for k in strategies:
        m = all_metrics[k]
        prec = m["Precision"]
        rec = m["Sensitivity"]
        fpr = 1 - m["Specificity"] if m["Specificity"] is not None else 0
        tpr = rec
        precision_pts.append(prec)
        recall_pts.append(rec)
        fpr_pts.append(fpr)
        tpr_pts.append(tpr)
        labels.append(f"k={k}")

    # PR curve
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(recall_pts, precision_pts, "bo-", markersize=10)
    for i, lbl in enumerate(labels):
        ax.annotate(lbl, (recall_pts[i], precision_pts[i]),
                    textcoords="offset points", xytext=(8, 4), fontsize=10)
    ax.set_xlabel("Recall (Sensitivity)", fontsize=12)
    ax.set_ylabel("Precision", fontsize=12)
    ax.set_title(f"PR: Transporter Prediction{subtitle}", fontsize=13)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, f"{prefix}_pr_curve.png"), dpi=150)
    plt.close(fig)

    # ROC curve
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(fpr_pts, tpr_pts, "ro-", markersize=10)
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3)
    for i, lbl in enumerate(labels):
        ax.annotate(lbl, (fpr_pts[i], tpr_pts[i]),
                    textcoords="offset points", xytext=(8, 4), fontsize=10)
    ax.set_xlabel("False Positive Rate (1 - Specificity)", fontsize=12)
    ax.set_ylabel("True Positive Rate (Sensitivity)", fontsize=12)
    ax.set_title(f"ROC: Transporter Prediction{subtitle}", fontsize=13)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, f"{prefix}_roc_curve.png"), dpi=150)
    plt.close(fig)

    print(f"[PLOT] Saved PR and ROC curves to {output_dir}")


# ── Output writers ────────────────────────────────────────────────────────────

PREDICTION_COLUMNS = [
    "protein_id", "label", "pred", "strategy", "evidence_code", "confidence",
    "TC_hit", "Pfam_hit", "Seed_hit",
    "tc_subject", "tc_evalue", "tc_pident", "tc_family",
    "pfam_domains", "seed_subject", "seed_evalue", "seed_pident",
]


def write_predictions(results, filepath):
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PREDICTION_COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(results)
    print(f"[OUT] Predictions: {filepath}")


def write_metrics_summary(all_metrics, filepath):
    with open(filepath, "w", newline="") as f:
        columns = ["Strategy", "TP", "FP", "TN", "FN",
                    "Sensitivity", "Specificity", "Precision", "F1", "Accuracy"]
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        for k in sorted(all_metrics.keys()):
            row = {"Strategy": f"k={k}"}
            row.update(all_metrics[k])
            w.writerow(row)
    print(f"[OUT] Metrics summary: {filepath}")


def write_family_metrics(all_family, filepath):
    has_data = False
    with open(filepath, "w", newline="") as f:
        columns = ["Strategy", "TC_family", "total", "TP", "FN", "Sensitivity"]
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        for k in sorted(all_family.keys()):
            for row in all_family[k]:
                w.writerow({"Strategy": f"k={k}", **row})
                has_data = True
    if has_data:
        print(f"[OUT] Family metrics: {filepath}")
    else:
        print(f"[OUT] Family metrics (empty): {filepath}")


def write_family_metrics_with_neg(all_family, filepath):
    has_data = False
    with open(filepath, "w", newline="") as f:
        columns = ["Strategy", "TC_family", "total",
                   "TP", "FN", "FP", "TN", "Sensitivity", "Specificity"]
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        for k in sorted(all_family.keys()):
            for row in all_family[k]:
                w.writerow({"Strategy": f"k={k}", **row})
                has_data = True
    if has_data:
        print(f"[OUT] Family metrics (with neg): {filepath}")
    else:
        print(f"[OUT] Family metrics (with neg, empty): {filepath}")


def write_evidence_distribution(all_dist, filepath):
    with open(filepath, "w", newline="") as f:
        columns = ["Strategy", "evidence_code", "pos_TP", "pos_FN", "neg_FP", "neg_TN"]
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        for k in sorted(all_dist.keys()):
            for code, counts in sorted(all_dist[k].items()):
                w.writerow({
                    "Strategy": f"k={k}",
                    "evidence_code": code,
                    "pos_TP": counts["pos_tp"],
                    "pos_FN": counts["pos_fn"],
                    "neg_FP": counts["neg_fp"],
                    "neg_TN": counts["neg_tn"],
                })
    print(f"[OUT] Evidence distribution: {filepath}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    strategies = [1, 2, 3] if args.strategy == "all" else [int(args.strategy)]

    if not args.db_seed and not args.db_seed_ath:
        print("[ERROR] At least one of --db_seed or --db_seed_ath must be specified", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Resolve per-strategy thresholds
    thr = resolve_thresholds(args)
    print(f"[INFO] Per-strategy strict e-value thresholds:")
    for k in (1, 2, 3):
        override_flag = " (override)" if getattr(args, f"evalue_strict_k{k}") is not None else ""
        print(f"[INFO]   k={k}: {thr[k]:.0e}{override_flag}")

    # 1. Collect protein IDs
    positive_ids = set()
    negative_ids = set()

    def read_fasta_ids(filepath, id_set):
        with open(filepath) as f:
            for line in f:
                if line.startswith(">"):
                    pid = line[1:].split()[0].strip()
                    id_set.add(pid)

    print(f"[INFO] Reading protein IDs...")
    read_fasta_ids(args.positive_fa, positive_ids)
    read_fasta_ids(args.negative_fa, negative_ids)
    print(f"[INFO] Positive set: {len(positive_ids)} proteins")
    print(f"[INFO] Negative set: {len(negative_ids)} proteins")

    all_ids = positive_ids | negative_ids

    # 2. Merge FASTA files
    merged_fa = os.path.join(args.output_dir, "_merged_input.fa")
    if not args.skip_run or not os.path.exists(merged_fa):
        with open(merged_fa, "w") as out:
            for fa_path in [args.positive_fa, args.negative_fa]:
                with open(fa_path) as inp:
                    out.write(inp.read())
    print(f"[INFO] Merged FASTA: {merged_fa}")

    # 3. Run tools (or reuse existing results)
    tc_out = os.path.join(args.output_dir, "tc_blast.tsv")
    pfam_out = os.path.join(args.output_dir, "pfam.domtblout")

    seed_outs = []
    for db_path in args.db_seed:
        db_name = Path(db_path).stem
        seed_outs.append(os.path.join(args.output_dir, f"seed_blast_{db_name}.tsv"))

    ath_seed_outs = []
    for db_path in args.db_seed_ath:
        db_name = Path(db_path).stem
        ath_seed_outs.append(os.path.join(args.output_dir, f"ath_seed_blast_{db_name}.tsv"))

    if not args.skip_run:
        run_diamond(merged_fa, args.db_tc, tc_out, args.threads, args.evalue_blast, max_target_seqs=5)
        for db_path, out_path in zip(args.db_seed, seed_outs):
            run_diamond(merged_fa, db_path, out_path, args.threads, args.evalue_blast, max_target_seqs=20)
        for db_path, out_path in zip(args.db_seed_ath, ath_seed_outs):
            run_diamond(merged_fa, db_path, out_path, args.threads, args.evalue_blast, max_target_seqs=5)
        run_hmmscan(merged_fa, args.db_pfam, pfam_out, args.threads)
    else:
        print(f"[SKIP] Reusing existing results in {args.output_dir}")

    # 4. Parse results
    print(f"[INFO] Parsing results...")
    tc_hits = parse_diamond(tc_out)
    print(f"[DIAG] TC BLAST: {len(tc_hits)} proteins have hits")

    seed_hits = {}
    for out_path in seed_outs:
        hits_i = parse_diamond(out_path)
        print(f"[DIAG] Seed BLAST ({Path(out_path).name}): {len(hits_i)} proteins have hits")
        for query, hit_list in hits_i.items():
            if query not in seed_hits:
                seed_hits[query] = hit_list
            else:
                combined = seed_hits[query] + hit_list
                combined.sort(key=lambda x: -x[3])
                seed_hits[query] = combined
    print(f"[DIAG] Seed BLAST merged (other species, top-5 rank): {len(seed_hits)} proteins have hits total")

    ath_seed_hits = {}
    for out_path in ath_seed_outs:
        hits_i = parse_diamond(out_path)
        print(f"[DIAG] Arabidopsis seed BLAST ({Path(out_path).name}): {len(hits_i)} proteins have hits")
        for query, hit_list in hits_i.items():
            if query not in ath_seed_hits:
                ath_seed_hits[query] = hit_list
            else:
                combined = ath_seed_hits[query] + hit_list
                combined.sort(key=lambda x: -x[3])
                ath_seed_hits[query] = combined
    print(f"[DIAG] Arabidopsis seed BLAST merged (rank 1 only): {len(ath_seed_hits)} proteins have hits total")

    pfam_domains, pfam_domain_info = parse_domtblout(pfam_out, args.evalue_pfam)
    print(f"[DIAG] Pfam domains: {len(pfam_domains)} proteins have domain hits")

    # 5. Load TC family → Pfam mapping + valid seed IDs
    # Pfam criterion: transporter-domain file (standalone check)
    pfam_transport = parse_pfam_transport(args.pfam_transport)
    if not pfam_transport:
        sys.exit(f"[ERROR] No Pfam accessions parsed from: {args.pfam_transport}")
    print(f"[INFO] Pfam transport-domain file: '{args.pfam_transport}' "
          f"with {len(pfam_transport)} domains")

    all_seed_dict = {}
    valid_seed_ids = set()
    for info_path in args.seed_info:
        sd, _, vid = parse_seed_info(info_path)
        all_seed_dict.update(sd)
        valid_seed_ids.update(vid)
    print(f"[INFO] Seed transporters: {len(all_seed_dict)} (from {len(args.seed_info)} files)")
    print(f"[INFO] Valid seed protein IDs: {len(valid_seed_ids)}")

    # 6. Build one evidence dict per UNIQUE threshold value (efficiency: same
    #    threshold = same evidence, so build once and reuse across strategies)
    unique_thrs = sorted(set(thr[k] for k in strategies))
    evidence_by_thr = {}
    for t in unique_thrs:
        print(f"\n[INFO] Building evidence with e-value ≤{t:.0e} ...")
        evidence_by_thr[t] = build_evidence(
            all_ids, tc_hits, seed_hits, ath_seed_hits, pfam_domains, pfam_domain_info,
            pfam_transport, valid_seed_ids, evalue_strict=t
        )

    # Diagnostic: per-criterion counts for each unique threshold
    for t in unique_thrs:
        ev_dict = evidence_by_thr[t]
        n_tc_pos = sum(1 for pid in positive_ids if ev_dict.get(pid, {}).get("TC", False))
        n_pfam_pos = sum(1 for pid in positive_ids if ev_dict.get(pid, {}).get("Pfam", False))
        n_seed_pos = sum(1 for pid in positive_ids if ev_dict.get(pid, {}).get("Seed", False))
        n_tc_neg = sum(1 for pid in negative_ids if ev_dict.get(pid, {}).get("TC", False))
        n_pfam_neg = sum(1 for pid in negative_ids if ev_dict.get(pid, {}).get("Pfam", False))
        n_seed_neg = sum(1 for pid in negative_ids if ev_dict.get(pid, {}).get("Seed", False))
        print(f"\n[DIAG] Per-criterion pass rates @ e-value ≤{t:.0e}")
        print(f"[DIAG]   positive (n={len(positive_ids)}): TC={n_tc_pos}  Pfam={n_pfam_pos}  Seed={n_seed_pos}")
        print(f"[DIAG]   negative (n={len(negative_ids)}): TC={n_tc_neg}  Pfam={n_pfam_neg}  Seed={n_seed_neg}")

    # 7. Apply strategies, compute metrics, write outputs
    all_metrics = {}
    all_family = {}
    all_family_with_neg = {}
    all_dist = {}

    for k in strategies:
        ev_used = evidence_by_thr[thr[k]]
        print(f"\n{'='*60}")
        print(f"  Strategy: {k} out of 3 (e-value ≤{thr[k]:.0e})")
        print(f"{'='*60}")

        results = apply_strategy(ev_used, k, positive_ids, negative_ids)
        metrics = compute_classic_metrics(results)
        family = compute_family_metrics(results, include_neg=False)
        dist = compute_evidence_distribution(results)

        all_metrics[k] = metrics
        all_family[k] = family
        all_dist[k] = dist
        if args.include_neg_family:
            all_family_with_neg[k] = compute_family_metrics(results, include_neg=True)

        print(f"  TP={metrics['TP']}  FP={metrics['FP']}  TN={metrics['TN']}  FN={metrics['FN']}")
        print(f"  Sensitivity={metrics['Sensitivity']}  Specificity={metrics['Specificity']}")
        print(f"  Precision={metrics['Precision']}  F1={metrics['F1']}")
        print(f"  Accuracy={metrics['Accuracy']}")

        write_predictions(results, os.path.join(args.output_dir, f"{args.prefix}_predictions_k{k}.tsv"))

    write_metrics_summary(all_metrics, os.path.join(args.output_dir, f"{args.prefix}_metrics_summary.tsv"))
    write_family_metrics(all_family, os.path.join(args.output_dir, f"{args.prefix}_family_metrics.tsv"))
    write_evidence_distribution(all_dist, os.path.join(args.output_dir, f"{args.prefix}_evidence_distribution.tsv"))
    if args.include_neg_family:
        write_family_metrics_with_neg(
            all_family_with_neg,
            os.path.join(args.output_dir, f"{args.prefix}_family_metrics_with_neg.tsv")
        )

    # 8. Plot PR/ROC curves
    # Build a threshold summary subtitle, e.g. "k1≤1e-20, k2≤1e-20, k3≤1e-10"
    parts = []
    for k in strategies:
        parts.append(f"k{k}≤{thr[k]:.0e}")
    thr_summary = ", ".join(parts)
    plot_pr_roc(all_metrics, args.output_dir, args.prefix, thr_summary=thr_summary)

    print(f"\n{'='*60}")
    print(f"  Evaluation complete. Results in: {args.output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
