#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

def parse_args():
    parser = argparse.ArgumentParser(description="Parallel USalign execution script for plant genomes.")
    parser.add_argument("-i", "--input", default="data/af3_pdb_pairs.list", help="input pair-list file")
    parser.add_argument("-p", "--pdb_dir", default="pdb_single_chains_0-1", help="directory holding the PDB single-chain CIF files")
    parser.add_argument("-o", "--output", default="usalign_total_results.txt", help="output file for the combined results")
    parser.add_argument("-t", "--threads", type=int, default=20, help="number of parallel workers (default: 20)")
    return parser.parse_args()

def build_species_file_maps(input_list_file):
    """
    Pre-scan the species directories referenced by the input list and build a
    {lowercase filename -> actual filename} map to avoid repeated disk lookups
    in the parallel workers.
    """
    print("[1/3] scanning species dirs, building case-insensitive file map...", file=sys.stderr)
    distinct_species = set()

    if not os.path.exists(input_list_file):
        print(f"ERROR: input file not found: {input_list_file}", file=sys.stderr)
        sys.exit(1)

    with open(input_list_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split()
                if parts:
                    distinct_species.add(parts[0])

    species_maps = {}
    for species in distinct_species:
        if os.path.isdir(species):
            # map e.g. {'os09t0524300-00_model.cif': 'Os09t0524300-00_model.cif'}
            species_maps[species] = {f.lower(): f for f in os.listdir(species) if f.lower().endswith('.cif')}
        else:
            print(f"WARNING: species directory not found: '{species}/'", file=sys.stderr)
            species_maps[species] = {}

    return species_maps

def align_single_pair(line_idx, species, protein_id, pdb_id, species_maps, pdb_dir):
    """
    Run the USalign comparison for one pair of CIF files.
    """
    # 1. locate the species-local AF3 model CIF (case-insensitive match)
    target_lower_filename = f"{protein_id.lower()}_model.cif"
    file_map = species_maps.get(species, {})

    if target_lower_filename not in file_map:
        return f"# Error [Line {line_idx}]: model CIF for {protein_id} not found under {species}\n"

    real_filename = file_map[target_lower_filename]
    cif1_path = os.path.join(species, real_filename)

    # 2. locate the PDB chain CIF
    cif2_path = os.path.join(pdb_dir, f"{pdb_id}.cif")
    if not os.path.exists(cif2_path):
        # tolerate case variation of the PDB ID in filenames
        if os.path.exists(os.path.join(pdb_dir, f"{pdb_id.lower()}.cif")):
            cif2_path = os.path.join(pdb_dir, f"{pdb_id.lower()}.cif")
        elif os.path.exists(os.path.join(pdb_dir, f"{pdb_id.upper()}.cif")):
            cif2_path = os.path.join(pdb_dir, f"{pdb_id.upper()}.cif")
        else:
            return f"# Error [Line {line_idx}]: PDB CIF not found: {cif2_path}\n"

    # 3. build and run the USalign command
    # with -outfmt 2, USalign writes a one-line tabular result to stdout
    cmd = ["USalign", "-mm", "1", "-ter", "1", cif1_path, cif2_path, "-outfmt", "2"]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        output_line = res.stdout.strip()
        if output_line:
            # prefix species/protein/PDB columns for downstream awk or pandas filtering
            return f"{species}\t{protein_id}\t{pdb_id}\t{output_line}\n"
        else:
            return f"# Warning [Line {line_idx}]: USalign exited 0 but produced no stdout ({protein_id} <-> {pdb_id})\n"
    except subprocess.CalledProcessError as e:
        return f"# Error [Line {line_idx}]: USalign failed ({protein_id} <-> {pdb_id}). stderr: {e.stderr.strip()}\n"
    except Exception as e:
        return f"# Error [Line {line_idx}]: unexpected exception ({protein_id} <-> {pdb_id}): {str(e)}\n"

def main():
    args = parse_args()

    # build the fast-lookup hash table
    species_maps = build_species_file_maps(args.input)

    # parse the input pair list and build the task queue
    print("[2/3] parsing task list...", file=sys.stderr)
    tasks = []
    with open(args.input, 'r', encoding='utf-8') as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 3:
                species = parts[0]
                protein_id = parts[1]
                pdb_id = parts[2]
                tasks.append((idx, species, protein_id, pdb_id))

    total_tasks = len(tasks)
    print(f"{total_tasks} comparison tasks detected; running with {args.threads} threads...", file=sys.stderr)

    # submit tasks in parallel and stream results to the output file
    completed_count = 0
    with open(args.output, 'w', encoding='utf-8') as out_f:
        # write the custom header prefix
        out_f.write("#Species\tProtein_ID\tPDB_ID\tUSalign_Tabular_Outputs\n")

        # ThreadPoolExecutor concurrency: subprocess.run releases the GIL,
        # so a thread pool is lightweight and efficient here
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            # submit all tasks to the pool
            future_to_task = {
                executor.submit(align_single_pair, idx, sp, prot, pdb, species_maps, args.pdb_dir): (sp, prot, pdb)
                for idx, sp, prot, pdb in tasks
            }

            # consume completed futures as they finish (bounded memory, live progress)
            for future in as_completed(future_to_task):
                result_str = future.result()
                out_f.write(result_str)

                completed_count += 1
                if completed_count % 50 == 0 or completed_count == total_tasks:
                    sys.stderr.write(f"\rprogress: [{completed_count}/{total_tasks}] tasks done...")
                    sys.stderr.flush()

    print(f"\n\n[3/3] done. all results written to: {args.output}", file=sys.stderr)

if __name__ == "__main__":
    main()

