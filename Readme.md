# PTDB — Plant Transporter Database

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20593739.svg)](https://doi.org/10.5281/zenodo.20593739)

A comprehensive web-based database for plant transporter proteins, providing systematic classification, evolutionary analysis, cross-species comparison, and online prediction tools.

**Website:** [https://yanglab.hzau.edu.cn/ptdb/index/home](https://yanglab.hzau.edu.cn/ptdb/index/home)

## Overview

PTDB integrates transporter protein information from multiple plant species, offering:

- **Transporter Classification**: TC system, Pfam domain, Gene Family (ABC, MFS, etc.)
- **Cross-Species Comparison**: Synteny analysis, phylogenetic trees, Ka/Ks calculation
- **Functional Annotation**: Substrate search, pathway mapping, literature integration
- **Online Tools**: BLAST search, transporter prediction, gene family expansion & contraction analysis

## Analysis Tools

### Phylogenetic Analysis

Maximum-likelihood phylogenetic inference for transporter genes. The pipeline performs multiple sequence alignment with **MAFFT**, followed by tree construction with **FastTree (v2.1.11)**. Users can select the amino-acid substitution model (JTT / WAG / LG) and site-rate heterogeneity model (CAT / Gamma).

```bash
bash Tools/phylogenetic_analysis.sh -i input.fasta -t wag -r gamma -o output/
```

| Parameter | Description | Options | Default |
|-----------|-------------|---------|---------|
| `-i` | Input FASTA file | — | (required) |
| `-t` | Substitution model | jtt, wag, lg | jtt |
| `-r` | Rate heterogeneity | cat, gamma | cat |
| `-o` | Output directory | — | ./phylogenetic_output |

**Dependencies:** MAFFT, FastTree

**Output:**
- `aligned_sequences.fa` — MAFFT multiple sequence alignment
- `phylogenetic_tree.nwk` — Newick-format ML phylogenetic tree

---

### Evolution / Sequence Identity

Homologous gene comparison across species with multiple sequence alignment. Accepts a multi-sequence FASTA file, runs **MAFFT** for alignment, and outputs the result in CLUSTAL format along with parsed individual sequences.

```bash
bash Tools/evolution_analysis.sh -i input.fasta -o output/
```

| Parameter | Description | Options | Default |
|-----------|-------------|---------|---------|
| `-i` | Input FASTA file | — | (required) |
| `-o` | Output directory | — | ./evolution_output |

**Dependencies:** MAFFT

**Output:**
- `alignment.clustal` — CLUSTAL-format multiple sequence alignment
- `parsed_sequences.fasta` — Individual parsed sequences

---

### Gene Family Expansion & Contraction

Analysis of gene family gain and loss across plant species using **CAFE5**. Supports two modes:

**Matrix generation** (synchronous, ~5 minutes): Generates a gene family count matrix without running the full expansion/contraction analysis.

```bash
bash Tools/gene_family_expansion_contraction.sh \
    --species Arabidopsis_thaliana,Oryza_sativa,Glycine_max \
    --matrix-type tc \
    --outdir output/
```

**Full pipeline** (asynchronous, several hours): Runs the complete analysis including BUSCO filtering, IQ-TREE species tree construction, MCMCtree divergence time estimation, and CAFE5 expansion/contraction analysis.

```bash
bash Tools/gene_family_expansion_contraction.sh \
    --species Arabidopsis_thaliana,Oryza_sativa,Populus_trichocarpa,Zea_mays \
    --matrix-type tc \
    --outdir output/ \
    --full \
    --email user@example.com
```

| Parameter | Description | Options | Default |
|-----------|-------------|---------|---------|
| `--species` | Comma-separated species list (min 3) | — | (required) |
| `--matrix-type` | Gene family type | tc, symbol | (required) |
| `--family-list` | Custom gene family list file | file path | (built-in list) |
| `--outdir` | Output directory | — | ./cafe_output |
| `--full` | Run full async pipeline | — | (off) |
| `--email` | Email for notification | — | (required with --full) |
| `--label` | Custom job label | — | (auto-generated) |

**Dependencies:** planttpdb-cafe (CAFE5 wrapper); full mode additionally requires BUSCO, IQ-TREE, MCMCtree (PAML)

**Output (matrix mode):**
- `results/04_cafe_input/tc.filtered.tsv` (or `symbol.filtered.tsv`) — Gene family count matrix

---

## Project Structure

```
ptdb/
├── Tools/
│   ├── phylogenetic_analysis.sh             # Phylogenetic tree inference pipeline
│   ├── evolution_analysis.sh                 # Multiple sequence alignment pipeline
│   └── gene_family_expansion_contraction.sh # CAFE5 gene family analysis pipeline
├── Readme.md
├── README_CN.md
├── README_Tools.md
├── README_Tools_CN.md
├── LICENSE
├── CITATION.cff
└── ...
```

> For detailed documentation of each tool's data flow, bioinformatics tools, and parameters, see [README_Tools.md](README_Tools.md).

## Data Availability & Reproducibility

All core datasets, predicted transporter tables, structural models, confidence metrics, analysis scripts, and pipeline configurations are deposited in stable public repositories with versioned releases.

- **Source Code**: This GitHub repository
- **Version Control**: Git-based versioning with tagged releases and changelog
- **Data Deposition**: Core datasets available for bulk download via the [Download page](https://yanglab.hzau.edu.cn/ptdb/index/download) and the repository releases

## Citation

If you use PTDB in your research, please cite it as follows:

```
Liang, G., Huang, W., & Luo, C. (2026). PTDB: Plant Transporter Database.
Zenodo. https://doi.org/10.5281/zenodo.20593739
```

## License

This project is released under the terms specified in the LICENSE file.

## Contact

For questions, bug reports, or data requests, please open an issue in this repository.
