# PTDB API Reference

This document provides a complete reference for all PTDB API endpoints used across the web portal. Endpoints are organized by functional category.

**Base URL:** `https://your-domain/ptdb`

---

## 1. Search APIs

### 1.1 Search by ID / Keyword

```
POST /ptdb/table/get_ptdb_index_data
```

Multi-type search across transporter records. Supports searching by AthGID, Protein_ID, mRNAID, GeneID, PTPGID, UniRef50, UniRef90, SwissProt, PDB ID, Symbol, or All fields.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Search query |
| `intype` | string | Yes | `All`, `AthGID`, `Protein_ID`, `mRNAID`, `GeneID`, `PTPGID`, `UniRef50`, `UniRef90`, `SwissProt`, `PDB`, `Symbol` |

**Response:** JSON array of matching records from `ptdb_pid_for_gid_and_others`, enriched with `Symbol` and `pdb_id`.

```json
[
  {
    "Protein_ID": "...",
    "PTPGID": "...",
    "AthID": "...",
    "GeneID": "...",
    "mRNAID": "...",
    "Species": "...",
    "Symbol": "...",
    "pdb_id": "..."
  }
]
```

> Source: `search_index.html`

---

### 1.2 Search by Gene Symbol

```
POST /ptdb/table/get_ptdb_index_data_by_symbol
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `insymbol` | string | Yes | Gene symbol (e.g., "ABCG1") |

**Response:** JSON array matched by `ptdb_ath_to_symbol` JOIN `ptdb_pid_for_gid_and_others`.

> Source: `search_index.html`

---

### 1.3 Batch Search by PTDGID

```
POST /ptdb/table/get_ptdb_batch_search_index_data
```

Retrieve summary for one or more transporters by PTDGID (PTDB internal ID).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inids` | string | Yes | Comma-separated PTDGID list |

**Response:**

```json
[
  {
    "PTDGID": "...",
    "GeneID": "...",
    "Species": "...",
    "TaxonomicID": "...",
    "PTDSymbol": "...",
    "GenomeSource": "...",
    "GenomeSourceLink": "...",
    "Family": "...",
    "TCNumber": "...",
    "POS": "..."
  }
]
```

**DB Tables:** `ptdb_pid_for_gid_and_others`, `ptdb_all_spe_pid_index`, `ptdb_{speID}_protein_annotations`, `ptdb_species_summary_with_source`, `tc_hierarchy`, `ptdb_all_spe_best_hit_with_des_symbol`, `ptdb_fil_species_summary_gca_with_ks`

> Source: `search.html`

---

## 2. Gene Detail APIs

### 2.1 Get Protein Info by PTDGID

```
POST /ptdb/table/get_ptdb_search_index_data
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | PTDGID |
| `intype` | string | Yes | Fixed: `PTPGID` |

**Response:**

```json
{
  "GeneID": "...",
  "Protein_ID": "...",
  "Chr": "...",
  "Start": "...",
  "End": "...",
  "allPTPGID": ["..."],
  "allPID": ["..."]
}
```

> Source: `search.html`

---

### 2.2 Get Species by Protein ID

```
POST /ptdb/table/get_ptdb_spe_by_pid
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ingid` | string | Yes | Protein ID |

**DB Table:** `ptdb_all_spe_pid_index`

**Response:** `[{ "speID": "...", ... }]`

> Source: `search.html`

---

### 2.3 Get Protein Annotations

```
POST /ptdb/table/get_ptdb_protein_annotations
```

Comprehensive protein annotation: TC classification, Pfam domain, transmembrane predictions (DeepTM, TMHMM2), subcellular localization (DeepLoc2), signal peptide (SignalP6), physical-chemical properties.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ingid` | string | Yes | Protein ID |
| `inspe` | string | Yes | Species name (speID) |

**Response:**

```json
[{
  "speID": "...",
  "pid": "...",
  "tc": "3.A.1.1.1",
  "tc_pident": 95.5,
  "tc_evalue": 1e-180,
  "tid_pfam": "PF00005",
  "seed_tid": "...",
  "seed_tc": "...",
  "seed_pfam": "...",
  "DeepTM_num": 6,
  "DeepTM_gff": "...",
  "TMHMM2_num": 6,
  "TMHMM2_gff": "...",
  "DeepLoc2_Localizations": "...",
  "DeepLoc2_Signals": "...",
  "SP6_score": 0.95,
  "SP6_info": "...",
  "pep_prop_len": 632,
  "pep_prop_MW": 70123.4,
  "pep_prop_PI": 6.8
}]
```

**DB Tables:** `ptdb_{inspe}_protein_annotations`, `ptdb_species_summary_with_source`

> Source: `search.html`

---

### 2.4 Get Family by TC Code

```
POST /ptdb/table/get_ptdb_family_by_tc
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `intc` | string | Yes | TC classification code |

**DB Table:** `tc_hierarchy`

**Response:** `[{ "level_3_name": "...", "level_3_id": "...", ... }]`

> Source: `search.html`

---

### 2.5 Get Genome Source by Species

```
POST /ptdb/table/get_ptdb_spe_source_by_spe
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inspe` | string | Yes | Species name |

**DB Table:** `ptdb_fil_species_summary_gca_with_ks`

**Response:** `[{ "Species": "...", "GenomeSource": "...", "GenomeSourceLink": "..." }]`

> Source: `search.html`

---

### 2.6 Get Gene Symbol

```
POST /ptdb/table/get_symbol_by_query_id
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Query ID |

**DB Tables:** `ptdb_all_spe_best_hit_with_des_symbol`, `ptdb_ath_to_symbol`

**Response:** Comma-separated symbol string (e.g., `"ABCG1,ABCG2"`)

> Source: `search.html`

---

### 2.7 Get Pfam Domain Annotations

```
POST /ptdb/table/get_ptdb_pfam_annotations
```

Pfam domain data, protein sequence, transmembrane GFF, and TC info.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ingid` | string | Yes | Protein ID |
| `inspe` | string | Yes | Species name (speID) |

**Response:**

```json
{
  "pfam_data": [{ "pid": "...", "pfam": "...", "pfamID": "...", "pfam_start": ..., "pfam_end": "...", "pep": "..." }],
  "info": [{ "pid": "...", "tc": "...", "speID": "...", ... }]
}
```

**DB Tables:** `ptdb_{inspe}_pfam_annotations`, `ptdb_{inspe}_protein_annotations`

> Source: `search.html`

---

### 2.8 Get Substrate by TC Code

```
POST /ptdb/table/get_ptdb_Substrate_info_by_tc
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `intc` | string | Yes | TC classification code |

**DB Table:** `ptdb_tc_substrates`

**Response:** `[{ "TCcode": "...", "Substrate": "...", "CHEBI": "..." }]`

> Source: `search.html`

---

### 2.9 Get Best Hit in Arabidopsis

```
POST /ptdb/table/get_best_hit_info
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Query ID |

**DB Table:** `ptdb_all_spe_best_hit_with_des_symbol`

**Response:**

```json
[{
  "Arabidopsis_GID": "AT1G01020.1",
  "Arabidopsis_PID": "...",
  "E_value": 1e-150,
  "function": "...",
  "description": "..."
}]
```

> Source: `search.html`

---

## 3. TC Classification APIs

### 3.1 Get TC Code Overview

```
POST /ptdb/Table/get_base1_info_by_tc_code
```

Family, superfamily, description, substrates, and associated symbols for a TC code.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `intc` | string | Yes | TC code (e.g., "3.A.1") |

**Response:**

```json
{
  "family": "...",
  "superfamily": "...",
  "description": "...",
  "substrates": [{ "TCcode": "...", "Substrate": "...", "CHEBI": "..." }],
  "symbols": "..."
}
```

**DB Tables:** `ptdb_tc_info`, `ptdb_ath_to_symbol`, `ptdb_tc_substrates`

> Source: `tc_code.html`

---

### 3.2 Get All Species Data by TC Code

```
POST /ptdb/Table/get_all_base1_info_by_tc_code
```

TC overview plus all species transporter records for the given TC code.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `intc` | string | Yes | TC code |

**Response:**

```json
{
  "family": "...",
  "superfamily": "...",
  "description": "...",
  "substrates": [...],
  "symbols": "...",
  "base_info": [
    { "pid": "...", "speID": "...", "tc": "...", "tid_pfam": "...", "GeneID": "...", "PTPGID": "..." }
  ]
}
```

**DB Tables:** `ptdb_tc_info`, `ptdb_ath_to_symbol`, `ptdb_tc_substrates`, `ptdb_{intc}_all_base_info`

> Source: `tc_code.html`

---

## 4. 3D Structure APIs

### 4.1 Get SwissProt Homology Overlap

```
POST /ptdb/table/get_ptdb_3d_SwissProt_overlap
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Protein ID |
| `inspe` | string | Yes | Species name |

**DB Table:** `ptdb_swissprot_overlap`

**Response:** `[{ "Species": "...", "Protein_ID": "...", "SwissProtID": "...", "Overlap": "...", "Pident": "...", "E_value": "..." }]`

> Source: `search.html`

---

### 4.2 Get AlphaFold Prediction Info

```
POST /ptdb/table/get_ptdb_3d_alphafold_info
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Protein ID |

**DB Table:** `ptdb_15_speices_pLDDT`

**Response:** `[{ "PID": "...", "pLDDT": 92.5, "pTM": 0.85, "PAE": "..." }]`

> Source: `search.html`

---

### 4.3 Get PDB Homology Overlap

```
POST /ptdb/table/get_ptdb_3d_pdb_overlap
```

Best PDB hit by alignment score.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inid` | string | Yes | Protein ID |
| `inspe` | string | Yes | Species name |

**DB Table:** `ptdb_pdb_blast_diamond`

**Response:** `[{ "species": "...", "pid": "...", "pdb_id": "7ABC", "pident": 45.2, "e_value": 1e-50, "qcovhsp": 78.0, "score": 320 }]`

> Source: `search.html`

---

### 4.4 Get PDB Structure Metadata

```
POST /ptdb/table/get_ptdb_pdb_info
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pdbId` | string | Yes | PDB structure ID (e.g., "7ABC") |

**DB Table:** `ptdb_all_pdb_info`

**Response:**

```json
[{
  "Structure_ID": "7ABC",
  "PDB_DOI": "10.2210/pdb...",
  "Title": "...",
  "Organism": "...",
  "Method": "X-RAY DIFFRACTION",
  "Resolution": "2.50 A",
  "R_Value_Work": "...",
  "Authors": "..."
}]
```

> Source: `search.html`

---

## 5. Prediction APIs

### 5.1 Submit Prediction Task

```
POST /ptdb/prediction/submit_prediction
```

**Content-Type:** `multipart/form-data`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `fasta_file` | file | Yes | FASTA file (.fa, .fasta, .txt), max 100MB |
| `species_name` | string | Yes | Species name |
| `email` | string | No | Notification email |

**Response:** `{ "status": "success", "task_id": 1718000000, "message": "..." }`

---

### 5.2 Get Task Status

```
GET /ptdb/prediction/get_task_status?task_id=<task_id>
```

**Status flow:** `pending` → `queued` → `running` → `completed` / `failed`

---

### 5.3 Download Results

```
GET /ptdb/prediction/download_result?task_id=<task_id>&file_type=<type>
```

**file_type:** `summary`, `pfam`, `tc`, `seed`, `tmhmm`, `deeploc`, `reszip` (all)

---

## 6. Agent API

### 6.1 Intelligent Query

```
GET /ptdb/agent/get_ptdb_agent_base_info_by_id?inid=<ID>
```

Accepts any ID type (Protein_ID, PTPGID, AthID, GeneID, mRNAID). Returns comprehensive transporter info in a single call: base annotations, family, AlphaFold, PDB hits.

---

## API Endpoint Summary

| # | Endpoint | Method | Category | Source Page |
|---|----------|--------|----------|-------------|
| 1 | `/ptdb/table/get_ptdb_index_data` | POST | Search | search_index.html |
| 2 | `/ptdb/table/get_ptdb_index_data_by_symbol` | POST | Search | search_index.html |
| 3 | `/ptdb/table/get_ptdb_batch_search_index_data` | POST | Search | search.html |
| 4 | `/ptdb/table/get_ptdb_search_index_data` | POST | Gene Detail | search.html |
| 5 | `/ptdb/table/get_ptdb_spe_by_pid` | POST | Gene Detail | search.html |
| 6 | `/ptdb/table/get_ptdb_protein_annotations` | POST | Gene Detail | search.html |
| 7 | `/ptdb/table/get_ptdb_family_by_tc` | POST | Gene Detail | search.html |
| 8 | `/ptdb/table/get_ptdb_spe_source_by_spe` | POST | Gene Detail | search.html |
| 9 | `/ptdb/table/get_symbol_by_query_id` | POST | Gene Detail | search.html |
| 10 | `/ptdb/table/get_ptdb_pfam_annotations` | POST | Gene Detail | search.html |
| 11 | `/ptdb/table/get_ptdb_Substrate_info_by_tc` | POST | Gene Detail | search.html |
| 12 | `/ptdb/table/get_best_hit_info` | POST | Gene Detail | search.html |
| 13 | `/ptdb/Table/get_base1_info_by_tc_code` | POST | TC Classification | tc_code.html |
| 14 | `/ptdb/Table/get_all_base1_info_by_tc_code` | POST | TC Classification | tc_code.html |
| 15 | `/ptdb/table/get_ptdb_3d_SwissProt_overlap` | POST | 3D Structure | search.html |
| 16 | `/ptdb/table/get_ptdb_3d_alphafold_info` | POST | 3D Structure | search.html |
| 17 | `/ptdb/table/get_ptdb_3d_pdb_overlap` | POST | 3D Structure | search.html |
| 18 | `/ptdb/table/get_ptdb_pdb_info` | POST | 3D Structure | search.html |
| 19 | `/ptdb/prediction/submit_prediction` | POST | Prediction | prediction.html |
| 20 | `/ptdb/prediction/get_task_status` | GET | Prediction | prediction.html |
| 21 | `/ptdb/prediction/download_result` | GET | Prediction | prediction.html |
| 22 | `/ptdb/agent/get_ptdb_agent_base_info_by_id` | GET | Agent | agent page |
