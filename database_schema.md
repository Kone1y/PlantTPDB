# Database Schema

## Overview

PTDB uses a MySQL database named `PTDB`. This document describes the main tables and their relationships.

## Table List

### Core Annotation Tables

#### `ptdb_pid_for_gid_and_others`
Primary cross-reference table mapping between different identifier systems.

| Column | Type | Description |
|--------|------|-------------|
| Protein_ID | VARCHAR | Unique protein identifier |
| PTPGID | VARCHAR | PTDB internal gene ID |
| AthID | VARCHAR | Arabidopsis thaliana ortholog ID |
| GeneID | VARCHAR | Gene identifier |
| mRNAID | VARCHAR | mRNA identifier |
| Species | VARCHAR | Source species name |

#### `ptdb_{species}_protein_annotations`
Species-specific protein annotation tables (one per species).

| Column | Type | Description |
|--------|------|-------------|
| speID | VARCHAR | Species-specific ID |
| pid | VARCHAR | Protein ID |
| tc | VARCHAR | TC classification code |
| tc_pident | FLOAT | TC classification sequence identity (%) |
| tc_evalue | FLOAT | TC classification E-value |
| tid_pfam | VARCHAR | Pfam domain ID |
| seed_tid | VARCHAR | Seed alignment ID |
| seed_tc | VARCHAR | Seed TC code |
| seed_pfam | VARCHAR | Seed Pfam domain |
| DeepTM_num | INT | Predicted transmembrane helix count (DeepTM) |
| DeepTM_gff | TEXT | Transmembrane helix coordinates (GFF format) |
| TMHMM2_num | INT | Predicted transmembrane helix count (TMHMM2) |
| TMHMM2_gff | TEXT | Transmembrane helix coordinates (TMHMM2) |
| DeepLoc2_Localizations | VARCHAR | Predicted subcellular localization |
| DeepLoc2_Signals | VARCHAR | Signal peptide predictions |
| DeepLoc2_memout | TEXT | DeepLoc2 membrane output |
| SP6_score | FLOAT | SignalP 6.0 score |
| SP6_info | TEXT | SignalP 6.0 detailed info |
| SP6_gff | TEXT | Signal peptide cleavage site (GFF) |
| pep_prop_len | INT | Protein length (aa) |
| pep_prop_MW | FLOAT | Molecular weight (Da) |
| pep_prop_PI | FLOAT | Isoelectric point |

### Classification Tables

#### `ptdb_tc_info`
TC (Transporter Classification) system reference table.

| Column | Type | Description |
|--------|------|-------------|
| tc_code | VARCHAR | TC classification code |
| family | VARCHAR | Family name |
| superfamily | VARCHAR | Superfamily name |

#### `genefamily_information`
Gene family summary statistics.

| Column | Type | Description |
|--------|------|-------------|
| family | VARCHAR | Gene family name |
| gene_num | INT | Total member count |
| Ath_num | INT | Arabidopsis member count |

#### `genefamily`
Gene family membership table.

| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR | Gene ID |
| family | VARCHAR | Gene family name |
| species | VARCHAR | Species name |

#### `pfam_information`
Pfam domain reference table.

| Column | Type | Description |
|--------|------|-------------|
| pfamID | VARCHAR | Pfam domain ID |
| pfamname | VARCHAR | Pfam domain name |

### Species & Gene Tables

#### `gene_information`
Gene-level information across all species.

| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR | Gene ID |
| species | VARCHAR | Species name |
| *additional columns* | | Species-specific annotations |

#### `spe_allinformation`
Species metadata table.

| Column | Type | Description |
|--------|------|-------------|
| species | VARCHAR | Species name |
| *additional columns* | | Taxonomic and genomic metadata |

#### `spe_gene_information`
Species-specific gene information.

| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR | Gene ID |
| species | VARCHAR | Species name |

#### `ath_gene`
Arabidopsis thaliana gene reference table.

| Column | Type | Description |
|--------|------|-------------|
| Ath_ID | VARCHAR | Arabidopsis gene ID |
| Ath_name | VARCHAR | Gene symbol/name |

### Structural & Homology Tables

#### `ptdb_15_speices_pLDDT`
AlphaFold predicted structure confidence metrics.

| Column | Type | Description |
|--------|------|-------------|
| PID | VARCHAR | Protein ID |
| pLDDT | FLOAT | Per-residue confidence score |
| pTM | FLOAT | Predicted TM-score |

#### `ptdb_pdb_blast_diamond`
BLAST/DIAMOND alignment results against RCSB PDB.

| Column | Type | Description |
|--------|------|-------------|
| species | VARCHAR | Species name |
| pid | VARCHAR | Protein ID |
| pident | FLOAT | Sequence identity (%) |
| e_value | FLOAT | E-value |
| qcovhsp | FLOAT | Query coverage (%) |
| score | FLOAT | Alignment score |

### Functional Annotation Tables

#### `pathway`
Transporter-pathway associations.

| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR | Gene ID |
| species | VARCHAR | Species name |
| Ath_ID | VARCHAR | Arabidopsis ortholog ID |
| pathway | VARCHAR | Metabolic pathway name |

#### `gene_family`
Gene-to-family mapping (detailed view).

| Column | Type | Description |
|--------|------|-------------|
| family | VARCHAR | Family name |
| Ath_ID | VARCHAR | Arabidopsis gene ID |

#### `ptdb_transporter_families`
Transporter superfamily reference table.

| Column | Type | Description |
|--------|------|-------------|
| *various* | | Superfamily and subfamily classification |

#### `ptdb_gene_transporters`
Gene transporter detailed information.

| Column | Type | Description |
|--------|------|-------------|
| subfamily_name | VARCHAR | Subfamily name |
| pfam | VARCHAR | Associated Pfam domain |
| *additional columns* | | Transporter-specific annotations |

### Symbol Mapping

#### `ptdb_ath_to_symbol`
Arabidopsis ID to gene symbol mapping.

| Column | Type | Description |
|--------|------|-------------|
| Ath_id | VARCHAR | Arabidopsis gene ID |
| Symbol | VARCHAR | Gene symbol |

## Entity Relationship Summary

```
ptdb_pid_for_gid_and_others (central hub)
  ├── ptdb_{species}_protein_annotations  (1:1, via Protein_ID + Species)
  ├── ptdb_tc_info                       (N:1, via tc code)
  ├── ptdb_15_speices_pLDDT              (1:N, via PID)
  ├── ptdb_pdb_blast_diamond             (1:N, via pid)
  ├── ptdb_ath_to_symbol                 (N:1, via AthID)
  ├── genefamily                         (N:M, via id)
  ├── gene_information                   (N:1, via id)
  └── pathway                            (N:M, via id + species)
```
