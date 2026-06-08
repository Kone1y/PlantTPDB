# Data Description

## Overview

PTDB integrates plant transporter protein data from multiple sources, covering species across the plant kingdom. This document describes the data content, format, and access methods.

## Data Categories

### 1. Transporter Annotations

The core dataset contains protein-level transporter annotations, including:

| Field | Description |
|-------|-------------|
| Protein_ID | Unique protein identifier |
| PTPGID | PTDB internal gene ID |
| AthID | Arabidopsis thaliana ortholog ID |
| GeneID | Gene identifier |
| mRNAID | mRNA identifier |
| Species | Source species name |
| Symbol | Gene symbol |

### 2. Classification Data

#### TC (Transporter Classification) System
- Transporters are classified using the TC system (Saier et al.)
- Includes TC code, family, and superfamily assignments
- Covers major families: ABC, MFS, OPT, PTR, NPF, etc.

#### Pfam Domain Annotations
- Pfam domain assignments for each transporter
- Includes Pfam ID and domain name

#### Gene Family Assignments
- Transporter-to-gene-family mapping
- Family membership counts per species

### 3. Structural Data

#### AlphaFold Predictions
- Predicted 3D structure confidence metrics
- pLDDT (per-residue confidence) and pTM (predicted TM-score) values

#### PDB Homology
- BLAST/DIAMOND alignment results against RCSB PDB
- Sequence identity, coverage, E-value, and alignment score
- Structure reference score (composite of identity x coverage x normalized E-value)
- Confidence level classification:
  - Direct reference (score >= 0.70)
  - Functional reference (score >= 0.30)
  - Fold reference (score >= 0.10)
  - Distant reference (score < 0.10)

### 4. Subcellular Localization
- DeepLoc 2.0 predictions: localization and signal predictions
- TMHMM 2.0 predictions: transmembrane helix count and coordinates
- SignalP 6.0 predictions: signal peptide scores and cleavage sites

### 5. Sequence Analysis
- Protein physical-chemical properties: length, molecular weight, isoelectric point
- Multiple sequence alignment results (MAFFT)
- Phylogenetic trees (FastTree, Newick format)

### 6. Evolutionary Data
- Ka/Ks ratios for orthologous gene pairs
- Synteny block information for cross-species comparison
- Species lineage information

### 7. Functional Annotations
- Substrate specificity predictions
- Metabolic pathway associations
- Curated literature references

## Data Format

| Data Type | Format | Access |
|-----------|--------|--------|
| Annotation tables | TSV / MySQL | Download page, API |
| Phylogenetic trees | Newick (.nwk) | Web viewer, Download |
| Sequence alignments | FASTA / CLUSTAL | MAFFT tool, API |
| BLAST results | TSV | API, Download |
| Structural metrics | TSV | API |
| Literature | HTML / JSON | Web page |

## Data Sources

- **Sequence Data**: Plant genome databases (Phytozome, Ensembl Plants, NCBI)
- **TC Classification**: Transporter Classification Database (TCDB)
- **Pfam Domains**: Pfam protein families database
- **PDB Structures**: RCSB Protein Data Bank
- **Structural Predictions**: AlphaFold Protein Structure Database
- **Subcellular Localization**: DeepLoc 2.0, TMHMM 2.0, SignalP 6.0
- **Sequence Alignment**: MAFFT
- **Phylogenetic Trees**: FastTree
- **Sequence Search**: DIAMOND / BLASTp against PDB

## Species Coverage

Data spans multiple plant species representing major lineages. For the complete species list, see the Species browser on the PTDB website or query the `gene_information` table via API.

## Bulk Download

The entire dataset is available for download from the PTDB Download page. This includes:
- Transporter annotation tables (all species)
- Classification assignments (TC, Pfam, Gene Family)
- Structural prediction metrics
- Phylogenetic tree files
- Ka/Ks calculation results

## Data Updates

Data updates are tracked in [CHANGELOG.md](CHANGELOG.md). Each release is tagged with a version number, and the corresponding dataset snapshot is archived in the GitHub Releases section.
