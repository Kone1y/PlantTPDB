#!/usr/bin/env python3
# 01_fetch_timetree.py
# Build a timetree from a species list via timetree.org.
# Usage: python 01_fetch_timetree.py species.list -o tree.nwk
import re
import sys
import argparse
import requests

BASE = "https://timetree.org"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def build_timetree(species_list_path, out_path):
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    # Step 1: upload names (field name MUST be "file")
    with open(species_list_path, "rb") as f:
        files = {"file": (species_list_path, f, "text/plain")}
        r = s.post(f"{BASE}/ajax/prune/load_names/", files=files)
    r.raise_for_status()
    widget = r.text

    # Parse unresolved names (if any) from the widget HTML
    unresolved = re.findall(
        r"<div id=\"unresolved-names\">\s*(.*?)</div>", widget, re.S
    )
    if unresolved:
        names = re.findall(
            r"(.*?)\s*\(not found.*?\)<br/>", unresolved[0] + "<br/>", re.S
        )
        names = [n.strip() for n in names if n.strip()]
        if names:
            print(
                f"[warn] {len(names)} name(s) not found in NCBI taxonomy: {names}",
                file=sys.stderr,
            )

    # Step 2: download newick (reuses PHPSESSID from step 1)
    r2 = s.post(
        f"{BASE}/ajax/newick/prunetree/download",
        data={"export": "newick", "rank": "", "taxon": ""},
    )
    r2.raise_for_status()

    with open(out_path, "w") as f:
        f.write(r2.text)
    print(f"[ok] wrote {len(r2.text)} bytes -> {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build a timetree from a species list.")
    ap.add_argument("species_list", help="text file with one species name per line")
    ap.add_argument("-o", "--out", default="tree.nwk", help="output newick file")
    a = ap.parse_args()
    build_timetree(a.species_list, a.out)
