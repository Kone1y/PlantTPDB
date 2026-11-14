#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build the per-species TC-family count matrix for CAFE5, with a large-family filter.

Input is the bundled 26-species full count matrix (CAFE5 format:
Desc \t Family ID \t <26 species columns>, one row per TC family, counts already
deduplicated at the gene level when the matrix was built).

Filter rules:
  - family_count_max: any per-species count >= this value -> the family is removed
  - family_diff_max : (max - min) >= this value -> the family is removed (optional)

Usage:
  python 04_get_tc_code_number.py \
      --species species.list \
      --desc    tc_family_descriptions.tsv \
      --matrix  cafe_input_matrix_26species.full.tsv \
      --output  cafe_input_matrix.tsv \
      --max-count 100 \
      --removed-output cafe_input_matrix.removed.tsv
"""

import argparse
import os
import sys


def load_representative_species(list_path):
    """Load the representative-species list into a lowercase set for case-insensitive matching."""
    species_set = set()
    if not os.path.exists(list_path):
        print(f"Error: species list file not found: {list_path}")
        sys.exit(1)
    with open(list_path, 'r', encoding='utf-8') as f:
        for line in f:
            sp = line.strip()
            if sp:
                species_set.add(sp.lower())
    return species_set


def load_tc_descriptions(desc_path):
    """Load the TC code -> description mapping."""
    tc_desc = {}
    if not os.path.exists(desc_path):
        print(f"Error: description file not found: {desc_path}")
        sys.exit(1)
    with open(desc_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t') if '\t' in line else line.split(maxsplit=1)
            if len(parts) == 2:
                tccode = parts[0].strip()
                desc = parts[1].strip()
                tc_desc[tccode] = desc
    return tc_desc


def format_species_name(name):
    """Capitalize the first letter of the species name only."""
    if not name:
        return name
    return name[0].upper() + name[1:]


def read_count_matrix(matrix_path, rep_species_lower):
    """
    Read the 26-species full count matrix (CAFE5 format) into
    {tccode: {species_lower: count}}.

    Expected layout (as written by the original matrix build and by
    write_matrix below): Desc \t Family ID \t species_1 \t ... \t species_26.
    Species columns are matched case-insensitively against the species list;
    the Desc column is ignored (descriptions are re-attached from
    tc_family_descriptions.tsv at write time). Counts were already
    gene-deduplicated when the full matrix was produced.
    """
    matrix = {}

    print(f"reading count matrix: {matrix_path} ...")
    with open(matrix_path, 'r', encoding='utf-8') as f:
        header = f.readline().rstrip('\n').split('\t')
        if len(header) < 3 or header[0].strip() != 'Desc' or header[1].strip() != 'Family ID':
            print(f"Error: unexpected matrix header in {matrix_path} "
                  f"(expected 'Desc \\t Family ID \\t <species ...>')")
            sys.exit(1)
        col_species = {}
        for idx, name in enumerate(header[2:], start=2):
            sp_lower = name.strip().lower()
            if sp_lower in rep_species_lower:
                col_species[idx] = sp_lower

        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            tccode = parts[1].strip()
            if not tccode:
                continue
            sp_counts = matrix.setdefault(tccode, {})
            for idx, sp_lower in col_species.items():
                value = parts[idx].strip() if idx < len(parts) else "0"
                sp_counts[sp_lower] = sp_counts.get(sp_lower, 0) + (int(value) if value else 0)

    return matrix


def filter_matrix(matrix, species_order_lower, max_count, diff_max):
    """
    Apply the CAFE5 family filter to the count matrix.

    Returns:
      kept:    {tccode: {species_lower: count}} retained families
      removed: list of (tccode, row_max, row_min, reason) removed families
    """
    kept = {}
    removed = []

    for tccode, sp_counts in matrix.items():
        counts = [sp_counts.get(sp, 0) for sp in species_order_lower]
        row_max = max(counts) if counts else 0
        row_min = min(counts) if counts else 0
        row_diff = row_max - row_min

        reason = None
        if max_count is not None and row_max >= max_count:
            reason = f"max_count_ge_{max_count}"
        elif diff_max is not None and row_diff >= diff_max:
            reason = f"diff_ge_{diff_max} (max={row_max}, min={row_min})"

        if reason:
            removed.append((tccode, row_max, row_min, reason))
        else:
            kept[tccode] = sp_counts

    return kept, removed


def write_matrix(matrix, output_path, species_order_original, tc_desc):
    """Write the CAFE5-format matrix: Desc \\t Family ID \\t sp1 \\t sp2 ..."""
    species_order_formatted = [format_species_name(sp) for sp in species_order_original]

    print(f"writing output file: {output_path} ...")
    with open(output_path, 'w', encoding='utf-8') as out:
        header = ["Desc", "Family ID"] + species_order_formatted
        out.write("\t".join(header) + "\n")

        for tccode in sorted(matrix.keys()):
            desc = tc_desc.get(tccode, "(null)")
            row = [desc, tccode]
            for sp_original in species_order_original:
                count = matrix[tccode].get(sp_original.lower(), 0)
                row.append(str(count))
            out.write("\t".join(row) + "\n")


def write_removed_report(removed, output_path, tc_desc):
    """Write the removed-families report: Desc \\t ID \\t max_count \\t reason"""
    print(f"writing removed-families report: {output_path} ...")
    with open(output_path, 'w', encoding='utf-8') as out:
        out.write("\t".join(["Desc", "ID", "max_count", "reason"]) + "\n")
        for tccode, row_max, _row_min, reason in sorted(removed, key=lambda x: x[0]):
            desc = tc_desc.get(tccode, "(null)")
            out.write("\t".join([desc, tccode, str(row_max), reason]) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Build the per-species TC-family count matrix (CAFE5 input format) with built-in family filtering"
    )
    parser.add_argument("--species", required=True,
                        help="representative species list file (one species name per line)")
    parser.add_argument("--desc", required=True,
                        help="TC code description file (tc_family_descriptions.tsv)")
    parser.add_argument("--matrix", required=True,
                        help="26-species full TC-family count matrix (cafe_input_matrix_26species.full.tsv)")
    parser.add_argument("--output", default="cafe_input_matrix.tsv",
                        help="filtered matrix output file (default: cafe_input_matrix.tsv)")
    parser.add_argument("--max-count", type=int, default=100,
                        help="family filter threshold: a per-species count >= this value removes the family (default: 100; <= 0 disables)")
    parser.add_argument("--diff-max", type=int, default=None,
                        help="optional: (max-min) >= this value removes the family (default: off)")
    parser.add_argument("--removed-output", default=None,
                        help="removed-families report path (default: <output>.removed.tsv)")
    args = parser.parse_args()

    # 1. load filter settings and metadata
    rep_species_lower = load_representative_species(args.species)
    tc_desc = load_tc_descriptions(args.desc)

    # preserve original casing and order
    species_order_original = []
    with open(args.species, 'r', encoding='utf-8') as f:
        for line in f:
            sp = line.strip()
            if sp and sp.lower() in rep_species_lower:
                species_order_original.append(sp)
    species_order_lower = [sp.lower() for sp in species_order_original]

    # 2. read the count matrix
    matrix = read_count_matrix(args.matrix, rep_species_lower)
    total_families = len(matrix)

    # 3. apply the CAFE5 filter
    max_count = args.max_count if args.max_count and args.max_count > 0 else None
    diff_max = args.diff_max if args.diff_max and args.diff_max > 0 else None

    if max_count is None and diff_max is None:
        print("[INFO] family filtering disabled (--max-count <= 0 and no --diff-max)")
        kept_matrix, removed = matrix, []
    else:
        kept_matrix, removed = filter_matrix(
            matrix, species_order_lower, max_count, diff_max
        )
        print(f"[INFO] total families: {total_families}")
        print(f"[INFO] kept: {len(kept_matrix)}")
        print(f"[INFO] removed: {len(removed)}")
        if max_count is not None:
            print(f"[INFO]   rule: any per-species count >= {max_count} -> removed")
        if diff_max is not None:
            print(f"[INFO]   rule: (max-min) >= {diff_max} -> removed")

    # 4. write the filtered matrix
    write_matrix(kept_matrix, args.output, species_order_original, tc_desc)

    # 5. write the removed-families report
    removed_path = args.removed_output
    if removed_path is None:
        base, ext = os.path.splitext(args.output)
        removed_path = f"{base}.removed{ext or '.tsv'}"

    if removed:
        write_removed_report(removed, removed_path, tc_desc)
    else:
        # write a header-only report when nothing was removed (pipeline consistency)
        with open(removed_path, 'w', encoding='utf-8') as out:
            out.write("\t".join(["Desc", "ID", "max_count", "reason"]) + "\n")
        print(f"[INFO] no families removed; empty report written: {removed_path}")

    print(f"Done. filtered matrix saved to: {os.path.abspath(args.output)}")
    print(f"removed-families report saved to: {os.path.abspath(removed_path)}")


if __name__ == "__main__":
    main()
