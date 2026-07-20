# PTDB — Plant Transporter Database

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20593739.svg)](https://doi.org/10.5281/zenodo.20593739)

A comprehensive web-based database for plant transporter proteins, providing systematic classification, evolutionary analysis, cross-species comparison, and online prediction tools.

**Website:** [https://yanglab.hzau.edu.cn/ptdb/index/home](https://yanglab.hzau.edu.cn/ptdb/index/home)

## Overview

PTDB integrates transporter protein information from multiple plant species, offering:

- **Transporter Classification**: TC system, Pfam domain, Gene Family (ABC, MFS, etc.)
- **Cross-Species Comparison**: Synteny analysis, phylogenetic trees, Ka/Ks calculation
- **Functional Annotation**: Substrate search, pathway mapping, literature integration
- **Online Tools**: BLAST search, transporter prediction, interactive gene family browser
- **Data Visualization**: Highcharts/ECharts-based interactive charts for expression and comparative analysis

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend Framework | ThinkPHP 5 |
| Database | MySQL |
| Web Server | Apache |
| Frontend Visualization | Highcharts, ECharts, D3.js |
| Sequence Alignment | MAFFT |
| Phylogenetic Tree | FastTree |
| Gene Family Evolution | CAFE5, BUSCO, IQ-TREE, MCMCtree (PAML) |
| Transmembrane Prediction | DeepTM |
| Sequence Search | SequenceServer |

## Project Structure

```
ptdb/
├── common.php              # Shared utility functions
├── config.php              # Application configuration
├── database.php            # Database connection settings
├── controller/
│   ├── Index.php           # Main page routing (Home, Species, Gene Family, etc.)
│   ├── Table.php           # Data table queries (gene family, species, etc.)
│   ├── Select.php          # Data selection & filtering endpoints
│   ├── Prediction.php     # Online transporter prediction
│   ├── Agent.php           # AI Agent API for intelligent queries
│   ├── Neo4j.php           # Graph database integration
│   ├── Php.php             # General PHP utilities
│   ├── OtherAaddition.php  # Gene family expansion/contraction (CAFE5)
│   ├── Table_copy.php      # Table data export/copy
│   └── Footer_nav.php      # Navigation component
└── view/
    ├── index/              # Main pages
    │   ├── home.html       # Homepage
    │   ├── search.html     # Gene search
    │   ├── blast.html      # BLAST interface
    │   ├── species.html    # Species browser
    │   ├── gene_family.html       # Gene family overview
    │   ├── gene_family_super.html # Gene family superfamily view
    │   ├── gene_family_phylogenetic.html # Gene family phylogenetics
    │   ├── phylogenetic.html      # Phylogenetic tree viewer
    │   ├── synteny.html           # Synteny analysis
    │   ├── evolution.html         # Evolutionary analysis
    │   ├── gf_contraction_expansion_submit.html  # Gene family expansion/contraction submit
    │   ├── gf_contraction_expansion.html         # Gene family expansion/contraction results
    │   ├── kaks.html              # Ka/Ks calculator
    │   ├── pathway.html          # Pathway mapping
    │   ├── prediction.html       # Transporter prediction
    │   ├── download.html         # Data download
    │   ├── tc_system.html        # TC classification system
    │   ├── tc_code.html          # TC code browser
    │   ├── abcde.html            # ABC transporter subfamily
    │   ├── transporter_k_d.html  # Transporter Kd values
    │   └── methods.html          # Methodology description
    ├── footer_nav/         # Shared navigation templates
    └── other_addition/     # Supplementary pages (flower diagrams, etc.)
```

## Features

### Browsing & Search
- **Gene Search**: Multi-field search by Protein ID, Gene ID, mRNA ID, species
- **Species Browser**: Browse transporter repertoires across plant species
- **Gene Family**: Explore transporter families (ABC, MFS, OPT, etc.) with member details
- **Pfam Family**: Domain-based classification
- **TC System**: Transporter Classification (TC) code-based browsing

### Evolution & Comparative Genomics
- **Phylogenetic Analysis**: ML tree inference using MAFFT (alignment) + FastTree (tree building). Users select TC code, species, substitution model (JTT/WAG/LG), and rate heterogeneity (CAT/Gamma). Results displayed as a D3.js phylogenetic tree paired with transmembrane span diagrams from DeepTM predictions.
- **Evolution / Sequence Identity**: Homologous gene comparison across species with transmembrane span visualization (Canvas-based TM helix diagrams), multiple sequence alignment (MAFFT with CLUSTAL output), and a full-featured MSA viewer (alignment view, image view, stats view with pairwise identity heatmap).
- **Synteny Analysis**: Collinear gene block visualization across species
- **Ka/Ks Analysis**: Selective pressure estimation for gene pairs
- **Evolution Search**: Query evolutionary events across lineages
- **Gene Family Expansion & Contraction**: CAFE5-based analysis of gene family gain/loss across plant species. Generates a gene family count matrix synchronously, then submits a full async pipeline (BUSCO filtering → IQ-TREE species tree → MCMCtree dating → CAFE5 analysis). Visualizations include ECharts heatmaps, species specificity index (τ) bar charts, D3.js ancestral state reconstruction trees, and per-family phylogenetic SVG trees. Results delivered via email.

### Functional Analysis
- **Pathway Mapping**: Map transporters to metabolic pathways
- **Substrate Search**: Find transporters by substrate specificity
- **Literature Integration**: Curated literature references

### Online Tools
- **BLAST**: Sequence similarity search against the PTDB dataset
- **Transporter Prediction**: Submit protein sequences for transporter classification
- **AI Agent**: Intelligent query interface for natural language questions
- **Gene Family Expansion & Contraction**: Submit species and email to receive CAFE5 analysis results

> For detailed documentation of each analysis tool's data flow, bioinformatics tools, and visualization methods, see [README_Tools.md](README_Tools.md).

### Data Access
- **Interactive Browse**: Web-based data exploration
- **Bulk Download**: Download datasets from the Download page

## API Access

PTDB provides programmatic data access through RESTful endpoints:

### Agent API
```
GET /ptdb/agent/get_ptdb_agent_base_info_by_id?id=<Protein_ID|PTPGID|AthID|GeneID|mRNAID>
```
Returns comprehensive transporter information including symbol, species, gene family, TC code, Pfam domains, and predicted substrates.

### Data Query APIs
```
GET /ptdb/table/gene_family_table             # All gene families
GET /ptdb/table/gene_family_species_table?family=<name>  # Species distribution per family
GET /ptdb/table/gene_family_member_table?family=<name>   # Family members
```

### Prediction API
```
POST /ptdb/prediction/submit_prediction   # Submit prediction task
GET  /ptdb/prediction/get_task_status?task_id=<id>  # Query task status
```

## Database Schema

The core MySQL database `PTDB` contains tables including:
- Gene/protein information and cross-references
- Per-species protein annotation tables (`ptdb_{speID}_protein_annotations`) with TC code, Pfam, protein sequences, and DeepTM transmembrane predictions
- `ptdb_tc_info` — Transporter Classification code registry with family/superfamily mappings
- `ptdb_all_species` / `ptdb_all_species_by_busco` — Species metadata and BUSCO completeness filtering
- Gene family assignments
- Pfam domain annotations
- TC classification mappings
- Species metadata
- Pathway annotations
- Ka/Ks calculation results
- Literature references
- Prediction task records

## Installation

### Prerequisites
- PHP >= 7.0
- MySQL >= 5.6
- Apache with mod_rewrite enabled
- ThinkPHP 5

### Setup
1. Clone this repository
2. Import the PTDB database schema and data into MySQL
3. Configure database connection in `database.php`
4. Deploy to Apache web server with the appropriate virtual host configuration
5. Ensure MAFFT, FastTree, and SequenceServer are installed and accessible for backend analysis tools

## Data Availability & Reproducibility

All core datasets, predicted transporter tables, structural models, confidence metrics, analysis scripts, and pipeline configurations are deposited in stable public repositories with versioned releases.

- **Source Code**: This GitHub repository
- **Version Control**: Git-based versioning with tagged releases and changelog
- **Data Deposition**: Core datasets available for bulk download via the Download page and the repository releases
- **Long-term Maintenance**: This repository serves as the persistent, version-controlled record of the PTDB codebase

## License

This project is released under the terms specified in the LICENSE file.

## Contact

For questions, bug reports, or data requests, please open an issue in this repository.
