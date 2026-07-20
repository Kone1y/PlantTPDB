# PTDB Analysis Tools Module

This document describes the three main analysis tools available in PTDB: **Phylogenetic Analysis**, **Evolution / Sequence Identity**, and **Gene Family Expansion & Contraction**.

---

## 1. Phylogenetic Analysis

**Page:** `view/index/phylogenetic.html`

### Overview

Performs maximum-likelihood (ML) phylogenetic inference for user-selected transporter genes. The workflow is fixed: **MAFFT** for multiple sequence alignment, followed by **FastTree (v2.1.11)** for tree construction. Within this workflow, users can choose the amino-acid substitution model and site-rate heterogeneity model.

### Frontend Display

- **Input form:** TC code (e.g. `2.A.45`), species selection (multi-select, up to 5 species), substitution model (JTT / WAG / LG), rate heterogeneity (CAT / Gamma).
- **Gene list table:** Bootstrap Table displaying matching proteins (Superfamily, Family, Species, Protein ID, TC Code, Pfam). Users can select specific rows via checkboxes, then click **Submit Selected Rows** to trigger the analysis.
- **Tree & Transmembrane Spans:** A D3.js phylogenetic tree (left) paired with a gene structure diagram (right) showing transmembrane helix positions parsed from DeepTM GFF annotations.
- The tree is rendered using `d3.phylogram.js`, and the gene structure using `d3.struct.js`.

### Data Flow

1. User submits a TC code and selects species.
2. **`Table::get_ptdb_seq_identity_data()`** — Queries `ptdb_tc_info` for family info, then queries per-species tables (`ptdb_{speID}_protein_annotations`) to retrieve matching proteins.
3. User selects protein rows and clicks Submit.
4. **`Php::get_ptdb_phylogenetic_data()`** — Retrieves protein sequences (`pep`) and transmembrane annotations (`DeepTM_gff`) from per-species tables, then:
   - Runs **MAFFT** (`mafft --quiet --maxiterate 1000`) to produce a multiple sequence alignment.
   - Runs **FastTree** (`FastTree [-wag|-lg] [-gamma]`) on the alignment to build an ML tree.
   - Returns the Newick-format tree string and protein domain annotations.
5. Frontend parses the Newick tree and DeepTM annotations, renders the D3 phylogenetic tree and transmembrane span diagram side by side.

### Bioinformatics Tools & Parameters

| Step | Tool | Parameters |
|------|------|-----------|
| Sequence Alignment | MAFFT | `--quiet --maxiterate 1000` |
| Tree Building | FastTree v2.1.11 | `-wag` / `-lg` (WAG or LG model; JTT is default), `-gamma` (Gamma rate; CAT is default) |

---

## 2. Evolution / Sequence Identity

**Page:** `view/index/evolution.html`

### Overview

Displays homologous gene information across selected species for a given TC code, including transmembrane span visualization and multiple sequence alignment results. Supports both single-species and multi-species views.

### Frontend Display

- **Input form:** TC code (e.g. `2.A.45`), species selection (multi-select).
- **Gene list table:** Bootstrap Table with the same columns as the Phylogenetic tool. Users select rows and click **Submit Selected Rows**.
- **Transmembrane Spans Info:** For each selected protein, renders an HTML5 Canvas diagram showing TM helix positions (from DeepTM predictions). Helices are drawn as blue rectangles with numbered labels. Hovering highlights the helix and shows a tooltip with position/length info.
- **Multiple Alignment Sequence Info:** Displays the raw CLUSTAL-format multiple sequence alignment output in a monospace pre-formatted block.
- **Tree & Structure** (single species only): D3.js phylogenetic tree paired with gene structure and Pfam domain visualization.
- **Multi Alignment Viewer:** A full-featured MSA viewer with three view modes — **msa view** (alignment with identity/gap statistics), **image view** (colored alignment image with configurable coloring scheme), and **stats view** (gap/identity distribution, pairwise identity heatmap).

### Data Flow

1. User submits a TC code and selects species.
2. **`Table::get_ptdb_seq_identity_data()`** — Same as Phylogenetic tool: queries `ptdb_tc_info` and per-species annotation tables to retrieve matching proteins.
3. User selects protein rows and clicks Submit.
4. **`Table::get_seq_trans_multi_data()`** — Retrieves protein sequences (`pep`) and transmembrane annotations (`DeepTM_gff`) for selected proteins.
5. Frontend renders TM span diagrams using Canvas, and generates FASTA data from the sequences.
6. **`Select::get_multi_alignment_seq_data()`** — Takes the FASTA data, runs **MAFFT** (`mafft --auto --clustalout`) for alignment, and returns both parsed sequences and the raw CLUSTAL output.
7. Frontend displays the raw CLUSTAL alignment text.
8. Additionally, **`Php::evo_data()`** — For single species, queries pre-computed trees (`tree` table), structures (`struct_json`), and MSA data (`multiseq` table). For multiple species, queries cross-species gene mappings (`spe_gene_information`, `seq` tables), runs MAFFT in real-time, and feeds results into the MSA Viewer.

### Bioinformatics Tools & Parameters

| Step | Tool | Parameters |
|------|------|-----------|
| Sequence Alignment | MAFFT | `--auto --clustalout` (from `get_multi_alignment_seq_data`); `--quiet --maxiterate 1000` (from `evo_data`, multi-species branch) |

### Shared Backend Interfaces with Phylogenetic Tool

- `Table::get_ptdb_all_spe()` — Populates the species dropdown.
- `Table::get_ptdb_seq_identity_data()` — Retrieves protein annotations by TC code.
- Both tools query the same per-species tables (`ptdb_{speID}_protein_annotations`) and `ptdb_tc_info`.

---

## 3. Gene Family Expansion & Contraction

**Pages:**
- `view/index/gf_contraction_expansion_submit.html` — Submission form page.
- `view/index/gf_contraction_expansion.html` — Results display page.

### Overview

Analyzes gene family expansion and contraction across selected plant species using **CAFE5**. Users select species and provide an email address. The tool first generates a gene family count matrix synchronously, then submits a full CAFE5 analysis pipeline as an asynchronous background job. Results are delivered via email.

### Frontend Display

**Submission page (`gf_contraction_expansion_submit.html`):**
- **Input form:** Species multi-select (default: 19 representative species filtered by BUSCO completeness >= 90%), email input.
- **Preliminary results (shown immediately after submit):**
  - **Gene Family Counts Table:** Bootstrap Table showing family counts across species, with a Species Specificity Index (τ) column. τ = Σ(1 - xᵢ/xₘₐₓ) / (n-1), ranging from 0 (ubiquitous) to 1 (highly specific). Table supports pagination, search, sorting, and Excel export.
  - **Gene Family Counts Heatmap:** ECharts heatmap visualization with a family filter sidebar. Color range: dark blue (#355F8D) → green (#2CA981) → yellow (#F1E628).
  - **Species Specificity Index (τ) Chart:** ECharts horizontal bar chart showing raw counts or relative specificity per species for a selected family. Color-coded by rank (top/mid/low).
- **CAFE analysis status message:** Shows whether the job uses pre-computed data (19 default species) or is a custom analysis.

**Results page (`gf_contraction_expansion.html`):**
- All the same visualizations as the submission page (table, heatmap, τ chart).
- **Ancestral State Reconstruction (ASR) Tree:** D3.js-rendered phylogenetic tree with branch coloring: red for expansion, blue for contraction. Branches with p < 0.05/0.01/0.001 are marked with */\*\*/\*\*\*. Hover shows divergence, p-value, and change count.
- **Gene Family Phylogenetic Tree:** SVG image viewer. Users select a family (TC number or Symbol) and the corresponding pre-rendered SVG tree is loaded.

### Data Flow

1. **`OtherAaddition::get_tc_symbol_list()`** — Reads pre-defined TC code list (156 families) or Symbol list from flat files.
2. **`Table::get_ptdb_all_spe_by_busco()`** — Queries `ptdb_all_species_by_busco` and filters to species with BUSCO completeness >= 90%.
3. On submit:
   - **Pre-computed case** (19 default species): Frontend directly fetches pre-computed TSV and code list files from the static server.
   - **Custom case:** **`OtherAaddition::run_cafe_matrix_only()`** — Synchronously runs three `planttpdb-cafe` subcommands (`init` → `prepare-input` → `make-cafe-matrix`) to generate the gene family count matrix. Returns the TSV data and family IDs for immediate display.
4. **`OtherAaddition::run_cafe_analysis()`** — Submits the full CAFE5 pipeline as an async background job:
   - Creates a monitoring script that polls `job.status.json` every 20 seconds for up to 48 hours.
   - Sends email notifications (submission received, job completed/failed).
   - Results are stored on the server and accessible via a URL link sent by email.
   - Pre-computed results are served immediately with a permanent URL.
   - Results are auto-cleaned after 14 days.
5. Results page (`gf_contraction_expansion.html`) loads data from static URLs based on the URL parameter `?variable={label}`, including TSV, family code lists, ASR tree files (NEXUS format), and branch probability/change statistics.

### Bioinformatics Tools & Parameters

| Step | Tool | Parameters / Notes |
|------|------|---------------------|
| Species Filtering | BUSCO | BUSCO completeness >= 90% |
| Species Tree Building | IQ-TREE | Part of the async CAFE5 pipeline |
| Divergence Time Estimation | MCMCtree (PAML) | Ultrametric tree for CAFE5 input |
| Gene Family Analysis | CAFE5 (`planttpdb-cafe`) | Subcommands: `init`, `prepare-input`, `make-cafe-matrix`, `run-all` |
| Matrix Generation | planttpdb-cafe | `init --force` → `prepare-input --force` → `make-cafe-matrix --matrix-type {tc\|symbol} --force` |
| Full Pipeline | planttpdb-cafe | `run-all --species {file} --config {yaml} --outdir {dir} --matrix-type {type} --list {list} --label {label} --force` |

---

## Frontend Technology Stack (All Tools)

| Component | Technology |
|-----------|-----------|
| CSS Framework | Bootstrap 3.3.7 |
| Data Tables | Bootstrap Table (with pagination, export, tree grid extensions) |
| Dropdown Select | Select2 |
| Phylogenetic Trees | D3.js v3 + d3.phylogram.js (Phylogenetic tool); D3.js v3 custom rendering (ASR trees) |
| Gene Structure | d3.struct.js |
| Heatmaps & Charts | ECharts 5.3.2 |
| TM Span Diagrams | HTML5 Canvas (Evolution tool) |
| MSA Viewer | Custom JavaScript (seqlib.js, SequenceLogoDiagramD3.js, etc.) |
| Excel Export | TableExport + XLSX.js + FileSaver.js |
| Data Format | TSV files, Newick tree strings, GFF-like annotations (DeepTM), CLUSTAL alignment, NEXUS (ASR trees) |
