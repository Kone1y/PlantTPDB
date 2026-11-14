#!/usr/bin/env bash
# 06_run_cafe_restarts.sh — the CAFE5 analysis stage (robust-restart protocol)
#
# Rate model (RATE_MODEL):
#   2lambda (default)  two-rate birth-death (-y lambda tree). DEEP_LAMBDA_TAXA is
#                    required: the comma-separated lambda-2 species group; clades whose
#                    tips are all lambda-2 species (incl. their stems) are category 2;
#                    everything else is lambda-1.
#   base             single global rate (no lambda tree).
# Both modes share one protocol:
#   s1  tree x100 -> 10-Myr units; 2lambda mode also emits the lambda tree
#   s2  input matrix (>=100 filter only; no other family-level filtering)
#   s3  REPS independent joint restarts (lambda[,lambda2,]epsilon; -I 5000); restarts are
#       required because the optimizer has many local optima
#   s4  keep the min -lnL as best; fix its error model and re-estimate lambda over FINAL_REPS restarts
#
#   s5  BH-FDR correction of the final Base_family_results.txt
#        rep*/, summary.tsv, final_reps/, final_reps.summary.tsv,
#        best/, final/, rate_params.tsv (lambda1[,lambda2], epsilon, -lnL summary)}
#
# Rationale from earlier single-rate runs: lambda hit the 1/longest-branch boundary and
# epsilon drifted to ~0.45; a separate deep-lineage lambda normalized it. CAFE5 5.1.0
# fixed-error model files are valid only on the estimating family set (any superset segfaults),
set -euo pipefail

RUN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D06="${RUN_ROOT}/06_mcmctree"
D07="${RUN_ROOT}/07_cafe5"

CAFE5_BIN="${CAFE5_BIN:-cafe5}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MCMCTREE_PREFIX="${MCMCTREE_PREFIX:-plant_31_auto}"
SCALE="${SCALE:-100}"           # MCMCtree units (1000 Myr) -> 10-Myr units.
                                # Note: at x1000 (true Myr) CAFE5 multi-lambda initialization fails often
                                # ("Failed to initialize any reasonable values"); x100 is numerically stable.
                                # Reported lambda values are per 10 Myr.
REPS="${REPS:-30}"
JOBS="${JOBS:-4}"
ITER="${ITER:-5000}"
# The final stage (fixed error model) is also multi-modal: a single final run once sat 29
# log-likelihood units above the best joint fit of its own error model; use restarts.
FINAL_REPS="${FINAL_REPS:-20}"
MIN_REPS="${MIN_REPS:-5}"          # minimum successful restarts for s4 selection (lower for smoke tests)
MIN_FINAL_REPS="${MIN_FINAL_REPS:-3}"

# ---- rate model and lambda-2 grouping ----
RATE_MODEL="${RATE_MODEL:-2lambda}"   # 2lambda | base
# lambda-2 group (2lambda mode only; no default here — provided by the caller):
# comma-separated species names that must exist on the tree; the rest are lambda-1
DEEP_LAMBDA_TAXA="${DEEP_LAMBDA_TAXA:-}"

case "$RATE_MODEL" in
  2lambda)
    [[ -n "$DEEP_LAMBDA_TAXA" ]] || {
      echo "[ERROR] RATE_MODEL=2lambda requires DEEP_LAMBDA_TAXA (the lambda-2 group)" >&2
      echo "        (comma-separated species; the rest are lambda-1), e.g.:" >&2
      echo "        DEEP_LAMBDA_TAXA=Klebsormidium_nitens,Edaphochlamys_debaryana,Volvox_carteri_nagariensis" >&2
      exit 1
    }
    ;;
  base)
    echo "[s0] RATE_MODEL=base: single global rate (DEEP_LAMBDA_TAXA ignored if set)"
    ;;
  *)
    echo "[ERROR] unknown RATE_MODEL='$RATE_MODEL' (expected: 2lambda | base)" >&2
    exit 1
    ;;
esac

TREE_RAW="${TREE_RAW:-${D06}/${MCMCTREE_PREFIX}.mcmctree.ultrametric.nwk}"
MATRIX_RAW="${MATRIX_RAW:-${D07}/cafe_input_matrix.filtered.tsv}"
OUT="${OUT:-${D07}/run_2lambda}"
TREE_MYR="${OUT}/tree.Myr.nwk"
TREE_L2="${OUT}/tree.lambda2.nwk"
MATRIX_NJ="${OUT}/matrix.nojump.tsv"

[[ -s "$TREE_RAW" ]]   || { echo "[ERROR] missing $TREE_RAW" >&2; exit 1; }
[[ -s "$MATRIX_RAW" ]] || { echo "[ERROR] missing $MATRIX_RAW" >&2; exit 1; }

mkdir -p "$OUT"

# ---------- s1: scale tree + lambda tree (2lambda mode only) ----------------
RATE_MODEL="$RATE_MODEL" DEEP_LAMBDA_TAXA="$DEEP_LAMBDA_TAXA" \
"$PYTHON_BIN" - "$TREE_RAW" "$TREE_MYR" "$TREE_L2" "$SCALE" <<'PY'
import os, re, sys

src, dst, dst_l2, scale = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
rate_model = os.environ.get("RATE_MODEL", "2lambda")
DEEP = set(t for t in os.environ["DEEP_LAMBDA_TAXA"].split(",") if t)
nwk = open(src).read().strip().rstrip(";")
num = re.compile(r":(\d+\.?\d*(?:[eE][-+]?\d+)?)")
scaled = num.sub(lambda m: ":" + f"{float(m.group(1)) * scale:g}", nwk)
open(dst, "w").write(scaled + ";\n")
print(f"[s1] tree x{scale:g} -> {dst}")

if rate_model != "2lambda":
    print(f"[s1] RATE_MODEL={rate_model}: skipping lambda-tree generation")
    sys.exit(0)

# lambda tree: clades whose tips are all lambda-2 species (incl. stems) -> category 2, rest -> 1
s = scaled
pos = 0
def node():
    global pos
    children = []
    if s[pos] == "(":
        pos += 1
        while True:
            children.append(node())
            if s[pos] == ",": pos += 1; continue
            assert s[pos] == ")"; pos += 1; break
    m = re.match(r"[A-Za-z0-9_.]+", s[pos:]); name = m.group(0) if m else None
    if m: pos += m.end()
    bl = 0.0
    if pos < len(s) and s[pos] == ":":
        m = re.match(r":[0-9.eE+-]+", s[pos:]); bl = float(m.group(0)[1:]); pos += m.end()
    return {"name": name, "bl": bl, "children": children}

def tips(n): return [n["name"]] if not n["children"] else [t for c in n["children"] for t in tips(c)]
def label(n):
    cat = 2 if all(t in DEEP for t in tips(n)) else 1
    inner = ("(" + ",".join(label(c) for c in n["children"]) + ")") if n["children"] else n["name"]
    return inner + f":{cat}"

tree = node()
missing = DEEP - set(tips(tree))
assert not missing, f"DEEP_LAMBDA_TAXA species not on the tree: {sorted(missing)}"
open(dst_l2, "w").write(label(tree) + ";\n")
print(f"[s1] lambda tree (2 cats, lambda2 group={sorted(DEEP)}) -> {dst_l2}")
PY

# -y flag of the CAFE command (empty in base mode; paths contain no spaces)
if [[ "$RATE_MODEL" == "2lambda" ]]; then
  YFLAG="-y $TREE_L2"
else
  YFLAG=""
fi
export YFLAG

# ---------- s2: input matrix (>=100 filter only, no custom jump rules) ----
# The only filter is the per-species count >= 100 applied at matrix build (04_get_tc).
# The custom max>=20 & max/min>=10 jump filter was removed: CAFE5 5.1.0 fixed-error model
# files are valid only on the estimating family set (any superset segfaults); estimation and
# testing must run on the same set, so the model input is MATRIX_RAW (the >=100-filtered matrix, 147 families).
cp "$MATRIX_RAW" "$MATRIX_NJ"
echo "[s2] matrix (>=100 filter only): $(($(grep -c '' "$MATRIX_NJ") - 1)) families"

# ---------- s3: REPS restarts -------------------------------------------
if [[ "${SKIP_REPS:-}" == "1" ]]; then
  echo "[s3] SKIP_REPS=1, reusing existing rep*"
else
run_one() {
  local i="$1"
  local d
  d=$(printf "%s/rep%02d" "$OUT" "$i")
  mkdir -p "$d"
  "$CAFE5_BIN" -t "$TREE_MYR" -i "$MATRIX_NJ" \
    -p -e $YFLAG -I "$ITER" -o "$d" > "$d/cafe5.stdout" 2>&1
}
export -f run_one
export OUT CAFE5_BIN TREE_MYR MATRIX_NJ TREE_L2 ITER

echo "[s3] running $REPS restarts ($JOBS jobs, -I $ITER) ..."
seq 1 "$REPS" | xargs -P "$JOBS" -I{} bash -c 'run_one {}'
fi

# ---------- s4: pick best + fixed-error final runs ---------------------
"$PYTHON_BIN" - "$OUT" "$MIN_REPS" <<'PY'
import os, re, sys, shutil

out, min_reps = sys.argv[1], int(sys.argv[2])
rows = []
skipped = []
for d in sorted(os.listdir(out)):
    rf = os.path.join(out, d, "Base_results.txt")
    if not d.startswith("rep"):
        continue
    if not os.path.isfile(rf):
        skipped.append(d)
        continue
    txt = open(rf).read()
    m_lnl = re.search(r"Likelihood \(-lnL\): ([0-9.]+)", txt)
    m_lam = re.search(r"^Lambda: (.+)$", txt, re.M)
    m_eps = re.search(r"^Epsilon: ([-0-9.eE+]+)", txt, re.M)   # poor solutions may cross the boundary negative
    if not (m_lnl and m_lam and m_eps):
        skipped.append(d)
        continue
    rows.append((float(m_lnl.group(1)), d, m_lam.group(1).strip(), float(m_eps.group(1))))

if skipped:
    print(f"[s4] WARNING: {len(skipped)} reps failed/skipped: {', '.join(skipped)}")
if len(rows) < min_reps:
    sys.exit(f"[ERROR] only {len(rows)}/{len(rows)+len(skipped)} reps succeeded, refusing to pick best")

rows.sort()
with open(os.path.join(out, "summary.tsv"), "w") as f:
    f.write("rank\trep\tneg_lnL\tlambdas\tepsilon\n")
    for rank, (lnl, d, lam, eps) in enumerate(rows, 1):
        f.write(f"{rank}\t{d}\t{lnl:.2f}\t{lam}\t{eps:g}\n")

best_lnl, best, lam, eps = rows[0]
print(f"[s4] best = {best}  -lnL={best_lnl:.2f}  lambda=({lam})  epsilon={eps:g}")
print(f"     worst -lnL={rows[-1][0]:.2f} (spread {rows[-1][0]-best_lnl:.1f} log units)")

dst = os.path.join(out, "best")
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(os.path.join(out, best), dst)
print(f"[s4] copied {best} -> best/")
PY

BEST_EM="${OUT}/best/Base_error_model.txt"
[[ -s "$BEST_EM" ]] || { echo "[ERROR] no error model in best/" >&2; exit 1; }

# final (fixed error model): multiple restarts, keep the best -lnL (avoid poor local optima)
run_final_one() {
  local i="$1"
  local d
  d=$(printf "%s/final_reps/rep%02d" "$OUT" "$i")
  mkdir -p "$d"
  "$CAFE5_BIN" -t "$TREE_MYR" -i "$MATRIX_NJ" \
    -p -e"$BEST_EM" $YFLAG -I "$ITER" -o "$d" > "$d/cafe5.stdout" 2>&1
}
export -f run_final_one
export BEST_EM

echo "[s4] running $FINAL_REPS fixed-error final restarts ($JOBS jobs) ..."
seq 1 "$FINAL_REPS" | xargs -P "$JOBS" -I{} bash -c 'run_final_one {}'

"$PYTHON_BIN" - "$OUT" "$MIN_FINAL_REPS" <<'PY'
import os, re, sys, shutil

out, min_reps = sys.argv[1], int(sys.argv[2])
rows, skipped = [], []
for d in sorted(os.listdir(os.path.join(out, "final_reps"))):
    rf = os.path.join(out, "final_reps", d, "Base_results.txt")
    if not d.startswith("rep"):
        continue
    if not os.path.isfile(rf):
        skipped.append(d); continue
    txt = open(rf).read()
    m_lnl = re.search(r"Likelihood \(-lnL\): ([0-9.]+)", txt)
    m_lam = re.search(r"^Lambda: (.+)$", txt, re.M)
    m_eps = re.search(r"^Epsilon: ([-0-9.eE+]+)", txt, re.M)
    if not (m_lnl and m_lam):
        skipped.append(d); continue
    rows.append((float(m_lnl.group(1)), d, m_lam.group(1).strip(),
                 float(m_eps.group(1)) if m_eps else None))
if skipped:
    print(f"[s4] final WARNING: skipped {len(skipped)} reps: {', '.join(skipped)}")
if len(rows) < min_reps:
    sys.exit(f"[ERROR] only {len(rows)} final restarts succeeded")
rows.sort()
with open(os.path.join(out, "final_reps.summary.tsv"), "w") as f:
    f.write("rank\trep\tneg_lnL\tlambdas\tepsilon\n")
    for rank, (lnl, d, lam_i, eps_i) in enumerate(rows, 1):
        f.write(f"{rank}\t{d}\t{lnl:.2f}\t{lam_i}\t"
                f"{'NA' if eps_i is None else format(eps_i, 'g')}\n")
best_lnl, best, lam, eps = rows[0]
print(f"[s4] final best = {best}  -lnL={best_lnl:.2f}  lambda=({lam})  "
      f"epsilon={'NA' if eps is None else format(eps, 'g')}  "
      f"(worst {rows[-1][0]:.2f}, {len(rows)} reps)")
dst = os.path.join(out, "final")
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(os.path.join(out, "final_reps", best), dst)
PY

echo "[s4] final (fixed error model, best of $FINAL_REPS restarts) done"
grep -E "^Lambda|^Epsilon|Likelihood" "${OUT}/final/Base_results.txt"

# ---------- s5: BH FDR -------------------------------------------------
"$PYTHON_BIN" - "${OUT}/final" <<'PY'
import sys, os
from scipy.stats import false_discovery_control

cafe_dir = sys.argv[1]
src = os.path.join(cafe_dir, "Base_family_results.txt")
out = os.path.join(cafe_dir, "Base_family_results.fdr.tsv")

def fmt(x):
    return "0" if x == 0 else f"{x:.4g}"

fams, p_raw, p_float, orig_sig = [], [], [], []
with open(src) as fh:
    fh.readline()
    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        try:
            p = float(parts[1])
        except ValueError:
            continue
        fams.append(parts[0]); p_raw.append(parts[1]); p_float.append(p)
        orig_sig.append(parts[2].strip().lower().startswith("y") if len(parts) > 2 else p < 0.05)

q = [min(1.0, float(v)) for v in false_discovery_control(p_float, method="bh")]
with open(out, "w") as fh:
    fh.write("#FamilyID\tpvalue\tBH_FDR\tsig_pvalue_0.05\tsig_FDR_0.05\n")
    for fam, raw, qq, s in zip(fams, p_raw, q, orig_sig):
        fh.write(f"{fam}\t{raw}\t{fmt(qq)}\t{'y' if s else 'n'}\t{'y' if qq < 0.05 else 'n'}\n")
print(f"[s5] {out}: {len(fams)} families, FDR<0.05: {sum(1 for qq in q if qq < 0.05)}")
PY

# ---------- s6: final fitted parameters (lambda1[,lambda2], epsilon, -lnL) -------
"$PYTHON_BIN" - "$OUT" "$RATE_MODEL" "$SCALE" "$DEEP_LAMBDA_TAXA" <<'PY'
import os, re, sys

out, rate_model, scale, deep = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] or "-"

def parse_results(path):
    txt = open(path).read()
    m_lnl = re.search(r"Likelihood \(-lnL\): ([0-9.]+)", txt)
    m_lam = re.search(r"^Lambda: (.+)$", txt, re.M)
    m_eps = re.search(r"^Epsilon: ([-0-9.eE+]+)", txt, re.M)
    lams = [float(x) for x in m_lam.group(1).split(",")] if m_lam else []
    return (float(m_lnl.group(1)) if m_lnl else None,
            lams[0] if lams else None,
            lams[1] if len(lams) > 1 else None,
            float(m_eps.group(1)) if m_eps else None)

def fmt(x):
    return "NA" if x is None else format(x, ".6g")

with open(os.path.join(out, "matrix.nojump.tsv")) as fh:
    n_fam = sum(1 for _ in fh) - 1

# The MCMCtree tree is in 1000-Myr units; after xSCALE the CAFE tree unit is 1000/SCALE Myr,
# so lambda is per (1000/SCALE) Myr.
try:
    unit = format(1000 / float(scale), "g") + "-Myr"
except ValueError:
    unit = "NA"

rows = []
for stage, sub in (("joint_best", "best"), ("final_fixed_error", "final")):
    p = os.path.join(out, sub, "Base_results.txt")
    if os.path.isfile(p):
        rows.append((stage,) + parse_results(p))

dst = os.path.join(out, "rate_params.tsv")
with open(dst, "w") as f:
    f.write("stage\tneg_lnL\tlambda_1\tlambda_2\tepsilon\tn_families\t"
            "tree_units\trate_model\tlambda2_taxa\n")
    for stage, lnl, l1, l2, eps in rows:
        f.write("\t".join([stage, fmt(lnl), fmt(l1), fmt(l2), fmt(eps),
                           str(n_fam), unit, rate_model, deep]) + "\n")

print(f"[s6] {dst}")
for stage, lnl, l1, l2, eps in rows:
    lam_txt = f"lambda_1={fmt(l1)}" + (f", lambda_2={fmt(l2)}" if l2 is not None else "")
    print(f"[s6] {stage}: {lam_txt}, epsilon={fmt(eps)}, -lnL={fmt(lnl)} "
          f"(lambda unit: per {unit})")
PY

echo
echo "==================== done ===================="
echo "restart summary:  ${OUT}/summary.tsv"
echo "best restart:     ${OUT}/best"
echo "final result:     ${OUT}/final (incl. Base_family_results.fdr.tsv)"
echo "final restarts:   ${OUT}/final_reps.summary.tsv"
echo "rate parameters:  ${OUT}/rate_params.tsv (lambda1[,lambda2], epsilon, -lnL summary)"