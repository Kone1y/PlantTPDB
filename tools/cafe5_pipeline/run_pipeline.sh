#!/usr/bin/env bash
# run_pipeline.sh — main pipeline (BUSCO + IQ-TREE + MCMCtree + CAFE5)
# 26-species TC transporter gene-family expansion/contraction analysis:
# single-rate (lambda1) and two-rate (lambda2) models + BH-FDR + publication figures.
#
# Usage:
#   bash 00_run_all.sh run-all \
#       --species species.list \
#       --config config.yaml \
#       --outdir rerun_test \
#       [--matrix-type tc] [--tree-mode ml] [--force]
#
# tree-mode (env var of the same name, TREE_MODE):
#   ml (default; matches the manuscript Methods) IQ-TREE ModelFinder (-m MFP+MERGE)
#       jointly selects substitution models and a partition-merge scheme, then searches
#       the topology by ML; the TimeTree tree is used only for outgroup determination
#       and MCMCtree calibration, not as a topology constraint
#
# Input data (TC count matrix / descriptions / TaxID map / BUSCO results / TimeTree topology)
# lives next to this script; relative paths in config.yaml resolve against it;
# software paths go in config.yaml (defaults are commands from PATH).
#
# See README.md for the pipeline overview.





#
# Important optional environment variables (take precedence over config.yaml):
#   TREE_MODE=ml                    species-tree construction mode (default ml)
#   THREADS=16                      worker threads
#   CAFE2_JOBS=4                    parallel CAFE restart jobs
#   DEEP_LAMBDA_TAXA="A,B,C"        lambda-2 species group (comma-separated)
#   TIMETREE_NWK=/path/to/tree.nwk  use a pre-downloaded TimeTree topology
#   SKIP_2LAMBDA=1                  skip the 2-lambda analysis (base/lambda-1 only)
#   LAMBDA1_RESTARTS=0              skip the lambda-1 robust-restart lane
#   FORCE=1                         same as --force

set -Eeuo pipefail
shopt -s nullglob

# ----------------------------- [1] paths / defaults / CLI ----------------------------- #

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALLER_DIR="$(pwd)"

ACTION="" SPECIES_ARG="" CONFIG_FILE="" OUTDIR_ARG="" MATRIX_TYPE="tc" TREE_MODE_CLI="" LABEL_IGNORED=""
FORCE="${FORCE:-}"

usage() { sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        run-all)       ACTION="$1"; shift ;;
        --species)     SPECIES_ARG="$2"; shift 2 ;;
        --config)      CONFIG_FILE="$2"; shift 2 ;;
        --outdir)      OUTDIR_ARG="$2"; shift 2 ;;
        --matrix-type) MATRIX_TYPE="$2"; shift 2 ;;
        --tree-mode)   TREE_MODE_CLI="$2"; shift 2 ;;
        --label)       LABEL_IGNORED="$2"; shift 2 ;;   # accepted for CLI compatibility; ignored
        --force)       FORCE=1; shift ;;
        -h|--help)     usage ;;
        --list)        echo "[ERROR] --list is no longer supported in v3: the matrix universe is determined by the TC summary file; tc_manuscript_156_families.list is used only for family_filter_report annotation (see README)" >&2; exit 1 ;;
        *)             echo "[ERROR] unknown option: $1" >&2; usage ;;
    esac
done

[[ "$ACTION" == "run-all" ]] || { echo "[ERROR] expected the 'run-all' subcommand" >&2; usage; }
[[ -n "$SPECIES_ARG" ]]     || { echo "[ERROR] --species is required" >&2; exit 1; }
[[ -f "$SPECIES_ARG" ]]     || { echo "[ERROR] species file not found: $SPECIES_ARG" >&2; exit 1; }
[[ -n "$CONFIG_FILE" ]]     || { echo "[ERROR] --config is required" >&2; exit 1; }
[[ -f "$CONFIG_FILE" ]]     || { echo "[ERROR] config file not found: $CONFIG_FILE" >&2; exit 1; }
[[ -n "$OUTDIR_ARG" ]]      || { echo "[ERROR] --outdir is required" >&2; exit 1; }
[[ "$MATRIX_TYPE" == "tc" ]] || { echo "[ERROR] only --matrix-type tc is supported in v3" >&2; exit 1; }

# ---- config.yaml -> environment (exported env vars win over config settings) ----
# Relative paths resolve against SCRIPT_DIR; /path/to/your/* placeholders are skipped with a warning.
python3 -c 'import yaml' 2>/dev/null || {
    echo "[ERROR] PyYAML is required to read config.yaml (pip install pyyaml)" >&2
    exit 1
}
while IFS=$'\t' read -r env_kv; do
    [[ -n "${env_kv:-}" ]] || continue
    env_k="${env_kv%%=*}"
    case "$env_k" in ''|*[!A-Z0-9_]*) continue ;; esac
    export "${env_kv:?}"
done < <(python3 - "$CONFIG_FILE" "$SCRIPT_DIR" <<'PY'
import os, sys
import yaml

cfg_path, script_dir = sys.argv[1], sys.argv[2]
with open(cfg_path) as fh:
    cfg = yaml.safe_load(fh) or {}

def get(*path):
    node = cfg
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node

MAP = {
    ("database", "tc_matrix"):          "TC_MATRIX",
    ("database", "tc_description"):     "TC_DESC",
    ("database", "taxid_mapping"):      "TAXID_MAP",
    ("software", "python"):             "PYTHON_BIN",
    ("software", "mafft"):              "MAFFT_BIN",
    ("software", "cafe5"):              "CAFE5_BIN",
    ("software", "cafeplotter"):        "CAFEPLOTTER_BIN",
    ("analysis", "busco_root"):            "BUSCO_ROOT",
    ("analysis", "busco_lineage"):         "BUSCO_LINEAGE",
    ("analysis", "mcmctree_bin"):          "MCMCTREE_BIN",
    ("analysis", "iqtree_bin"):            "IQTREE_BIN",
    ("analysis", "trimal_bin"):            "TRIMAL_BIN",
    ("analysis", "amas_bin"):              "AMAS_BIN",
    ("analysis", "nw_reroot_bin"):         "NW_REROOT_BIN",
    ("parameters", "cafe_threads"):     "THREADS",
    ("parameters", "cafe2_jobs"):       "CAFE2_JOBS",
    ("parameters", "family_count_max"): "MAX_COUNT",
    ("parameters", "cafeplotter_format"): "FIG_FORMAT",
    ("parameters", "tree_mode"):          "TREE_MODE",
    ("parameters", "timetree_nwk"):     "TIMETREE_NWK",
    ("parameters", "deep_lambda_taxa"): "DEEP_LAMBDA_TAXA",
    ("parameters", "lambda1_restarts"): "LAMBDA1_RESTARTS",
}
# Only data/input file paths are resolved as relative; tool commands (PATH names or absolute) are used verbatim
PATH_KEYS = {"TC_MATRIX", "TC_DESC", "TAXID_MAP", "BUSCO_ROOT", "TIMETREE_NWK"}
BOOL_KEYS = {"LAMBDA1_RESTARTS"}

for path, env in MAP.items():
    if os.environ.get(env) is not None:
        continue
    val = get(*path)
    if val is None:
        continue
    val = str(val).strip()
    if not val:
        continue
    if val.startswith("/path/to/your"):
        print(f"[WARN] config {'.'.join(path)} is a placeholder ({val}); fill in a real path in config.yaml", file=sys.stderr)
        continue
    if env in BOOL_KEYS:
        val = "1" if val.lower() in ("1", "true", "yes", "on") else "0"
    if env in PATH_KEYS and not val.startswith(("/", "~")):
        val = os.path.join(script_dir, val)
    print(f"{env}={val}")
PY
)

# ---- Output directory: resolve to an absolute path before cd; products land under --outdir ----
abspath() { python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$1"; }
SPECIES_LIST="$(abspath "$SPECIES_ARG")"
CONFIG_FILE="$(abspath "$CONFIG_FILE")"
OUTDIR="$(abspath "$OUTDIR_ARG")"
mkdir -p "$OUTDIR"
cd "$OUTDIR"
WORKDIR="$(pwd)"
[[ -z "$LABEL_IGNORED" ]] || echo "[INFO] --label ignored (no web deployment in this package)"

# Input snapshot (provenance): config and species list are copied into the output directory
mkdir -p input
cp "$CONFIG_FILE" input/config.yaml
cp "$SPECIES_LIST" input/species.list

# tools (default: commands from PATH; config.yaml / env can override with absolute paths)
PYTHON_BIN="${PYTHON_BIN:-python3}"
CAFE5_BIN="${CAFE5_BIN:-cafe5}"
CAFEPLOTTER_BIN="${CAFEPLOTTER_BIN:-cafeplotter}"
AMAS_BIN="${AMAS_BIN:-AMAS.py}"
TRIMAL_BIN="${TRIMAL_BIN:-trimal}"
FAMSA_BIN="${FAMSA_BIN:-famsa}"
MAFFT_BIN="${MAFFT_BIN:-mafft}"
NW_REROOT_BIN="${NW_REROOT_BIN:-nw_reroot}"
MCMCTREE_BIN="${MCMCTREE_BIN:-mcmctree}"
# IQ-TREE: bioconda's iqtree=3.x is available as iqtree3/iqtree2/iqtree, auto-detected
if [[ -z "${IQTREE_BIN:-}" ]]; then
    IQTREE_BIN="iqtree"
    for _c in iqtree3 iqtree2 iqtree; do
        if command -v "$_c" >/dev/null 2>&1; then IQTREE_BIN="$_c"; break; fi
    done
fi

# inputs / required env
# Two BUSCO layouts are supported (see busco_run_dir):
#   busco_out/{Species}_auto/run_{lineage}   (odb12.2 batches)
#   busco_{lowercase_species}/run_{lineage}  (the layout of the bundled busco_odb10/)
# All species within one run must use the same lineage (BUSCO generations differ).
BUSCO_ROOT="${BUSCO_ROOT:-${SCRIPT_DIR}/data/busco_odb10}"
BUSCO_LINEAGE="${BUSCO_LINEAGE:-viridiplantae_odb10}"
# The three database files live next to this script (config.yaml relative paths resolve likewise)
TAXID_MAP="${TAXID_MAP:-${SCRIPT_DIR}/data/species_taxid_map.tsv}"
TC_MATRIX="${TC_MATRIX:-${SCRIPT_DIR}/data/cafe_input_matrix_26species.full.tsv}"
TC_DESC="${TC_DESC:-${SCRIPT_DIR}/data/tc_family_descriptions.tsv}"

# BUSCO extraction
MIN_TAXA_FRAC="${MIN_TAXA_FRAC:-0.5}"
INCLUDE_DUPLICATED="${INCLUDE_DUPLICATED:-1}"
MULTI_MODE="${MULTI_MODE:-longest}"

# alignment / IQ-TREE / MCMCtree
THREADS="${THREADS:-16}"
# Species-tree mode (priority: --tree-mode CLI > env > config.yaml > default):
#   ml (default; matches the manuscript) ModelFinder(MFP+MERGE) + ML topology search
[[ -n "${TREE_MODE_CLI:-}" ]] && export TREE_MODE="$TREE_MODE_CLI"
TREE_MODE="${TREE_MODE:-ml}"
case "$TREE_MODE" in
    ml|fixed) ;;
    *) echo "[ERROR] --tree-mode / TREE_MODE must be 'ml' or 'fixed' (got: $TREE_MODE)" >&2; exit 1 ;;
esac
IQTREE_MODEL="${IQTREE_MODEL:-LG+F+G4}"
PARTITION_MODEL="${PARTITION_MODEL:-LG+F+G4}"
MCMCTREE_PREFIX="${MCMCTREE_PREFIX:-plant_31_auto}"
MCMC_ITER="${MCMC_ITER:-20000,100,20000}"
MCMC_BDS="${MCMC_BDS:-1,1,0.5}"
MCMC_CLOCK="${MCMC_CLOCK:-IND}"
MCMC_UNIT_MYA="${MCMC_UNIT_MYA:-1000}"
MCMC_MAX_CAL="${MCMC_MAX_CAL:-12}"
MCMC_EXCLUDE_CHERRIES="${MCMC_EXCLUDE_CHERRIES:-1}"

# CAFE5 / plotting
MAX_COUNT="${MAX_COUNT:-100}"
DIFF_MAX="${DIFF_MAX:-}"
MIN_SPECIES_PRESENT="${MIN_SPECIES_PRESENT:-2}"
FIG_FORMAT="${FIG_FORMAT:-svg}"

# CAFE5 analysis (rate model selectable)
CAFE_RATE_MODEL="${CAFE_RATE_MODEL:-2lambda}"  # 2lambda | base
CAFE_TREE_SCALE="${CAFE_TREE_SCALE:-100}"     # MCMCtree units are 1000 Myr; x100 -> 10-Myr
CAFE2_REPS="${CAFE2_REPS:-30}"                 # multi-modal optimizer; restarts keep min -lnL
CAFE2_FINAL_REPS="${CAFE2_FINAL_REPS:-20}"     # fixed-error-stage restarts (kept >= joint-stage best)
CAFE2_JOBS="${CAFE2_JOBS:-4}"
CAFE2_ITER="${CAFE2_ITER:-5000}"
# lambda-1 robust-restart lane (s10d); 0 = skip
LAMBDA1_RESTARTS="${LAMBDA1_RESTARTS:-1}"
# lambda-2 group (required for CAFE_RATE_MODEL=2lambda): comma-separated species on the tree; the rest are lambda-1
DEEP_LAMBDA_TAXA="${DEEP_LAMBDA_TAXA:-Klebsormidium_nitens,Edaphochlamys_debaryana,Volvox_carteri_nagariensis}"
# Family filtering keeps a single rule: per-species count >= 100
# (applied at the 04_get_tc matrix-build stage). The custom max>=20 & max/min>=10 jump filter was removed:
# CAFE5 5.1.0 fixed-error model files are valid only on the estimating family set (any superset
# segfaults); estimation and testing must run on the same set.
# The >=100-filtered matrix (147 families x 26 species) is the model input.

# TimeTree / outgroup
TIMETREE_NWK="${TIMETREE_NWK:-}"
OUTGROUP_TAXA="${OUTGROUP_TAXA:-}"
OUTGROUP_MAX_FRACTION="${OUTGROUP_MAX_FRACTION:-0.4}"
SPECIES_ALIAS="${SPECIES_ALIAS:-${SCRIPT_DIR}/data/species_alias.tsv}"
TIMETREE_CACHE="${TIMETREE_CACHE:-${WORKDIR}/.timetree_pairwise_cache.json}"

# output dirs
LOG_DIR="logs"
D01="01_busco_by_gene_pseudo_sco"
D02="02_align_pseudo_sco"
D03="03_trim_pseudo_sco"
D04="04_concat_pseudo_sco"
D05="05_iqtree_pseudo_sco"
D06="06_mcmctree"
D07="07_cafe5"
D11="09_cafeplotter_figures"
D12="09_cafeplotter_figures_2"
D13="09_cafeplotter_figures_2lambda"
D14="09_cafeplotter_figures_1lambda"
DALIAS="alias_map"

CAFE_TREE="${D06}/${MCMCTREE_PREFIX}.mcmctree.ultrametric.nwk"

# ----------------------------- [2] helpers ----------------------------- #

log() {
    local level="$1"; shift
    printf '[%s] [%s] %s\n' "$(date '+%F %T')" "$level" "$*"
}

die() {
    log "ERROR" "$*"
    exit 1
}

check_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "command not found in PATH: $1"
}

check_exe_or_cmd() {
    local x="$1"
    if [[ "$x" == */* ]]; then
        [[ -x "$x" ]] || die "not executable: $x"
    else
        check_cmd "$x"
    fi
}

assert_input() {
    local f
    for f in "$@"; do
        [[ -e "$f" ]] || die "missing input: $f"
    done
}

assert_output() {
    local f
    for f in "$@"; do
        [[ -s "$f" ]] || die "expected output not produced or empty: $f"
    done
}

mkdirs() {
    mkdir -p "$LOG_DIR" "$D01" "$D02" "$D03" "$D04" "$D05" "$D06" "$D07" "$D11" "$D12" "$D13" "$D14" "$DALIAS"
}

run_step() {
    local id="$1"
    local name="$2"
    local func="$3"
    local done_file="${LOG_DIR}/${id}.done"
    local log_file="${LOG_DIR}/${id}.${name}.log"

    if [[ -f "$done_file" && -z "${FORCE:-}" ]]; then
        log "SKIP" "$id $name (sentinel exists)"
        return 0
    fi

    log "START" "$id $name"
    mkdir -p "$LOG_DIR"

    set +e
    "$func" >"$log_file" 2>&1
    local rc=$?
    set -e

    if [[ "$rc" -ne 0 ]]; then
        log "ERROR" "$id $name failed; see $log_file"
        exit "$rc"
    fi

    touch "$done_file"
    log "DONE" "$id $name"
}

# job.status.json: (status/current_step/updated_at/pipeline/message)
write_job_status() {  # $1=status $2=message
    "$PYTHON_BIN" - "$WORKDIR" "$1" "$2" <<'PY'
import json, os, sys
from datetime import datetime
outdir, status, message = sys.argv[1], sys.argv[2], sys.argv[3]
with open(os.path.join(outdir, "job.status.json"), "w", encoding="utf-8") as f:
    json.dump({"status": status, "current_step": "run_all",
               "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "pipeline": "BUSCO+IQ-TREE+MCMCtree+CAFE5 (v3)",
               "message": message}, f, indent=2, ensure_ascii=False)
PY
}

read_species_array() {
    mapfile -t SPECIES < <(awk 'NF && $1 !~ /^#/ {print $1}' "$SPECIES_LIST")
    [[ "${#SPECIES[@]}" -ge 2 ]] || die "species.list has fewer than 2 species: $SPECIES_LIST"
}

parse_outgroup_string_to_array() {
    local raw="$1"
    raw="${raw//,/ }"
    # shellcheck disable=SC2206
    OUTGROUP_ARRAY=($raw)
    [[ "${#OUTGROUP_ARRAY[@]}" -ge 1 ]] || die "OUTGROUP_TAXA is empty after parsing"
}

write_outgroup_conf_from_array() {
    local out="$1"
    shift
    {
        printf 'OUTGROUP_TAXA=('
        local x
        for x in "$@"; do
            printf '%q ' "$x"
        done
        printf ')\n'
    } > "$out"
}

validate_tree_tip_set() {
    local tree="$1"
    local species_list="$2"
    "$PYTHON_BIN" - "$tree" "$species_list" <<'PY'
import sys
from Bio import Phylo

tree_file, species_file = sys.argv[1], sys.argv[2]
tree = Phylo.read(tree_file, "newick")
tips = sorted(t.name for t in tree.get_terminals())
species = []
with open(species_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            species.append(line.split()[0])
species = sorted(set(species))
missing_in_tree = sorted(set(species) - set(tips))
extra_in_tree = sorted(set(tips) - set(species))
if missing_in_tree or extra_in_tree:
    print("[ERROR] tree tip set != species.list", file=sys.stderr)
    print("missing_in_tree:", missing_in_tree[:50], file=sys.stderr)
    print("extra_in_tree:", extra_in_tree[:50], file=sys.stderr)
    sys.exit(1)
print(f"[OK] tip set matched: {len(tips)} taxa")
PY
}

validate_root_bifurcating() {
    local tree="$1"
    "$PYTHON_BIN" - "$tree" <<'PY'
import sys
from Bio import Phylo
tree = Phylo.read(sys.argv[1], "newick")
n = len(tree.root.clades)
if n != 2:
    print(f"[ERROR] root is not bifurcating: {n} children", file=sys.stderr)
    sys.exit(1)
print("[OK] root is bifurcating")
PY
}

check_taxid_coverage() {
    "$PYTHON_BIN" - "$SPECIES_LIST" "$TAXID_MAP" <<'PY'
import sys
species_file, map_file = sys.argv[1], sys.argv[2]
species = []
with open(species_file) as f:
    for line in f:
        line=line.strip()
        if line and not line.startswith("#"):
            species.append(line.split()[0].replace("_"," ").lower())
mapping = set()
with open(map_file) as f:
    for i,line in enumerate(f,1):
        line=line.strip()
        if not line:
            continue
        if i == 1 and "TaxID" in line:
            continue
        parts=line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            mapping.add(" ".join(parts[:-1]).lower())
missing = [s for s in species if s not in mapping]
if missing:
    print("[ERROR] TAXID_MAP missing species:", file=sys.stderr)
    for x in missing[:100]:
        print("  " + x, file=sys.stderr)
    sys.exit(1)
print(f"[OK] TAXID_MAP covers all {len(species)} species")
PY
}

# BUSCO result-directory lookup (both layouts auto-detected):
#   {BUSCO_ROOT}/busco_out/{Species}_auto/run_{lineage}   odb12.2 batches
#   {BUSCO_ROOT}/busco_{lowercase}/run_{lineage}          odb10 batches (this project)
busco_run_dir() {
    local sp="$1" d
    d="${BUSCO_ROOT}/busco_out/${sp}_auto/run_${BUSCO_LINEAGE}"
    [[ -d "$d" ]] && { printf '%s\n' "$d"; return 0; }
    d="${BUSCO_ROOT}/busco_$(printf '%s' "$sp" | tr 'A-Z' 'a-z')/run_${BUSCO_LINEAGE}"
    [[ -d "$d" ]] && { printf '%s\n' "$d"; return 0; }
    return 1
}

assert_fig_format() {
    case "$FIG_FORMAT" in
        svg|png|both) ;;
        *) die "FIG_FORMAT must be svg, png, or both; got: $FIG_FORMAT" ;;
    esac
}

assert_plot_output() {
    local outdir="$1"
    if [[ "$FIG_FORMAT" == "both" ]]; then
        assert_output "${outdir}/summary_all_gene_family.svg" "${outdir}/summary_all_gene_family.png"
    else
        assert_output "${outdir}/summary_all_gene_family.${FIG_FORMAT}"
    fi
}

# ----------------------------- [3] steps ----------------------------- #

s00_sanity_check() {
    mkdirs
    assert_input "$SPECIES_LIST"

    [[ -n "$TAXID_MAP" ]] || die "TAXID_MAP is required"
    [[ -n "$TC_MATRIX" ]] || die "TC_MATRIX is required"
    [[ -n "$TC_DESC" ]] || die "TC_DESC is required"

    assert_input "$TAXID_MAP" "$TC_MATRIX" "$TC_DESC"
    [[ -d "$BUSCO_ROOT" ]] || die "BUSCO_ROOT not found: $BUSCO_ROOT"

    check_cmd "$PYTHON_BIN"
    check_cmd "$IQTREE_BIN"
    check_cmd "$TRIMAL_BIN"
    check_cmd "$AMAS_BIN"
    check_cmd "$NW_REROOT_BIN"
    check_cmd "$CAFE5_BIN"
    check_cmd "$CAFEPLOTTER_BIN"
    check_exe_or_cmd "$MCMCTREE_BIN"

    if command -v "$FAMSA_BIN" >/dev/null 2>&1; then
        log "INFO" "alignment backend: famsa"
    elif command -v "$MAFFT_BIN" >/dev/null 2>&1; then
        log "INFO" "alignment backend: mafft"
    else
        die "neither famsa nor mafft found"
    fi

    "$PYTHON_BIN" - <<'PY'
import requests
from Bio import Phylo
print("[OK] python modules: requests, Bio.Phylo")
PY

    for s in \
        "${SCRIPT_DIR}/scripts/01_fetch_timetree.py" \
        "${SCRIPT_DIR}/scripts/02_auto_extract_outgroup.py" \
        "${SCRIPT_DIR}/scripts/03_auto_mcmctree_from_timetree.py" \
        "${SCRIPT_DIR}/scripts/04_get_tc_code_number.py" \
        "${SCRIPT_DIR}/scripts/05_plot_cafe5_cafeplotter.py" \
        "${SCRIPT_DIR}/scripts/06_run_cafe_restarts.sh"
    do
        [[ -s "$s" ]] || die "required pipeline script missing: $s"
    done

    assert_fig_format
    read_species_array

    local total="${#SPECIES[@]}"
    local missing=0
    local sp run_dir
    for sp in "${SPECIES[@]}"; do
        if ! run_dir=$(busco_run_dir "$sp"); then
            log "WARN" "BUSCO run dir missing for ${sp} (run_${BUSCO_LINEAGE} under ${BUSCO_ROOT})"
            missing=$((missing + 1))
        fi
    done
    if (( missing * 100 > total * 20 )); then
        die "BUSCO directories missing for ${missing}/${total} species (>20%)"
    fi

    check_taxid_coverage

    log "INFO" "WORKDIR=$WORKDIR"
    log "INFO" "SCRIPT_DIR=$SCRIPT_DIR"
    log "INFO" "species=$total"
    log "INFO" "tree-mode=$TREE_MODE (ml=ModelFinder MFP+MERGE+ML topology; fixed=TimeTree topology+${IQTREE_MODEL} branch lengths)"
}

s01_extract_pseudo_sco() {
    rm -rf "$D01"
    mkdir -p "$D01"

    "$PYTHON_BIN" - "$SPECIES_LIST" "$BUSCO_ROOT" "$BUSCO_LINEAGE" "$D01" "$MIN_TAXA_FRAC" "$INCLUDE_DUPLICATED" "$MULTI_MODE" <<'PY'
import sys
from pathlib import Path
from collections import defaultdict

species_file, busco_root, lineage, outdir, min_taxa_frac, include_dup, multi_mode = sys.argv[1:8]
busco_root = Path(busco_root)
outdir = Path(outdir)
min_taxa_frac = float(min_taxa_frac)
include_dup = int(include_dup)
multi_mode = multi_mode.lower()

species = []
with open(species_file) as f:
    for line in f:
        line=line.strip()
        if line and not line.startswith("#"):
            species.append(line.split()[0])

def read_fasta(path):
    records = []
    name = None
    seq = []
    with open(path) as f:
        for line in f:
            line=line.rstrip("\n")
            if line.startswith(">"):
                if name is not None:
                    records.append((name, "".join(seq)))
                name = line[1:].strip().split()[0]
                seq = []
            else:
                seq.append(line.strip())
        if name is not None:
            records.append((name, "".join(seq)))
    return records

def find_busco_seq_dirs(run_dir):
    candidates = [
        run_dir / "busco_sequences",
        run_dir,
    ]
    single_dirs = []
    multi_dirs = []
    for base in candidates:
        s = base / "single_copy_busco_sequences"
        m = base / "multi_copy_busco_sequences"
        if s.is_dir():
            single_dirs.append(s)
        if m.is_dir():
            multi_dirs.append(m)
    return single_dirs, multi_dirs

families = defaultdict(dict)
selected_rows = []
missing_run = []

def find_run_dir(sp):
    # both layouts auto-detected: busco_out/{Sp}_auto (odb12.2) / busco_{lowercase} (odb10)
    for cand in (busco_root / "busco_out" / f"{sp}_auto",
                 busco_root / f"busco_{sp.lower()}"):
        d = cand / f"run_{lineage}"
        if d.is_dir():
            return d
    return None

for sp in species:
    run_dir = find_run_dir(sp)
    if run_dir is None:
        missing_run.append(sp)
        continue

    single_dirs, multi_dirs = find_busco_seq_dirs(run_dir)

    for d in single_dirs:
        for faa in sorted(d.glob("*.faa")):
            bid = faa.stem
            recs = read_fasta(faa)
            if not recs:
                continue
            rec = max(recs, key=lambda x: len(x[1]))
            families[bid][sp] = rec[1]
            selected_rows.append((bid, sp, "single", faa.name, rec[0], len(rec[1])))

    if include_dup:
        for d in multi_dirs:
            for faa in sorted(d.glob("*.faa")):
                bid = faa.stem
                if sp in families[bid]:
                    continue
                recs = read_fasta(faa)
                if not recs:
                    continue
                if multi_mode == "longest":
                    rec = max(recs, key=lambda x: len(x[1]))
                else:
                    rec = recs[0]
                families[bid][sp] = rec[1]
                selected_rows.append((bid, sp, "multi", faa.name, rec[0], len(rec[1])))

min_taxa = max(2, int(len(species) * min_taxa_frac + 0.999999))
kept = 0
removed = 0

with open(outdir / "selected_copies.tsv", "w") as f:
    f.write("busco_id\tspecies\tcopy_type\tfile\tseq_id\tlength\n")
    for row in selected_rows:
        f.write("\t".join(map(str,row)) + "\n")

with open(outdir / "summary.txt", "w") as f:
    f.write(f"species_total\t{len(species)}\n")
    f.write(f"missing_busco_run\t{len(missing_run)}\n")
    f.write(f"min_taxa_frac\t{min_taxa_frac}\n")
    f.write(f"min_taxa\t{min_taxa}\n")
    f.write(f"include_duplicated\t{include_dup}\n")
    f.write(f"multi_mode\t{multi_mode}\n")

for bid, sp2seq in sorted(families.items()):
    if len(sp2seq) < min_taxa:
        removed += 1
        continue
    with open(outdir / f"{bid}.faa", "w") as f:
        for sp in species:
            if sp in sp2seq:
                f.write(f">{sp}\n")
                seq = sp2seq[sp]
                for i in range(0, len(seq), 60):
                    f.write(seq[i:i+60] + "\n")
    kept += 1

with open(outdir / "summary.txt", "a") as f:
    f.write(f"families_seen\t{len(families)}\n")
    f.write(f"families_kept\t{kept}\n")
    f.write(f"families_removed_low_taxa\t{removed}\n")
    if missing_run:
        f.write("missing_species\t" + ",".join(missing_run) + "\n")

if kept == 0:
    raise SystemExit("[ERROR] no BUSCO pseudo-SCO families kept")
print(f"[OK] kept {kept} BUSCO pseudo-SCO families")
PY

    assert_output "${D01}/summary.txt"
    local n
    n=$(find "$D01" -name "*.faa" | wc -l)
    [[ "$n" -gt 0 ]] || die "s01 produced no *.faa"
}

s02_align() {
    rm -rf "$D02"
    mkdir -p "$D02"

    local aligner
    if command -v "$FAMSA_BIN" >/dev/null 2>&1; then
        aligner="famsa"
    else
        aligner="mafft"
    fi

    find "$D01" -maxdepth 1 -name "*.faa" -print0 \
      | xargs -0 --no-run-if-empty -I {} -n 1 -P "$THREADS" bash -c '
            set -euo pipefail
            in="$1"
            outdir="$2"
            aligner="$3"
            famsa_bin="$4"
            mafft_bin="$5"
            base="$(basename "$in" .faa)"
            out="${outdir}/${base}.aln.faa"
            if [[ "$aligner" == "famsa" ]]; then
                "$famsa_bin" "$in" "$out"
            else
                "$mafft_bin" --auto --thread 1 "$in" > "$out"
            fi
        ' _ {} "$D02" "$aligner" "$FAMSA_BIN" "$MAFFT_BIN"

    local in_n out_n
    in_n=$(find "$D01" -maxdepth 1 -name "*.faa" | wc -l)
    out_n=$(find "$D02" -maxdepth 1 -name "*.aln.faa" | wc -l)
    [[ "$out_n" -ge "$in_n" ]] || die "s02 alignment count too low: ${out_n}/${in_n}"
}

s03_trim() {
    rm -rf "$D03"
    mkdir -p "$D03"

    find "$D02" -maxdepth 1 -name "*.aln.faa" -print0 \
      | xargs -0 --no-run-if-empty -I {} -n 1 -P "$THREADS" bash -c '
            in="$1"
            outdir="$2"
            trimal_bin="$3"
            base="$(basename "$in" .aln.faa)"
            out="${outdir}/${base}.trim.faa"
            if [[ ! -s "$in" ]]; then
                echo "[WARN] skip empty alignment: $in" >&2
                exit 0
            fi
            if ! "$trimal_bin" -in "$in" -out "$out" -automated1 2>&1; then
                echo "[WARN] trimal failed: $in" >&2
                rm -f "$out"
                exit 0
            fi
        ' _ {} "$D03" "$TRIMAL_BIN"

    find "$D03" -maxdepth 1 -name "*.trim.faa" | sort > "${D03}/trim_list.txt"
    assert_output "${D03}/trim_list.txt"

    local in_n out_n
    in_n=$(find "$D02" -maxdepth 1 -name "*.aln.faa" -size +0c | wc -l)
    out_n=$(find "$D03" -maxdepth 1 -name "*.trim.faa" | wc -l)
    log "INFO" "s03 trimmed ${out_n}/${in_n} non-empty alignments"
}

s04_concat() {
    rm -rf "$D04"
    mkdir -p "$D04"

    assert_input "${D03}/trim_list.txt"

    mapfile -t trim_files < "${D03}/trim_list.txt"
    [[ "${#trim_files[@]}" -gt 0 ]] || die "trim_list.txt is empty"

    "$AMAS_BIN" concat \
      -i "${trim_files[@]}" \
      -f fasta \
      -d aa \
      -u phylip \
      -t "${D04}/busco_supermatrix.phy" \
      -p "${D04}/busco_partitions.txt"

    awk -v model="$PARTITION_MODEL" 'NF {print model", "$0}' \
      "${D04}/busco_partitions.txt" \
      > "${D04}/busco_partitions.iqtree.txt"

    assert_output \
      "${D04}/busco_supermatrix.phy" \
      "${D04}/busco_partitions.iqtree.txt"
}

s04b_species_alias() {
    rm -rf "$DALIAS"
    mkdir -p "$DALIAS"

    "$PYTHON_BIN" - "$SPECIES_LIST" "$SPECIES_ALIAS" \
        "${DALIAS}/species_timetree.list" "${DALIAS}/alias_map.tsv" <<'PY'
import sys
from pathlib import Path

species_file, alias_file, out_list, out_map = sys.argv[1:5]

species = []
with open(species_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            species.append(line.split()[0])

alias_pairs = []
apath = Path(alias_file) if alias_file else None
if apath and apath.is_file() and apath.stat().st_size > 0:
    seen_originals = set()
    seen_timetree = {}
    for raw in apath.read_text().splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            print(f"[warn] alias line ignored (expected 2 tab-separated cols): {raw!r}", file=sys.stderr)
            continue
        original, timetree = parts[0].strip(), parts[1].strip()
        if not original or not timetree:
            print(f"[warn] alias line has empty field: {raw!r}", file=sys.stderr)
            continue
        if original not in species:
            print(f"[warn] alias references species not in species.list, ignored: {original}", file=sys.stderr)
            continue
        if original in seen_originals:
            print(f"[warn] alias duplicate original, ignored: {original}", file=sys.stderr)
            continue
        if timetree in seen_timetree:
            print(f"[warn] alias maps two originals to same timetree name, ignored: {original} -> {timetree} (already mapped from {seen_timetree[timetree]})", file=sys.stderr)
            continue
        seen_originals.add(original)
        seen_timetree[timetree] = original
        alias_pairs.append((original, timetree))

fwd = dict(alias_pairs)
mapped = [fwd.get(s, s) for s in species]

with open(out_list, "w") as f:
    for s in mapped:
        f.write(s + "\n")

with open(out_map, "w") as f:
    for original, timetree in alias_pairs:
        f.write(f"{original}\t{timetree}\n")

print(f"[ok] species_timetree.list: {len(mapped)} species; alias pairs: {len(alias_pairs)}")
PY

    assert_output "${DALIAS}/species_timetree.list"
    # alias_map.tsv is non-empty only when an aliased species is actually
    # present in species.list; otherwise aliasing is legitimately unused for
    # this run (s05 also guards it via [[ -s ]]). Assert only in the former
    # case, so we still catch a real alias failure rather than an alias file
    # that merely references species not in the current species.list.
    local alias_in_list=0
    if [[ -s "$SPECIES_ALIAS" ]]; then
        awk -F'\t' 'NR==FNR{if($1!~/^#/)sp[$1]=1;next} $1 in sp{f=1} END{exit !f}' \
            "$SPECIES_LIST" "$SPECIES_ALIAS" && alias_in_list=1
    fi
    if [[ "$alias_in_list" == 1 ]]; then
        assert_output "${DALIAS}/alias_map.tsv"
    fi
}

s05_timetree_iqtree() {
    rm -rf "$D05"
    mkdir -p "$D05"

    assert_input \
      "${D04}/busco_supermatrix.phy" \
      "${D04}/busco_partitions.iqtree.txt"

    local tt_species_list="$SPECIES_LIST"
    if [[ -s "${DALIAS}/species_timetree.list" ]]; then
        tt_species_list="${DALIAS}/species_timetree.list"
        log "INFO" "using aliased species list for TimeTree: $tt_species_list"
    fi

    if [[ -n "${TIMETREE_NWK:-}" ]]; then
        assert_input "$TIMETREE_NWK"
        cp "$TIMETREE_NWK" "${D05}/timetree.nwk"
    else
        "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/01_fetch_timetree.py" \
          "$tt_species_list" \
          -o "${D05}/timetree.nwk"
    fi

    if [[ -s "${DALIAS}/alias_map.tsv" ]]; then
        "$PYTHON_BIN" - "${D05}/timetree.nwk" "${DALIAS}/alias_map.tsv" <<'PY'
import sys, re
from pathlib import Path

tree_path, map_path = sys.argv[1], sys.argv[2]
text = Path(tree_path).read_text()

rev = {}
for raw in Path(map_path).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split("\t")
    if len(parts) != 2:
        continue
    timetree, original = parts[1].strip(), parts[0].strip()
    rev[timetree] = original

n = 0
for timetree_name in sorted(rev.keys(), key=len, reverse=True):
    pattern = re.compile(rf"(?<=[(,]){re.escape(timetree_name)}(?=[\):,])")
    text, k = pattern.subn(rev[timetree_name], text)
    n += k

Path(tree_path).write_text(text)
print(f"[ok] renamed {n} timetree tip(s) back to original species names")
PY
    fi

    # The TimeTree online API sometimes expands species names into subspecies/variety tips
    # (e.g. Oryza_satta -> Oryza_sativa_Japonica_Group + ..._Indica_Group; Acorus_calamus -> 3 var.),
    # which breaks tip-set validation. Collapse the tree back to species level: assign each
    # tip to "the species with the exact same name or the longest species-name prefix"
    # (ties: longest name); keep the exact tip when present and prune extra expanded tips;
    # otherwise keep the first sorted representative and rename it to the species name.
    "$PYTHON_BIN" - "${D05}/timetree.nwk" "$SPECIES_LIST" <<'PY'
import sys
from Bio import Phylo

tree_path, species_file = sys.argv[1], sys.argv[2]
species = []
with open(species_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            species.append(line.split()[0])
species_set = set(species)

tree = Phylo.read(tree_path, "newick")
tip_names = sorted(t.name for t in tree.get_terminals())

assign = {}
for t in tip_names:
    for s in sorted(species_set, key=len, reverse=True):
        if t == s or t.startswith(s + "_") or t.startswith(s + " "):
            assign.setdefault(s, []).append(t)
            break

missing = [s for s in species if s not in assign]
if missing:
        print("[ERROR] These species were not found in the TimeTree tree (incl. subspecies/variety expansions): "
          + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

drop, rename = [], {}
for s, cands in assign.items():
    if s in cands:
        drop.extend(c for c in cands if c != s)
        continue
    rename[cands[0]] = s
    drop.extend(cands[1:])

for name in drop:
    tree.prune(name)
for t in tree.get_terminals():
    if t.name in rename:
        t.name = rename[t.name]

tips = sorted(t.name for t in tree.get_terminals())
if tips != sorted(species):
            print(f"[ERROR] tip set still mismatches species.list after collapsing: {tips}", file=sys.stderr)
    sys.exit(1)

Phylo.write(tree, tree_path, "newick", plain=True,
            format_branch_length="%1.10g")
print(f"[ok] TimeTree tree collapsed to species level: {len(tips)} tips "
      f"(renamed {len(rename)}, pruned {len(drop)})")
PY

    assert_output "${D05}/timetree.nwk"
    validate_tree_tip_set "${D05}/timetree.nwk" "$SPECIES_LIST"
    validate_root_bifurcating "${D05}/timetree.nwk"

    if [[ -n "${OUTGROUP_TAXA:-}" ]]; then
        parse_outgroup_string_to_array "$OUTGROUP_TAXA"
        write_outgroup_conf_from_array "${D05}/outgroup_taxa.conf" "${OUTGROUP_ARRAY[@]}"
    else
        "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/02_auto_extract_outgroup.py" \
          "${D05}/timetree.nwk" \
          "${D05}/outgroup_taxa.conf" \
          "$SPECIES_LIST"
        unset OUTGROUP_TAXA || true
        # shellcheck disable=SC1090
        source "${D05}/outgroup_taxa.conf"
        if declare -p OUTGROUP_TAXA 2>/dev/null | grep -q 'declare \-[aA]'; then
            OUTGROUP_ARRAY=("${OUTGROUP_TAXA[@]}")
        else
            parse_outgroup_string_to_array "${OUTGROUP_TAXA:-}"
        fi
    fi

    [[ "${#OUTGROUP_ARRAY[@]}" -ge 1 ]] || die "failed to determine OUTGROUP_ARRAY"
    log "INFO" "OUTGROUP_ARRAY=${OUTGROUP_ARRAY[*]}"

    # tree-mode branch: ml = ModelFinder + partition merging + ML topology search.
    # The TimeTree tree feeds outgroup choice and s06 calibration.
    if [[ "$TREE_MODE" == "ml" ]]; then
        log "INFO" "tree-mode=ml: IQ-TREE ModelFinder (-m MFP+MERGE) + ML topology search"
        "$IQTREE_BIN" \
          -s "${D04}/busco_supermatrix.phy" \
          -p "${D04}/busco_partitions.iqtree.txt" \
          -m MFP+MERGE \
          -T "$THREADS" \
          --prefix "${D05}/busco"
    else
        log "INFO" "tree-mode=fixed: fixed TimeTree topology (-te), ${IQTREE_MODEL} branch lengths"
        "$IQTREE_BIN" \
          -s "${D04}/busco_supermatrix.phy" \
          -p "${D04}/busco_partitions.iqtree.txt" \
          -m "$IQTREE_MODEL" \
          -te "${D05}/timetree.nwk" \
          -T "$THREADS" \
          --prefix "${D05}/busco"
    fi

    assert_output "${D05}/busco.treefile"

    "$NW_REROOT_BIN" \
      "${D05}/busco.treefile" \
      "${OUTGROUP_ARRAY[@]}" \
      > "${D05}/busco.rooted.treefile"

    assert_output "${D05}/busco.rooted.treefile"
    validate_tree_tip_set "${D05}/busco.rooted.treefile" "$SPECIES_LIST"
    validate_root_bifurcating "${D05}/busco.rooted.treefile"
}

s06_mcmctree_dating() {
    rm -rf "$D06"
    mkdir -p "$D06"

    assert_input \
      "${D05}/busco.rooted.treefile" \
      "${D04}/busco_supermatrix.phy" \
      "${D04}/busco_partitions.iqtree.txt" \
      "$TAXID_MAP"

    local exclude_arg=()
    if [[ "$MCMC_EXCLUDE_CHERRIES" == "1" || "$MCMC_EXCLUDE_CHERRIES" == "true" ]]; then
        exclude_arg=(--exclude-cherries)
    fi

    "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/03_auto_mcmctree_from_timetree.py" \
      --tree "${D05}/busco.rooted.treefile" \
      --taxid-map "$TAXID_MAP" \
      --outdir "$D06" \
      --alignment "${D04}/busco_supermatrix.phy" \
      --partition "${D04}/busco_partitions.iqtree.txt" \
      --iqtree-bin "$IQTREE_BIN" \
      --mcmctree-bin "$MCMCTREE_BIN" \
      --prefix "$MCMCTREE_PREFIX" \
      --mcmc-iter "$MCMC_ITER" \
      --mcmc-bds "$MCMC_BDS" \
      --mcmc-clock "$MCMC_CLOCK" \
      --unit-mya "$MCMC_UNIT_MYA" \
      --max-calibrations "$MCMC_MAX_CAL" \
      --cache "$TIMETREE_CACHE" \
      "${exclude_arg[@]}" \
      --force

    assert_output \
      "${D06}/calibrated_mcmctree_tree.nwk" \
      "${D06}/timetree_node_calibrations.tsv"

    grep -q "'" "${D06}/calibrated_mcmctree_tree.nwk" \
      || die "calibrated_mcmctree_tree.nwk has no MCMCtree calibration labels"

    (
        cd "$D06"
        "$IQTREE_BIN" \
          -s "../${D04}/busco_supermatrix.phy" \
          -p "../${D04}/busco_partitions.iqtree.txt" \
          -te "calibrated_mcmctree_tree.nwk" \
          --dating mcmctree \
          --mcmc-iter "$MCMC_ITER" \
          --mcmc-bds "$MCMC_BDS" \
          --mcmc-clock "$MCMC_CLOCK" \
          -T "$THREADS" \
          --prefix "$MCMCTREE_PREFIX"
    )

    assert_output \
      "${D06}/${MCMCTREE_PREFIX}.mcmctree.ctl" \
      "${D06}/${MCMCTREE_PREFIX}.mcmctree.hessian" \
      "${D06}/${MCMCTREE_PREFIX}.dummy.phy" \
      "${D06}/${MCMCTREE_PREFIX}.rooted.nwk"

    (
        cd "$D06"
        "$MCMCTREE_BIN" "${MCMCTREE_PREFIX}.mcmctree.ctl"
    )

    assert_output "${D06}/FigTree.tre"

    "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/03_auto_mcmctree_from_timetree.py" \
      --extract-only \
      --figtree "${D06}/FigTree.tre" \
      --clean-newick "$CAFE_TREE"

    assert_output "$CAFE_TREE"
}

s07_make_tc_matrix() {
    mkdir -p "$D07"
    rm -f "${D07}/cafe_input_matrix.tsv" "${D07}/cafe_input_matrix.removed.tsv"

    assert_input "$SPECIES_LIST" "$TC_MATRIX" "$TC_DESC"

    local args=(
      "${SCRIPT_DIR}/scripts/04_get_tc_code_number.py"
      --species "$SPECIES_LIST"
      --desc "$TC_DESC"
      --matrix "$TC_MATRIX"
      --output "${D07}/cafe_input_matrix.tsv"
      --max-count "$MAX_COUNT"
      --removed-output "${D07}/cafe_input_matrix.removed.tsv"
    )

    if [[ -n "${DIFF_MAX:-}" ]]; then
        args+=(--diff-max "$DIFF_MAX")
    fi

    "$PYTHON_BIN" "${args[@]}"

    assert_output "${D07}/cafe_input_matrix.tsv"
}

s08_filter_cafe_input() {
    assert_input "${D07}/cafe_input_matrix.tsv"
    rm -f "${D07}/cafe_input_matrix.filtered.tsv"

    awk -v min_present="$MIN_SPECIES_PRESENT" 'BEGIN{FS=OFS="\t"}
        NR==1 {print; next}
        {
            present=0
            for (i=2; i<=NF; i++) {
                if (($i+0) > 0) present++
            }
            if (present >= min_present) print
        }
    ' "${D07}/cafe_input_matrix.tsv" > "${D07}/cafe_input_matrix.filtered.tsv"

    assert_output "${D07}/cafe_input_matrix.filtered.tsv"
}

# Family-filter ledger: record the fate of every TC family present in this species set
# (removed by s07 max-count / removed by s08 presence filter / entered the final CAFE model)
# and whether it belongs to tc_manuscript_156_families.list (the manuscript's 156-family set).
s08b_family_filter_report() {
    assert_input \
        "${D07}/cafe_input_matrix.tsv" \
        "${D07}/cafe_input_matrix.removed.tsv" \
        "${D07}/cafe_input_matrix.filtered.tsv"

    local list156="${SCRIPT_DIR}/data/tc_manuscript_156_families.list"
    [[ -s "$list156" ]] || list156=""

    "$PYTHON_BIN" - \
        "${D07}/cafe_input_matrix.tsv" \
        "${D07}/cafe_input_matrix.removed.tsv" \
        "${D07}/cafe_input_matrix.filtered.tsv" \
        "${D07}/family_filter_report.tsv" \
        "$MIN_SPECIES_PRESENT" \
        "$list156" <<'PY'
import sys

matrix_f, removed_f, filtered_f, out_f, min_present, list156_f = sys.argv[1:7]
min_present = int(min_present)

def read_rows(path):
    rows = {}
    with open(path) as fh:
        fh.readline()  # header
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            rows[parts[1]] = parts  # FamilyID -> row
    return rows

    matrix  = read_rows(matrix_f)    # kept by s07 (with per-species counts)
    removed = read_rows(removed_f)   # removed by s07 max-count: Desc, ID, max_count, reason
    filt    = read_rows(filtered_f)  # families that entered the model after s08

fam156 = set()
if list156_f:
    with open(list156_f) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                fam156.add(line)

universe = sorted(set(matrix) | set(removed))
n_model = n_s07 = n_s08 = 0
with open(out_f, "w") as out:
    out.write("FamilyID\tDesc\tn_species_present\tmax_count\tin_final_model\t"
              "removed_at\treason\tin_156_list\n")
    for fam in universe:
        if fam in removed:
            row = removed[fam]
            desc, maxc = row[0], row[2]
            reason = row[3] if len(row) > 3 else ""
            stage, present = "s07_max_count", "NA"
            n_s07 += 1
        elif fam in matrix:
            row = matrix[fam]
            desc = row[0]
            try:
                counts = [int(x) for x in row[2:]]
                present = str(sum(1 for c in counts if c > 0))
                maxc = str(max(counts))
            except ValueError:
                counts, present, maxc = [], "NA", "NA"
            if fam in filt:
                stage, reason = "kept", ""
                n_model += 1
            else:
                stage = "s08_min_species_present"
                reason = f"present<{min_present}"
                n_s08 += 1
        else:
            desc, present, maxc, stage, reason = "(null)", "NA", "NA", "unknown", ""
        out.write("\t".join([fam, desc, present, maxc,
                             "y" if stage == "kept" else "n", stage, reason,
                             "y" if fam in fam156 else "n"]) + "\n")

total = len(universe)
print(f"[ok] family_filter_report: universe={total} "
      f"(in 156-list: {sum(1 for f in universe if f in fam156)}/{len(fam156) or 0}), "
      f"in_model={n_model}, removed_s07={n_s07}, removed_s08={n_s08} -> {out_f}")
PY

    assert_output "${D07}/family_filter_report.tsv"
}

s09_run_cafe_base() {
    rm -rf "${D07}/run_base"
    mkdir -p "${D07}/run_base"

    assert_input "$CAFE_TREE" "${D07}/cafe_input_matrix.filtered.tsv"

    "$CAFE5_BIN" \
      -t "$CAFE_TREE" \
      -i "${D07}/cafe_input_matrix.filtered.tsv" \
      -p \
      -e \
      -o "${D07}/run_base"

    assert_output "${D07}/run_base/Base_error_model.txt"
}

s10_run_cafe_base2() {
    rm -rf "${D07}/run_base2"
    mkdir -p "${D07}/run_base2"

    assert_input \
      "$CAFE_TREE" \
      "${D07}/cafe_input_matrix.filtered.tsv" \
      "${D07}/run_base/Base_error_model.txt"

    "$CAFE5_BIN" \
      -t "$CAFE_TREE" \
      -i "${D07}/cafe_input_matrix.filtered.tsv" \
      -p \
      -e"${D07}/run_base/Base_error_model.txt" \
      -o "${D07}/run_base2"

    assert_output "${D07}/run_base2/Base_family_results.txt"
}

# Benjamini-Hochberg correction of the per-family CAFE5 p-values;
# writes Base_family_results.fdr.tsv next to the original (CAFE output untouched):
#   #FamilyID  pvalue  BH_FDR  sig_pvalue_0.05  sig_FDR_0.05
make_family_fdr() {
    local cafe_dir="$1"

    "$PYTHON_BIN" - "$cafe_dir" <<'PY'
import sys, os
from scipy.stats import false_discovery_control

cafe_dir = sys.argv[1]
src = os.path.join(cafe_dir, "Base_family_results.txt")
out = os.path.join(cafe_dir, "Base_family_results.fdr.tsv")
if not os.path.isfile(src):
    sys.exit(f"[ERROR] missing {src}")

def fmt(x):
    if x == 0:
        return "0"
    return f"{x:.4g}"

fams, p_raw, p_float, orig_sig = [], [], [], []
with open(src) as fh:
    fh.readline()  # header: #FamilyID<TAB>pvalue<TAB>Significant at 0.05
    for line in fh:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            p = float(parts[1])
        except ValueError:
            continue
        fams.append(parts[0])
        p_raw.append(parts[1])
        p_float.append(p)
        orig_sig.append(parts[2].strip().lower().startswith("y")
                        if len(parts) > 2 else (p < 0.05))

if not fams:
    sys.exit(f"[ERROR] no families parsed from {src}")

q = [min(1.0, float(v)) for v in false_discovery_control(p_float, method="bh")]

with open(out, "w") as fh:
    fh.write("#FamilyID\tpvalue\tBH_FDR\tsig_pvalue_0.05\tsig_FDR_0.05\n")
    for fam, raw, qq, s in zip(fams, p_raw, q, orig_sig):
        fh.write(f"{fam}\t{raw}\t{fmt(qq)}\t{'y' if s else 'n'}\t{'y' if qq < 0.05 else 'n'}\n")

print(f"[ok] {os.path.basename(cafe_dir)}: {len(fams)} families, "
      f"FDR<0.05: {sum(1 for qq in q if qq < 0.05)}")
PY
}

s10b_family_fdr() {
    assert_input \
      "${D07}/run_base/Base_family_results.txt" \
      "${D07}/run_base2/Base_family_results.txt"

    make_family_fdr "${D07}/run_base"
    make_family_fdr "${D07}/run_base2"

    assert_output \
      "${D07}/run_base/Base_family_results.fdr.tsv" \
      "${D07}/run_base2/Base_family_results.fdr.tsv"
}

# 2-lambda model: a separate rate category for the deep lineages (DEEP_LAMBDA_TAXA).
# With a single lambda on this dataset, lambda hits the 1/longest-branch boundary and
# epsilon drifted to ~0.45; with two rates epsilon is ~0.17 and -lnL improves markedly.
# The optimizer is multi-modal: the helper script restarts CAFE2_REPS times, keeps the best -lnL, then
s10c_run_cafe_2lambda() {
    assert_input \
      "$CAFE_TREE" \
      "${D07}/cafe_input_matrix.filtered.tsv" \
      "${SCRIPT_DIR}/scripts/06_run_cafe_restarts.sh"

    if [[ "$CAFE_RATE_MODEL" == "2lambda" && -z "$DEEP_LAMBDA_TAXA" ]]; then
        log "ERROR" "CAFE_RATE_MODEL=2lambda requires DEEP_LAMBDA_TAXA (lambda-2 group, comma-separated; the rest are lambda-1)"
        exit 1
    fi

    # lambda-2 group resolution: keep only DEEP_LAMBDA_TAXA taxa present on the current tree;
    # if none are on the tree, fall back to the s05 auto-detected outgroup (the deepest clade).
    # On resume, s05 may be skipped by sentinels, so the outgroup is restored from the config.
    if [[ "$CAFE_RATE_MODEL" == "2lambda" ]]; then
        if [[ -z "${OUTGROUP_ARRAY[*]:-}" ]]; then
            assert_input "${D05}/outgroup_taxa.conf"
            # shellcheck disable=SC1090
            source "${D05}/outgroup_taxa.conf"
            if declare -p OUTGROUP_TAXA 2>/dev/null | grep -q 'declare \-[aA]'; then
                OUTGROUP_ARRAY=("${OUTGROUP_TAXA[@]}")
            else
                parse_outgroup_string_to_array "${OUTGROUP_TAXA:-}"
            fi
        fi
        DEEP_LAMBDA_TAXA="$("$PYTHON_BIN" - "$CAFE_TREE" "$DEEP_LAMBDA_TAXA" \
            ${OUTGROUP_ARRAY[@]+"${OUTGROUP_ARRAY[@]}"} <<'PY'
import sys
from Bio import Phylo

tips = {t.name for t in Phylo.read(sys.argv[1], "newick").get_terminals()}
requested = [x for x in sys.argv[2].split(",") if x]
on_tree = [x for x in requested if x in tips]
dropped = [x for x in requested if x not in tips]
if dropped:
            print(f"[INFO] dropped from DEEP_LAMBDA_TAXA (not on tree): {','.join(dropped)}", file=sys.stderr)
if not on_tree:
    on_tree = [x for x in sys.argv[3:] if x in tips]
    if on_tree:
            print(f"[INFO] lambda-2 group fell back to the auto-detected outgroup (deep clade): {','.join(on_tree)}",
              file=sys.stderr)
print(",".join(on_tree))
PY
        )"
        [[ -n "$DEEP_LAMBDA_TAXA" ]] || {
            log "ERROR" "cannot determine the lambda-2 group: neither the given group nor the outgroup is on the tree"
            exit 1
        }
        log "INFO" "lambda-2 group (resolved): $DEEP_LAMBDA_TAXA"
    fi

    TREE_RAW="$CAFE_TREE" \
    MATRIX_RAW="${D07}/cafe_input_matrix.filtered.tsv" \
    OUT="${D07}/run_2lambda" \
    SCALE="$CAFE_TREE_SCALE" \
    RATE_MODEL="$CAFE_RATE_MODEL" \
    REPS="$CAFE2_REPS" \
    FINAL_REPS="$CAFE2_FINAL_REPS" \
    JOBS="$CAFE2_JOBS" \
    ITER="$CAFE2_ITER" \
    MCMCTREE_PREFIX="$MCMCTREE_PREFIX" \
    DEEP_LAMBDA_TAXA="$DEEP_LAMBDA_TAXA" \
    CAFE5_BIN="$CAFE5_BIN" \
    PYTHON_BIN="$PYTHON_BIN" \
      bash "${SCRIPT_DIR}/scripts/06_run_cafe_restarts.sh"

    assert_output "${D07}/run_2lambda/final/Base_family_results.fdr.tsv"
}

run_plotter() {
    local cafe_dir="$1"
    local outdir="$2"

    rm -rf "$outdir"

    if [[ "$FIG_FORMAT" == "both" ]]; then
        "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/05_plot_cafe5_cafeplotter.py" \
          --cafe-dir "$cafe_dir" \
          --outdir "$outdir" \
          --format svg
        "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/05_plot_cafe5_cafeplotter.py" \
          --cafe-dir "$cafe_dir" \
          --outdir "$outdir" \
          --format png
    else
        "$PYTHON_BIN" "${SCRIPT_DIR}/scripts/05_plot_cafe5_cafeplotter.py" \
          --cafe-dir "$cafe_dir" \
          --outdir "$outdir" \
          --format "$FIG_FORMAT"
    fi
}

s11_plot_run_base() {
    assert_input "${D07}/run_base/Base_asr.tre"
    run_plotter "${D07}/run_base" "$D11"
    assert_plot_output "$D11"
}

s12_plot_run_base2() {
    assert_input "${D07}/run_base2/Base_asr.tre"
    run_plotter "${D07}/run_base2" "$D12"
    assert_plot_output "$D12"
}

s12b_plot_2lambda() {
    assert_input "${D07}/run_2lambda/final/Base_asr.tre"
    run_plotter "${D07}/run_2lambda/final" "$D13"
    assert_plot_output "$D13"
}

# lambda-1 robust-restart lane: the same restart protocol as 2lambda
# (REPS joint restarts -> fix the error model -> FINAL_REPS restarts, keep the best),
# but with a single global rate (RATE_MODEL=base, no -y two-rate grouping).
# Avoids the poor local optimum where lambda hits the 1/longest-branch boundary and epsilon inflates.
s10d_run_cafe_1lambda() {
    assert_input \
      "$CAFE_TREE" \
      "${D07}/cafe_input_matrix.filtered.tsv" \
      "${SCRIPT_DIR}/scripts/06_run_cafe_restarts.sh"

    TREE_RAW="$CAFE_TREE" \
    MATRIX_RAW="${D07}/cafe_input_matrix.filtered.tsv" \
    OUT="${D07}/run_1lambda" \
    SCALE="$CAFE_TREE_SCALE" \
    RATE_MODEL="base" \
    REPS="$CAFE2_REPS" \
    FINAL_REPS="$CAFE2_FINAL_REPS" \
    JOBS="$CAFE2_JOBS" \
    ITER="$CAFE2_ITER" \
    MCMCTREE_PREFIX="$MCMCTREE_PREFIX" \
    DEEP_LAMBDA_TAXA="" \
    CAFE5_BIN="$CAFE5_BIN" \
    PYTHON_BIN="$PYTHON_BIN" \
      bash "${SCRIPT_DIR}/scripts/06_run_cafe_restarts.sh"

    assert_output "${D07}/run_1lambda/final/Base_family_results.fdr.tsv"
}

s12c_plot_1lambda() {
    assert_input "${D07}/run_1lambda/final/Base_asr.tre"
    run_plotter "${D07}/run_1lambda/final" "$D14"
    assert_plot_output "$D14"
}

# s13: results/ view with familiar directory names (final artifacts of the four models)
s13_collect_results() {
    local r="results"
    rm -rf "$r"
    mkdir -p "${r}/03_ultrametric_tree" "${r}/04_cafe_input" \
             "${r}/05_cafe5_output" "${r}/06_figures"

    cp "$CAFE_TREE" "${r}/03_ultrametric_tree/r8s.ultrametric.txt"
    cp "${D07}/cafe_input_matrix.filtered.tsv" "${r}/04_cafe_input/tc.filtered.tsv"
    [[ -s "${D07}/family_filter_report.tsv" ]] && \
        cp "${D07}/family_filter_report.tsv" "${r}/04_cafe_input/"

    cp -a "${D07}/run_base"  "${r}/05_cafe5_output/cafe5_base"
    cp -a "${D07}/run_base2" "${r}/05_cafe5_output/cafe5_k1"

    # 2lambda / lambda-1 restart dirs keep only final + the three summary tables (rep*/ are large)
    local m f
    for m in 2lambda 1lambda; do
        [[ -d "${D07}/run_${m}/final" ]] || continue
        cp -a "${D07}/run_${m}/final" "${r}/05_cafe5_output/cafe5_${m}"
        for f in rate_params.tsv summary.tsv final_reps.summary.tsv; do
            [[ -s "${D07}/run_${m}/${f}" ]] && \
                cp "${D07}/run_${m}/${f}" "${r}/05_cafe5_output/cafe5_${m}/"
        done
    done

    cp -a "$D11" "${r}/06_figures/cafe5_base_plot"
    cp -a "$D12" "${r}/06_figures/cafe5_k1_plot"
    [[ -d "$D13" ]] && cp -a "$D13" "${r}/06_figures/cafe5_2lambda_plot"
    [[ -d "$D14" ]] && cp -a "$D14" "${r}/06_figures/cafe5_1lambda_plot"

    log "INFO" "results/ view collected -> ${WORKDIR}/results"
}

# ----------------------------- [4] main ----------------------------- #

main() {
    mkdirs

    if [[ -n "${FORCE:-}" ]]; then
        log "INFO" "--force enabled: removing sentinels only"
        rm -f "${LOG_DIR}"/s*.done
    fi

    run_step s00 sanity_check s00_sanity_check
    run_step s01 extract_pseudo_sco s01_extract_pseudo_sco
    run_step s02 align s02_align
    run_step s03 trim s03_trim
    run_step s04 concat s04_concat
    run_step s04b species_alias s04b_species_alias
    run_step s05 timetree_iqtree s05_timetree_iqtree
    run_step s06 mcmctree_dating s06_mcmctree_dating
    run_step s07 make_tc_matrix s07_make_tc_matrix
    run_step s08 filter_cafe_input s08_filter_cafe_input
    run_step s08b family_filter_report s08b_family_filter_report
    run_step s09 run_cafe_base s09_run_cafe_base
    run_step s10 run_cafe_base2 s10_run_cafe_base2
    run_step s10b family_fdr s10b_family_fdr
    # SKIP_2LAMBDA=1 skips the 2-lambda analysis and its plots; base/single-rate results are
    # unaffected (saves the 20+10 restart runs)
    if [[ "${SKIP_2LAMBDA:-0}" == "1" ]]; then
        log "INFO" "SKIP_2LAMBDA=1: skipping s10c (cafe_2lambda) and s12b (plot_2lambda)"
    else
        run_step s10c cafe_2lambda s10c_run_cafe_2lambda
    fi
    if [[ "${LAMBDA1_RESTARTS:-1}" != "1" ]]; then
        log "INFO" "LAMBDA1_RESTARTS!=1: skipping s10d (cafe_1lambda) and s12c (plot_1lambda)"
    else
        run_step s10d cafe_1lambda s10d_run_cafe_1lambda
    fi
    run_step s11 plot_run_base s11_plot_run_base
    run_step s12 plot_run_base2 s12_plot_run_base2
    if [[ "${SKIP_2LAMBDA:-0}" != "1" ]]; then
        run_step s12b plot_2lambda s12b_plot_2lambda
    fi
    if [[ "${LAMBDA1_RESTARTS:-1}" == "1" ]]; then
        run_step s12c plot_1lambda s12c_plot_1lambda
    fi
    run_step s13 collect_results s13_collect_results

    log "DONE" "Pipeline finished"
    log "RESULT" "CAFE5 run_base:  ${D07}/run_base"
    log "RESULT" "CAFE5 run_base2: ${D07}/run_base2"
    log "RESULT" "CAFE5 2-lambda:  ${D07}/run_2lambda (summary.tsv / best / final / rate_params.tsv)"
    log "RESULT" "CAFE5 1-lambda:  ${D07}/run_1lambda (summary.tsv / best / final / rate_params.tsv)"
    log "RESULT" "Family FDR (BH): ${D07}/run_base/Base_family_results.fdr.tsv (+ run_base2, run_2lambda/final, run_1lambda/final)"
    log "RESULT" "Filter report:   ${D07}/family_filter_report.tsv"
    log "RESULT" "Figures base:     ${D11}"
    log "RESULT" "Figures base2:    ${D12}"
    log "RESULT" "Figures 2-lambda: ${D13}"
    log "RESULT" "Figures 1-lambda: ${D14}"
    log "RESULT" "Ultrametric tree: ${CAFE_TREE}"
    log "RESULT" "Compat view:      ${WORKDIR}/results"
}

# ---- job.status.json status writes (EXIT trap: FINISHED on success, FAILED on error) ----
on_exit() {
    local rc=$?
    if [[ "$rc" -eq 0 ]]; then
        write_job_status "FINISHED" "Full pipeline finished successfully."
    else
        write_job_status "FAILED" "Pipeline failed (rc=${rc}); see logs/ for details."
    fi
}
trap on_exit EXIT

write_job_status "RUNNING" "Pipeline started."
main

