#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03_auto_mcmctree_from_timetree.py

Functionality:
  1. Read a rooted species-tree Newick.
  2. Walk the internal nodes and automatically pick two leaves representing each MRCA.
  3. Query the TimeTree pairwise API via a species-name -> NCBI TaxID mapping file.
  4. Build MCMCtree calibration ranges from TimeTree precomputed_ci_low / precomputed_ci_high.
  5. Write a Newick tree carrying MCMCtree node-calibration labels, e.g.:
        (Arabidopsis_thaliana,Brassica_rapa)'>0.015<0.045'
  6. Optional: run IQ-TREE --dating mcmctree and the modified MCMCtree automatically.
  7. Optional: extract a clean ultrametric Newick from FigTree.tre.

Notes:
  - TimeTree calibrations are secondary calibrations; for publication, trace the primary
    literature and run sensitivity analyses. unit-mya defaults to 1000 (1.0 = 1000 Ma),
    so 15-45 Ma is written as '>0.015<0.045'.
"""

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from itertools import combinations
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("[ERROR] requests is required: pip install requests")

try:
    from Bio import Phylo
except ImportError:
    sys.exit("[ERROR] biopython is required: pip install biopython")


DEFAULT_TIMETREE_API = "http://timetree.temple.edu/api"


# ----------------------------- basic utils ----------------------------- #

def normalize_species_name(name: str) -> str:
    """Arabidopsis_thaliana -> Arabidopsis thaliana"""
    return name.strip().replace("_", " ")


def safe_float(x):
    if x in ("", None, "NA", "NaN", "nan"):
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def read_taxid_mapping(mapping_file: str):
    """
    Parse a file like 733_with_taxID.result:
      species words ... TaxID

    Example:
      Arabidopsis thaliana 3702
      Brassica rapa 3711
    """
    mapping = {}
    with open(mapping_file, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if line_no == 1 and "TaxID" in line:
                continue

            # tab- or space-separated; the last column must be the taxid, the rest form the species name
            parts = line.split()
            if len(parts) < 2:
                continue

            taxid = parts[-1]
            species = " ".join(parts[:-1])
            if not taxid.isdigit():
                continue

            mapping[species.lower()] = {
                "species": species,
                "taxid": taxid,
            }

    if not mapping:
        raise ValueError(f"No valid records in the TaxID mapping file: {mapping_file}")
    return mapping


def get_taxid(species_underscore: str, taxid_map: dict):
    species_space = normalize_species_name(species_underscore)
    key = species_space.lower()
    if key not in taxid_map:
        return None, species_space
    return taxid_map[key]["taxid"], taxid_map[key]["species"]


# ----------------------------- TimeTree API ----------------------------- #

def load_pairwise_cache(cache_file):
    """Load TimeTree pairwise API responses from JSON cache file."""
    if not cache_file:
        return {}
    cache_path = Path(cache_file)
    if not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cache = {tuple(k.split(",")): v for k, v in data.items()}
        print(f"[cache] loaded {len(cache)} cached pairwise responses from {cache_path}")
        return cache
    except Exception as e:
        print(f"[cache] WARN: failed to load cache from {cache_path}: {e}", file=sys.stderr)
        return {}


def save_pairwise_cache(cache, cache_file):
    """Persist TimeTree pairwise API cache to JSON file."""
    if not cache_file:
        return
    cache_path = Path(cache_file)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {",".join(k): v for k, v in cache.items()}
    cache_path.write_text(
        json.dumps(serializable, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"[cache] saved {len(cache)} responses to {cache_path}")


def query_timetree_pairwise(taxid1: str, taxid2: str, api_base: str,
                            timeout: int = 30, sleep: float = 0.2,
                            cache=None):
    """
    Call the TimeTree pairwise API:
      {api_base}/pairwise/{taxid1}/{taxid2}

    Expects a CSV-like text whose fields usually include:
      precomputed_age
      precomputed_ci_low
      precomputed_ci_high
      adjusted_age
      all_total
    """
    key = tuple(sorted([str(taxid1), str(taxid2)]))
    if cache is not None and key in cache:
        return cache[key]

    url = f"{api_base.rstrip('/')}/pairwise/{taxid1}/{taxid2}"
    time.sleep(sleep)

    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    text = r.text.strip()
    if not text:
        raise RuntimeError(f"TimeTree API returned empty content: {url}")

    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if len(rows) < 2:
        raise RuntimeError(f"TimeTree API returned an invalid table: {url}\n{text[:300]}")

    rec = dict(zip(rows[0], rows[1]))
    for k in ("precomputed_age", "precomputed_ci_low", "precomputed_ci_high",
              "adjusted_age", "all_total"):
        if k in rec:
            v = safe_float(rec[k])
            if v is not None:
                rec[k] = v

    rec["_api_url"] = url

    if cache is not None:
        cache[key] = rec
    return rec


def get_ci_from_timetree_record(rec: dict, fallback_width: float = 0.25):
    """
    Prefer precomputed_ci_low / precomputed_ci_high.
    Without a CI, build a conservative range from precomputed_age +/- fallback_width.
    """
    age = safe_float(rec.get("precomputed_age"))
    low = safe_float(rec.get("precomputed_ci_low"))
    high = safe_float(rec.get("precomputed_ci_high"))

    if low is not None and high is not None and low > 0 and high > low:
        return low, high, "precomputed_ci"

    if age is not None and age > 0:
        low = max(age * (1.0 - fallback_width), 0.001)
        high = age * (1.0 + fallback_width)
        return low, high, f"fallback_pm_{fallback_width:.2f}"

    return None, None, "missing"


# ----------------------------- tree traversal ----------------------------- #

def terminal_names(clade):
    return [t.name for t in clade.get_terminals() if t.name]


def select_pair_for_node(clade, taxid_map):
    """
    Pick two leaves for one internal node from different child subtrees,
    so that their MRCA is exactly that internal node.
    """
    children = list(clade.clades)
    if len(children) < 2:
        return None

    child_tips = [terminal_names(c) for c in children]
    child_tips = [tips for tips in child_tips if tips]
    if len(child_tips) < 2:
        return None

    # prefer pairs whose leaves both have TaxIDs in taxid_map
    for i, j in combinations(range(len(child_tips)), 2):
        for a in child_tips[i]:
            taxid_a, _ = get_taxid(a, taxid_map)
            if taxid_a is None:
                continue
            for b in child_tips[j]:
                taxid_b, _ = get_taxid(b, taxid_map)
                if taxid_b is None:
                    continue
                return a, b

    # without a complete-taxid pair, return the first cross-subtree pair (marked failed later)
    return child_tips[0][0], child_tips[1][0]


def node_depth_by_tips(clade):
    """Sort by descendant-leaf count: calibrate deep / major-clade nodes first."""
    return len(clade.get_terminals())


def iter_candidate_internal_nodes(tree, include_root=True,
                                  min_descendant_tips=2,
                                  exclude_cherries=False):
    """
    Return the candidate internal nodes.
    Root is included by default; exclude_cherries skips shallow two-leaf nodes.
    """
    root = tree.root
    for clade in tree.find_clades(order="level"):
        if clade.is_terminal():
            continue
        if clade is root and not include_root:
            continue
        ntips = node_depth_by_tips(clade)
        if ntips < min_descendant_tips:
            continue
        if exclude_cherries and ntips == 2:
            continue
        yield clade


# ----------------------------- MCMCtree newick writer ----------------------------- #

def format_mcmctree_bound(low_mya: float, high_mya: float, unit_mya: float):
    """
    MCMCtree unit conversion:
      value_in_tree = age_Ma / unit_mya

    e.g. unit_mya=1000:
      15-45 Ma -> '>0.015<0.045'
    """
    low = low_mya / unit_mya
    high = high_mya / unit_mya
    if low <= 0 or high <= low:
        raise ValueError(f"Invalid calibration range: low={low_mya}, high={high_mya}")

    # 6 significant digits to keep labels short
    return f"'>%.6g<%.6g'" % (low, high)


def serialize_newick(clade, annotations: dict, keep_branch_lengths=False):
    """
    Serialize the Newick ourselves so internal-node calibration labels are written as:
      (...)'>0.015<0.045'
    """
    if clade.is_terminal():
        s = clade.name
    else:
        s = "(" + ",".join(
            serialize_newick(c, annotations, keep_branch_lengths)
            for c in clade.clades
        ) + ")"
        if id(clade) in annotations:
            s += annotations[id(clade)]

    if keep_branch_lengths and clade.branch_length is not None:
        s += f":{clade.branch_length:.10g}"

    return s


def write_calibrated_tree(tree, annotations: dict, output_tree: str,
                          keep_branch_lengths=False):
    nwk = serialize_newick(tree.root, annotations, keep_branch_lengths) + ";\n"
    Path(output_tree).write_text(nwk, encoding="utf-8")


# ----------------------------- calibration collection ----------------------------- #

def collect_timetree_calibrations(tree_file: str,
                                  taxid_map_file: str,
                                  output_tsv: str,
                                  api_base: str = DEFAULT_TIMETREE_API,
                                  unit_mya: float = 1000.0,
                                  max_calibrations: int = 12,
                                  min_descendant_tips: int = 2,
                                  include_root: bool = True,
                                  exclude_cherries: bool = False,
                                  sleep: float = 0.2,
                                  fallback_width: float = 0.25,
                                  cache_file=None):
    """
    Walk the internal nodes, query TimeTree automatically, and emit calibration records and node annotations.
    """
    tree = Phylo.read(tree_file, "newick")
    taxid_map = read_taxid_mapping(taxid_map_file)

    candidates = list(iter_candidate_internal_nodes(
        tree,
        include_root=include_root,
        min_descendant_tips=min_descendant_tips,
        exclude_cherries=exclude_cherries,
    ))

    # deep nodes first; root and major clades first
    candidates.sort(key=lambda c: node_depth_by_tips(c), reverse=True)

    annotations = {}
    rows = []
    cache = load_pairwise_cache(cache_file)
    accepted = 0

    for idx, clade in enumerate(candidates, 1):
        if max_calibrations > 0 and accepted >= max_calibrations:
            break

        ntips = node_depth_by_tips(clade)
        pair = select_pair_for_node(clade, taxid_map)
        if not pair:
            rows.append({
                "node_rank": idx,
                "status": "skip_no_pair",
                "n_descendant_tips": ntips,
            })
            continue

        sp_a, sp_b = pair
        taxid_a, species_a_space = get_taxid(sp_a, taxid_map)
        taxid_b, species_b_space = get_taxid(sp_b, taxid_map)

        base_row = {
            "node_rank": idx,
            "status": "",
            "n_descendant_tips": ntips,
            "species_a": sp_a,
            "species_b": sp_b,
            "species_a_space": species_a_space,
            "species_b_space": species_b_space,
            "taxid_a": taxid_a or "",
            "taxid_b": taxid_b or "",
            "precomputed_age_mya": "",
            "precomputed_ci_low_mya": "",
            "precomputed_ci_high_mya": "",
            "bound_low_mya": "",
            "bound_high_mya": "",
            "mcmctree_label": "",
            "ci_source": "",
            "api_url": "",
            "error": "",
        }

        if taxid_a is None or taxid_b is None:
            base_row["status"] = "skip_missing_taxid"
            rows.append(base_row)
            continue

        try:
            cache_size_before = len(cache)
            rec = query_timetree_pairwise(
                taxid_a, taxid_b,
                api_base=api_base,
                sleep=sleep,
                cache=cache,
            )
            if cache_file and len(cache) > cache_size_before:
                save_pairwise_cache(cache, cache_file)
            low, high, ci_source = get_ci_from_timetree_record(
                rec,
                fallback_width=fallback_width,
            )

            base_row["precomputed_age_mya"] = rec.get("precomputed_age", "")
            base_row["precomputed_ci_low_mya"] = rec.get("precomputed_ci_low", "")
            base_row["precomputed_ci_high_mya"] = rec.get("precomputed_ci_high", "")
            base_row["api_url"] = rec.get("_api_url", "")
            base_row["ci_source"] = ci_source

            if low is None or high is None:
                base_row["status"] = "skip_no_age"
                rows.append(base_row)
                continue

            label = format_mcmctree_bound(low, high, unit_mya=unit_mya)
            annotations[id(clade)] = label

            base_row["status"] = "accepted"
            base_row["bound_low_mya"] = low
            base_row["bound_high_mya"] = high
            base_row["mcmctree_label"] = label
            rows.append(base_row)
            accepted += 1

        except Exception as e:
            base_row["status"] = "skip_api_error"
            base_row["error"] = str(e)
            rows.append(base_row)
            continue

    fieldnames = [
        "node_rank", "status", "n_descendant_tips",
        "species_a", "species_b",
        "species_a_space", "species_b_space",
        "taxid_a", "taxid_b",
        "precomputed_age_mya",
        "precomputed_ci_low_mya",
        "precomputed_ci_high_mya",
        "bound_low_mya", "bound_high_mya",
        "mcmctree_label", "ci_source",
        "api_url", "error",
    ]

    with open(output_tsv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        w.writeheader()
        for row in rows:
            w.writerow(row)

    return tree, annotations, rows


# ----------------------------- command runners ----------------------------- #

def run_cmd(cmd, cwd=None, log_file=None):
    print("[CMD] " + " ".join(map(str, cmd)))
    if log_file:
        with open(log_file, "w", encoding="utf-8") as out:
            p = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, text=True)
    else:
        p = subprocess.run(cmd, cwd=cwd, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed, returncode={p.returncode}: {' '.join(map(str, cmd))}")
    return p


def extract_figtree_to_clean_newick(figtree_file: str, output_newick: str):
    """
    Extract the UTREE/TREE from MCMCtree's FigTree.tre and drop [&95%HPD=...] comments.
    """
    txt = Path(figtree_file).read_text(encoding="utf-8")
    m = re.search(r"(?:U?TREE)\s+[^=]*=\s*(.+?);", txt, flags=re.I | re.S)
    if not m:
        raise RuntimeError(f"No TREE/UTREE line found in {figtree_file}")

    nwk = m.group(1).strip() + ";"
    nwk = re.sub(r"\s*\[&[^\]]*\]", "", nwk)
    nwk = re.sub(r"\s+", "", nwk)
    Path(output_newick).write_text(nwk + "\n", encoding="utf-8")
    return output_newick


def write_shell_script(path, commands):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("set -euo pipefail\n\n")
        for cmd in commands:
            f.write(cmd.rstrip() + "\n\n")
    os.chmod(path, 0o755)


# ----------------------------- main ----------------------------- #

def main():
    p = argparse.ArgumentParser(
        description="Fetch multiple node-calibration ranges from the TimeTree API and build an ultrametric tree with IQ-TREE + MCMCtree"
    )

    p.add_argument("--tree", required=True,
                   help="Input rooted species-tree Newick; used to pick MRCA nodes and emit the calibrated tree.")
    p.add_argument("--taxid-map", required=True,
                   help="Species-name to NCBI TaxID mapping file; last column = TaxID, the rest = species name.")
    p.add_argument("--outdir", required=True,
                   help="Output directory.")
    p.add_argument("--api-base", default=DEFAULT_TIMETREE_API,
                   help=f"TimeTree API base, default {DEFAULT_TIMETREE_API}")
    p.add_argument("--unit-mya", type=float, default=1000.0,
                   help="MCMCtree time unit. Default 1000 (1.0 = 1000 Ma).")
    p.add_argument("--max-calibrations", type=int, default=12,
                   help="Maximum number of internal-node calibrations. Default 12; 0 = unlimited.")
    p.add_argument("--min-descendant-tips", type=int, default=2,
                   help="Minimum number of leaves per candidate node. Default 2.")
    p.add_argument("--exclude-cherries", action="store_true",
                   help="Skip shallow sister-pair nodes with only 2 leaves.")
    p.add_argument("--no-root-calibration", action="store_true",
                   help="Do not calibrate the root node.")
    p.add_argument("--fallback-width", type=float, default=0.25,
                   help="Without a TimeTree CI, build the range as age +/- this fraction. Default 0.25.")
    p.add_argument("--sleep", type=float, default=0.2,
                   help="Delay between TimeTree API requests. Default 0.2 s.")
    p.add_argument("--cache", default=None,
                   help="Path of the TimeTree pairwise-API JSON cache; hits avoid repeated requests.")
    p.add_argument("--keep-branch-lengths-in-cal-tree", action="store_true",
                   help="Keep the original branch lengths in the calibrated tree. Usually not recommended.")

    # IQ-TREE / MCMCtree optional run
    p.add_argument("--alignment", default=None,
                    help="Alignment file for IQ-TREE --dating mcmctree, e.g. busco_supermatrix.phy.")
    p.add_argument("--partition", default=None,
                    help="IQ-TREE partition file, e.g. busco_partitions.iqtree.txt.")
    p.add_argument("--model", default=None,
                    help="IQ-TREE model when partitions are not used, e.g. LG+F+G4.")
    p.add_argument("--iqtree-bin", default="iqtree3",
                    help="IQ-TREE executable. Default iqtree3.")
    p.add_argument("--mcmctree-bin", default="mcmctree",
                    help="Modified MCMCtree executable for IQ-TREE dating.")
    p.add_argument("--prefix", default="plant_auto",
                    help="IQ-TREE output prefix. Default plant_auto.")
    p.add_argument("--mcmc-iter", default="20000,100,20000",
                    help="--mcmc-iter passed to IQ-TREE, format burnin,sampfreq,nsample.")
    p.add_argument("--mcmc-bds", default="1,1,0.5",
                    help="--mcmc-bds passed to IQ-TREE.")
    p.add_argument("--mcmc-clock", default="CORR",
                   choices=["EQUAL", "IND", "CORR"],
                    help="--mcmc-clock passed to IQ-TREE. Default CORR.")
    p.add_argument("--run-iqtree", action="store_true",
                    help="Run IQ-TREE --dating mcmctree right after emitting the calibrated tree.")
    p.add_argument("--run-mcmctree", action="store_true",
                    help="Run the modified MCMCtree right after IQ-TREE finishes.")
    p.add_argument("--force", action="store_true",
                    help="Allow overwriting existing files in outdir.")

    args = p.parse_args()

    outdir = Path(args.outdir)
    if outdir.exists() and any(outdir.iterdir()) and not args.force:
        sys.exit(f"[ERROR] Output directory is not empty: {outdir}; add --force to overwrite")
    outdir.mkdir(parents=True, exist_ok=True)

    for f in [args.tree, args.taxid_map]:
        if not os.path.isfile(f):
            sys.exit(f"[ERROR] File not found: {f}")

    cal_tsv = outdir / "timetree_node_calibrations.tsv"
    cal_tree = outdir / "calibrated_mcmctree_tree.nwk"
    clean_ultrametric = outdir / f"{args.prefix}.mcmctree.ultrametric.nwk"
    shell_script = outdir / "run_iqtree_mcmctree.sh"

    print("[1/4] Fetching internal-node calibrations from the TimeTree API")
    tree, annotations, rows = collect_timetree_calibrations(
        tree_file=args.tree,
        taxid_map_file=args.taxid_map,
        output_tsv=str(cal_tsv),
        api_base=args.api_base,
        unit_mya=args.unit_mya,
        max_calibrations=args.max_calibrations,
        min_descendant_tips=args.min_descendant_tips,
        include_root=(not args.no_root_calibration),
        exclude_cherries=args.exclude_cherries,
        sleep=args.sleep,
        fallback_width=args.fallback_width,
        cache_file=args.cache,
    )

    accepted = sum(1 for r in rows if r.get("status") == "accepted")
    print(f"      accepted calibrations: {accepted}")
    print(f"      calibration table: {cal_tsv}")

    if accepted == 0:
        sys.exit("[ERROR] No calibration was generated; check the taxid map, the TimeTree API, and species names.")

    print("[2/4] Writing the MCMCtree calibrated tree")
    write_calibrated_tree(
        tree,
        annotations,
        output_tree=str(cal_tree),
        keep_branch_lengths=args.keep_branch_lengths_in_cal_tree,
    )
    print(f"      calibrated tree: {cal_tree}")

    iqtree_prefix = outdir / args.prefix
    iqtree_cmd = [
        args.iqtree_bin,
        "-s", args.alignment if args.alignment else "ALIGNMENT.phy",
    ]

    if args.partition:
        iqtree_cmd += ["-p", args.partition]
    elif args.model:
        iqtree_cmd += ["-m", args.model]
    else:
        iqtree_cmd += ["-m", "MFP"]

    iqtree_cmd += [
        "-te", str(cal_tree),
        "--dating", "mcmctree",
        "--mcmc-iter", args.mcmc_iter,
        "--mcmc-bds", args.mcmc_bds,
        "--mcmc-clock", args.mcmc_clock,
        "-T", "AUTO",
        "--prefix", str(iqtree_prefix),
    ]

    mcmctree_ctl = f"{iqtree_prefix}.mcmctree.ctl"
    mcmctree_cmd = [args.mcmctree_bin, mcmctree_ctl]

    write_shell_script(str(shell_script), [
        " ".join(map(str, iqtree_cmd)),
        " ".join(map(str, mcmctree_cmd)),
        f"python3 {Path(__file__).resolve()} --extract-only "
        f"--figtree FigTree.tre --clean-newick {clean_ultrametric}",
    ])

    print("[3/4] Writing the reproducible run script")
    print(f"      shell script: {shell_script}")

    if args.run_iqtree:
        if not args.alignment:
            sys.exit("[ERROR] --run-iqtree requires --alignment")
        print("[RUN] IQ-TREE --dating mcmctree")
        run_cmd(iqtree_cmd)

    if args.run_mcmctree:
        if not args.run_iqtree and not os.path.exists(mcmctree_ctl):
            sys.exit(f"[ERROR] {mcmctree_ctl} not found; run IQ-TREE first or add --run-iqtree")
        print("[RUN] modified MCMCtree")
        run_cmd(mcmctree_cmd)

        figtree_candidates = [
            Path("FigTree.tre"),
            outdir / "FigTree.tre",
        ]
        figtree_file = next((p for p in figtree_candidates if p.exists()), None)
        if figtree_file:
            extract_figtree_to_clean_newick(str(figtree_file), str(clean_ultrametric))
            print(f"      clean ultrametric Newick: {clean_ultrametric}")
        else:
            print("[WARN] FigTree.tre not found; locate it manually and use --extract-only.")

    print("[4/4] Done")
    print(f"  - calibration table: {cal_tsv}")
    print(f"  - MCMCtree calibrated tree: {cal_tree}")
    print(f"  - run script: {shell_script}")
    print("\nNext step is usually:")
    print(f"  bash {shell_script}")


def extract_only_main():
    p = argparse.ArgumentParser(description="Extract a clean Newick from FigTree.tre")
    p.add_argument("--extract-only", action="store_true")
    p.add_argument("--figtree", required=True)
    p.add_argument("--clean-newick", required=True)
    args = p.parse_args()
    extract_figtree_to_clean_newick(args.figtree, args.clean_newick)
    print(f"[DONE] {args.clean_newick}")


if __name__ == "__main__":
    if "--extract-only" in sys.argv:
        extract_only_main()
    else:
        main()
