#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_compute_structure_alignment_rate.py — compute the structural alignment rate Lali/L2
from a USalign tabular output.

Two input formats are supported (auto-detected from the first data row):
  A. prefixed format written by 03_run_usalign.py (14 columns):
     Species  Protein_ID  PDB_ID | PDBchain1 PDBchain2 TM1 TM2 RMSD ID1 ID2
     IDali L1 L2 Lali
  B. plain USalign (-outfmt 2) table (11 columns, e.g. usalign_results.txt):
     PDBchain1 PDBchain2 TM1 TM2 RMSD ID1 ID2 IDali L1 L2 Lali
     the three prefix columns are restored from the chain paths (species/protein
     from the PDBchain1 filename, PDB chain from PDBchain2); output is 15 columns.

Columns: chain 1 = AF3 model (<species>/<protein>_model.cif:A),
         chain 2 = PDB reference chain (pdb_single_chains_0-1/<PDB_chain>.cif:<chain>);
         L1/L2 = chain lengths, Lali = number of aligned residue pairs.
The structural alignment rate = Lali / L2 (alignment coverage normalized by the
PDB reference chain length); L2 = 0 is reported as NA.

Usage:
    python3 04_compute_structure_alignment_rate.py \
        -i usalign_results.txt \
        -o 04_structure_alignment_rate.txt \
        [--min-rate 0.5]     # optional: also save rows with rate >= threshold to <output>.filtered.txt
"""
import argparse
import sys

N_USALIGN = 11               # PDBchain1..Lali = 11 columns
I_L2_PLAIN = 9               # index of L2 in the 11-column format
I_LALI_PLAIN = 10


def derive_prefix(pdbchain1, pdbchain2):
    """Restore the Species / Protein_ID / PDB_ID columns from chain paths (11-col format)."""
    try:
        species = pdbchain1.split("/")[0]
        protein = pdbchain1.split("/")[1].split("_model.cif")[0]
    except IndexError:
        species, protein = "", ""
    pdb_id = pdbchain2.split("/")[-1].split(".cif")[0]
    return species, protein, pdb_id


def main():
    ap = argparse.ArgumentParser(
        description="Compute the structural alignment rate Lali/L2 from "
                    "tabular USalign results.")
    ap.add_argument("-i", "--input", default="usalign_results.txt",
                    help="USalign tabular result file (default: usalign_results.txt)")
    ap.add_argument("-o", "--output", default="04_structure_alignment_rate.txt",
                    help="output file (default: 04_structure_alignment_rate.txt)")
    ap.add_argument("--min-rate", type=float, default=None,
                    help="optional threshold: save rows with rate >= this value to <output>.filtered.txt")
    args = ap.parse_args()

    n_total = n_ok = n_zero = n_bad = 0
    rates = []
    filtered = []
    mode = None               # "prefixed" (14 cols) | "plain" (11 cols)

    with open(args.input, encoding="utf-8") as fh, \
         open(args.output, "w", encoding="utf-8") as out:
        out.write("Species\tProtein_ID\tPDB_ID\tPDBchain1\tPDBchain2\tTM1\tTM2\t"
                  "RMSD\tID1\tID2\tIDali\tL1\tL2\tLali\tstructure_alignment_rate\n")

        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                continue
            first_field = line.split("\t", 1)[0].strip()
            if first_field in ("Species", "Protein_ID", "PDB_ID", "PDBchain1", "PDBchain2"):
                continue
            n_total += 1
            parts = line.split("\t")

            if mode is None:
                mode = "prefixed" if len(parts) == 3 + N_USALIGN else "plain"

            if mode == "prefixed":
                prefix, us = parts[:3], parts[3:]
            else:
                us = parts
                prefix = list(derive_prefix(us[0] if us else "", us[1] if len(us) > 1 else ""))

            if len(us) != N_USALIGN:
                n_bad += 1
                out.write("\t".join(prefix + us + ["PARSE_ERROR"]) + "\n")
                continue
            try:
                l2 = float(us[I_L2_PLAIN])
                lali = float(us[I_LALI_PLAIN])
            except ValueError:
                n_bad += 1
                out.write("\t".join(prefix + us + ["PARSE_ERROR"]) + "\n")
                continue
            if l2 <= 0:
                n_zero += 1
                out.write("\t".join(prefix + us + ["NA"]) + "\n")
                continue
            rate = lali / l2
            n_ok += 1
            rates.append(rate)
            row = "\t".join(prefix + us + [f"{rate:.4f}"])
            out.write(row + "\n")
            if args.min_rate is not None and rate >= args.min_rate:
                filtered.append(row)

    if args.min_rate is not None:
        with open(args.output + ".filtered.txt", "w", encoding="utf-8") as fh:
            fh.write("Species\tProtein_ID\tPDB_ID\tPDBchain1\tPDBchain2\tTM1\tTM2\t"
                     "RMSD\tID1\tID2\tIDali\tL1\tL2\tLali\tstructure_alignment_rate\n")
            for row in filtered:
                fh.write(row + "\n")

    rates.sort()
    def pct(p):
        return rates[min(len(rates) - 1, int(p * len(rates)))] if rates else float("nan")
    print(f"[OK] {n_total} rows (format: {mode}): valid {n_ok}, L2=0 -> NA {n_zero}, unparsed {n_bad}")
    if rates:
        print(f"     Lali/L2  min={rates[0]:.4f}  median={pct(0.5):.4f}  "
              f"mean={sum(rates) / len(rates):.4f}  max={rates[-1]:.4f}")
    if args.min_rate is not None:
        print(f"     --min-rate {args.min_rate}: kept {len(filtered)} rows "
              f"({100.0 * len(filtered) / max(n_ok, 1):.1f}%) -> {args.output}.filtered.txt")


if __name__ == "__main__":
    main()
