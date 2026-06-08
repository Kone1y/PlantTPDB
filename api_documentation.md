# API Documentation

PTDB provides RESTful APIs for programmatic data access. All endpoints return JSON by default unless otherwise specified.

## Base URL

```
https://your-domain/ptdb/
```

## Authentication

No authentication is required for public data access. Rate limiting may apply for high-frequency requests.

---

## 1. Agent API — Intelligent Query

### Get Transporter Info by ID

Retrieve comprehensive transporter information using any of the supported ID types.

```
GET /ptdb/agent/get_ptdb_agent_base_info_by_id?inid=<ID>
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| inid | string | Yes | One or more IDs (comma-separated). Supported ID types: Protein_ID, PTPGID, AthID, GeneID, mRNAID |

**Example:**

```
GET /ptdb/agent/get_ptdb_agent_base_info_by_id?inid=AT1G01020.1
```

**Response Fields:**

| Field | Description |
|-------|-------------|
| query_id | The queried ID |
| matched_by | Which field matched (Protein_ID, AthID, etc.) |
| GeneID | Gene identifier |
| AthID | Arabidopsis ortholog ID |
| Protein_ID | Protein identifier |
| species | Species name |
| Symbol | Gene symbol |
| base_info | Protein annotations (TC, Pfam, TMHMM, DeepLoc, SignalP, physical properties) |
| family_info | TC family and superfamily |
| 3d_alphafold_prediction_info | AlphaFold pLDDT and pTM scores |
| Blast_with_pdb_info | Best PDB hit with structure reference score |

**Example Response:**

```json
{
  "query_id": "AT1G01020.1",
  "matched_by": "AthID",
  "GeneID": "AT1G01020",
  "AthID": "AT1G01020.1",
  "Protein_ID": "AT1G01020.1",
  "species": "Arabidopsis_thaliana",
  "Symbol": "ABC transporter",
  "base_info": {
    "tc": "3.A.1.1.1",
    "tc_pident": 95.5,
    "tid_pfam": "PF00005",
    "DeepTM_num": 6,
    "TMHMM2_num": 6,
    "pep_prop_len": 632,
    "pep_prop_MW": 70123.4,
    "pep_prop_PI": 6.8
  },
  "family_info": {
    "tc_code": "3.A.1.1.1",
    "family": "ABC transporter",
    "superfamily": "ABC superfamily"
  }
}
```

---

## 2. Table Query APIs

### Get All Gene Families

```
GET /ptdb/table/gene_family_table
```

Returns a list of all gene families with member counts, sorted by member count (descending).

**Response:** Array of objects with `family`, `gene_num`, `Ath_num` fields.

### Get Species Distribution by Family

```
GET /ptdb/table/gene_family_species_table?infamily=<family_name>
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| infamily | string | Yes | Gene family name |

**Response:**

```json
{
  "with": [{"species": "Arabidopsis_thaliana", "num": 130}],
  "without": ["Oryza_sativa"],
  "num": 15,
  "chart": {
    "species": ["Arabidopsis_thaliana", "..."],
    "num": [130, ...]
  }
}
```

### Get Family Members

```
GET /ptdb/table/gene_family_member_table?infamily=<family_name>
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| infamily | string | Yes | Gene family name |

**Response:** Array of member records with `family`, and all fields from `ath_gene`.

---

## 3. Selection / Filter APIs

### Get Gene Family List

```
GET /ptdb/select/gene_family_select
```

Returns a dropdown-compatible list of gene families.

**Response:**

```json
[
  {"id": "ABC", "text": "ABC"},
  {"id": "MFS", "text": "MFS"}
]
```

### Get Pfam Family List

```
GET /ptdb/select/pfam_family_select
```

Returns a list of Pfam families with IDs.

**Response:**

```json
[
  {"id": "ABC_transporter", "text": "ABC transporter (PF00005)"}
]
```

### Get Species List

```
GET /ptdb/select/species_select
```

Returns all species in the database.

### Get Species List (with "All" option)

```
GET /ptdb/select/species_select1
```

Same as above, with an "All" option prepended.

### Get Transporter Superfamilies

```
GET /ptdb/select/superfamily_get
```

Returns all transporter superfamily classifications.

### Get Transporters by Subfamily

```
GET /ptdb/select/get_ptdb_gene_transporters?infamily=<subfamily_name>
```

### Get Transporters by Pfam

```
GET /ptdb/select/get_ptdb_gene_transporters_by_pfam?inpfam=<pfam_name>
```

### Multi-Sequence Alignment (MAFFT)

```
POST /ptdb/select/get_multi_alignment_seq_data
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| fasta_data | string | Yes | FASTA-formatted sequences |

**Response:**

```json
{
  "success": true,
  "sequence_count": 5,
  "sequences": [
    {"id": "seq1", "sequence": "MAVLG..."}
  ],
  "raw_output": "CLUSTAL format alignment..."
}
```

---

## 4. Prediction APIs

### Submit Prediction Task

```
POST /ptdb/prediction/submit_prediction
```

**Parameters (multipart/form-data):**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| fasta_file | file | Yes | FASTA format protein sequence file (.fa, .fasta, .txt). Max 100MB |
| species_name | string | Yes | Species name (letters, numbers, underscores, spaces, hyphens only) |
| email | string | No | Email for notification |

**Response:**

```json
{
  "status": "success",
  "task_id": 1718000000,
  "message": "Prediction task submitted successfully"
}
```

### Get Task Status

```
GET /ptdb/prediction/get_task_status?task_id=<task_id>
```

**Response:**

```json
{
  "task_id": 1718000000,
  "status": "completed",
  "message": "Task completed successfully",
  "submitted_at": "2026-06-08 10:00:00",
  "updated_at": "2026-06-08 10:05:00"
}
```

Status values: `pending` → `queued` → `running` → `completed` / `failed`

### Get Result Summary

```
GET /ptdb/prediction/get_result_summary?task_id=<task_id>
```

### Get Result Data (Paginated)

```
GET /ptdb/prediction/get_result_data?task_id=<task_id>&result_type=<type>&page=1&limit=50
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| task_id | string | Yes | Task ID |
| result_type | string | No | `base1` (default) or `pfam` |
| page | int | No | Page number (default 1) |
| limit | int | No | Rows per page, 10-1000 (default 50) |

### Download Results

```
GET /ptdb/prediction/download_result?task_id=<task_id>&file_type=<type>
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| task_id | string | Yes | Task ID |
| file_type | string | Yes | `summary`, `pfam`, `tc`, `seed`, `tmhmm`, `deeploc`, or `reszip` (all files) |

### Get Example FASTA

```
GET /ptdb/prediction/get_example_fasta
```

Returns example FASTA sequences for testing the prediction pipeline.

---

## 5. Cross-Reference API

### Arabidopsis to Target Species Gene Mapping

```
GET /ptdb/select/ath2target_gene?ath=<AthID>&spec=<species>&pathway=<pathway>
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| ath | string | Yes | Comma-separated Arabidopsis gene IDs |
| spec | string | Yes | Target species name |
| pathway | string | No | Filter by pathway name |

**Response:** Comma-separated gene IDs from the target species.

---

## Error Handling

All APIs return error responses in the following format:

```json
{
  "status": "error",
  "message": "Description of the error"
}
```

Common HTTP status codes:
- `200`: Success
- `400`: Bad request (missing/invalid parameters)
- `404`: Resource not found
- `500`: Internal server error
