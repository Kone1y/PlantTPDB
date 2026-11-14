#!/usr/bin/env python3
"""
Quintuple extractor — processes per-symbol sentence files.
Extracts (head, head_type, relation, tail, tail_type) quintuples from sentences.
"""
import json
import time
import urllib.request
import urllib.parse
from tqdm import tqdm
import os
import re
from pathlib import Path
from collections import defaultdict

# ==================== configuration ====================
# Input/output dirs: overridable via environment variables; defaults are relative to the working directory
SENTENCES_DIR = Path(os.environ.get("SENTENCES_DIR", "sentences"))
TRIPLETS_DIR = Path(os.environ.get("TRIPLETS_DIR", "triplets"))
TRIPLETS_DIR.mkdir(parents=True, exist_ok=True)

# API configuration
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")  # set via environment; not stored in code
DEEPSEEK_ENDPOINT = 'https://api.deepseek.com/chat/completions'
DEEPSEEK_MODEL = 'deepseek-chat'

# processing parameters
MAX_RETRIES = 5
BATCH_SIZE = 5
REQUEST_DELAY = 2
TIMEOUT = 900

# Schema follows the manuscript's PlantTPKG description.
# Sentence-level extraction targets (the relations the prompt asks the model
# to extract from text):
EXTRACTION_RELATIONS = [
    "transports",
    "imports",
    "exports",
    "symports",
    "location"
]

# Full graph-schema relation set: the five extraction targets plus
# `published_in`, which is attached during graph assembly from source-paper
# metadata (DOI) rather than extracted from sentences.
RELATIONS = EXTRACTION_RELATIONS + ["published_in"]

# Entity types — four classes (PlantTPKG schema). The `paper` entity is
# likewise attached at graph assembly and is not an extraction target.
ENTITY_TYPES = [
    "transport_protein",  # transporter protein
    "paper",              # source publication (attached at graph assembly)
    "substrate",          # substrate (ions, lipids, carbohydrates, amino acids, ...)
    "membrane"            # membrane / organelle
]

# load the symbol list to get transporter symbols
def load_transport_proteins():
    """Load the transporter symbol list from symbol_to_categories.json."""
    mapping_file = Path(os.environ.get("SYMBOL_MAPPING_FILE", "symbol_to_categories.json"))
    transport_proteins = []
    if mapping_file.exists():
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping = json.load(f)
            transport_proteins = list(mapping.keys())
    return transport_proteins


def build_prompt(doi_sentences, symbol, doi):
    """Build the API prompt — process all sentences of one DOI."""
    transport_proteins = load_transport_proteins()
    proteins_display = ", ".join(transport_proteins[:20])
    if len(transport_proteins) > 20:
        proteins_display += f", ... and {len(transport_proteins) - 20} more"

    sentences_text = "\n".join([
        f"{i + 1}. {item['sentence']}" for i, item in enumerate(doi_sentences)
    ])

    prompt = f"""You are an expert in membrane transport proteins and their biological functions.

Task: Extract transport protein knowledge graph quintuplets (5-tuples) from the following sentences related to {symbol} transport proteins.

Paper DOI: {doi}
Number of sentences: {len(doi_sentences)}

Rules:
1. Identify transport proteins mentioned in sentences. Priority order:
   a) First, check if the sentence mentions any of these predefined transport proteins:
{proteins_display}
   b) If none match, extract ANY transport protein, transporter, carrier, or channel mentioned in the text (e.g., "AhNRAMP1", "OsPIN1b", "BacA", etc.)

2. RELATION must be one of these exact relation types:
{', '.join(EXTRACTION_RELATIONS)}
   (The graph schema additionally defines a `published_in` relation and `paper`
   entities; they are attached during graph assembly from source-paper metadata
   and are NOT extraction targets — never emit them here.)

3. Determine entity types:
   - "transport_protein": if the entity refers to a protein, transporter, carrier, or channel
   - "substrate": for ANY molecule being transported (ions, lipids, carbohydrates, amino acids, organic acids, nucleotides, peptides, vitamins, drugs, xenobiotics, metals, etc.)
   - "membrane": ONLY for membrane structures and cellular compartments (plasma membrane, vacuolar membrane, chloroplast, mitochondria, endoplasmic reticulum, Golgi, cytoplasm, peroxisome, etc.)
   (the schema's fourth type, "paper", is attached at graph assembly and is not an extraction target)

4. IMPORTANT constraints for "location" relation:
   - The TAIL of "location" relation MUST be a membrane structure or cellular compartment
   - DO NOT extract tissues, organs, or organism-level locations (NO: root, leaf, stem, flower, seed, xylem, phloem, etc.)
   - ONLY extract subcellular locations: plasma membrane, vacuolar membrane, tonoplast, chloroplast envelope, mitochondrial membrane, thylakoid, cytoplasm, etc.

Output requirements:
- Extract only quintuplets with EXPLICIT support from the text.
- Keep entity names exactly as they appear in the text (case-sensitive).
- Output a pure JSON array containing quintuplets with format:
  {{"head": "entity", "head_type": "TYPE", "relation": "RELATION", "tail": "entity", "tail_type": "TYPE"}}
- Entity types must be one of: transport_protein, substrate, membrane
- If NO valid quintuplet is found, output exactly: []
- NO other text, NO markdown, NO explanations, NO code blocks.

Example:
Sentence: The AMT1 gene encodes a transporter that imports ammonium in root cells.
Output: [{{"head": "AMT1", "head_type": "gene", "relation": "imports", "tail": "ammonium", "tail_type": "substrate"}}]

Sentence: AhNRAMP1 encodes a functional manganese and zinc transporter located in plasma membrane.
Output: [{{"head": "AhNRAMP1", "head_type": "gene", "relation": "transports", "tail": "manganese", "tail_type": "substrate"}}, {{"head": "AhNRAMP1", "head_type": "gene", "relation": "transports", "tail": "zinc", "tail_type": "substrate"}}, {{"head": "AhNRAMP1", "head_type": "gene", "relation": "location", "tail": "plasma membrane", "tail_type": "membrane"}}]

Sentence: The ABC transporter protein exports lipids from the cytoplasm.
Output: [{{"head": "ABC transporter", "head_type": "transport_protein", "relation": "exports", "tail": "lipids", "tail_type": "substrate"}}, {{"head": "ABC transporter", "head_type": "transport_protein", "relation": "location", "tail": "cytoplasm", "tail_type": "membrane"}}]

Sentence: BacA imports peptides into the vacuole.
Output: [{{"head": "BacA", "head_type": "transport_protein", "relation": "imports", "tail": "peptides", "tail_type": "substrate"}}, {{"head": "BacA", "head_type": "transport_protein", "relation": "location", "tail": "vacuole", "tail_type": "membrane"}}]

Now extract from these sentences:
{sentences_text}

Output only the JSON array (or [] if nothing found):
"""
    return prompt


def call_deepseek_api(prompt, attempt=1):
    """Call the DeepSeek API for quintuple extraction."""
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert in extracting knowledge graph quintuplets from biological text. Output only valid JSON arrays."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 4096
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        DEEPSEEK_ENDPOINT,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}'
        },
        method='POST'
    )

    for retry in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                if response.status == 200:
                    response_data = response.read().decode('utf-8')
                    result = json.loads(response_data)
                    content = result["choices"][0]["message"]["content"].strip()
                    # strip possible markdown code-fence markers
                    content = content.strip()
                    if content.startswith("```"):
                        content = content[3:]
                    if content.startswith("json"):
                        content = content[4:]
                    if content.startswith("```"):
                        content = content[3:]
                    content = content.strip()
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        print(f"[Attempt {attempt} - JSON parse fail] Content:\n{content[:500]}")
                        if retry < MAX_RETRIES - 1:
                            time.sleep(5 * (retry + 1))
                        continue
                elif response.status == 429:
                    wait = 10 * (2 ** retry)
                    print(f"[429 Rate limit] Attempt {attempt}, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"[HTTP {response.status}] {response_data[:200]}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (2 ** retry)
                print(f"[429 Rate limit] Attempt {attempt}, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"[HTTP {e.code}] {e.read().decode('utf-8')[:200]}")
        except urllib.error.URLError as e:
            print(f"[URL Error] {e}")
            time.sleep(5 * (retry + 1))
        except Exception as e:
            print(f"[Exception] {e}")
            time.sleep(5 * (retry + 1))

    print(f"[Final fail] Attempt {attempt} skipped")
    return []


def process_symbol_file(sentences_file):
    """Process one symbol's sentences file, grouped by DOI."""
    # symbol name
    symbol = sentences_file.stem.replace("_sentences", "")

    # read sentences
    with open(sentences_file, 'r', encoding='utf-8') as f:
        sentences = json.load(f)

    print(f"Processing {symbol}: {len(sentences)} sentences")

    # group sentences by DOI
    doi_groups = defaultdict(list)
    for sent in sentences:
        doi = sent.get("paper_id", "unknown")
        doi_groups[doi].append(sent)

    print(f"  Grouped into {len(doi_groups)} DOIs")

    # output file
    output_file = TRIPLETS_DIR / f"{symbol}.jsonl"
    checkpoint_file = TRIPLETS_DIR / f".{symbol}_checkpoint"

    # checkpoint: record processed DOIs
    processed_dois = set()
    if checkpoint_file.exists():
        with open(checkpoint_file, "r") as f:
            content = f.read().strip()
            processed_dois = set(content.split("\n")) if content else set()
        print(f"  Resuming from {len(processed_dois)} processed DOIs...")

    total_extracted = 0

    # process per DOI
    doi_list = sorted(doi_groups.keys())
    with open(output_file, "a" if processed_dois else "w", encoding='utf-8') as fout:
        for doi in tqdm(doi_list, desc=f"  {symbol} DOIs"):
            if doi in processed_dois:
                continue

            doi_sentences = doi_groups[doi]

            # call the API for all sentences of this DOI
            prompt = build_prompt(doi_sentences, symbol, doi)
            results = call_deepseek_api(prompt, attempt=doi)

            # attach source_paper_id to every extracted quintuple
            for item in results:
                item["source_paper_id"] = doi
                fout.write(json.dumps(item, ensure_ascii=False) + "\n")
                total_extracted += 1

            # update the checkpoint
            processed_dois.add(doi)
            with open(checkpoint_file, "w") as f:
                f.write("\n".join(processed_dois))

            # rate-limit delay
            time.sleep(REQUEST_DELAY)

    # remove the checkpoint file
    if checkpoint_file.exists():
        checkpoint_file.unlink()

    print(f"  Extracted {total_extracted} quintuplets -> {output_file.name}")
    return symbol, len(sentences), total_extracted


def main():
    """Main: process all symbol sentence files."""
    # collect all sentences files
    sentences_files = sorted(SENTENCES_DIR.glob("*_sentences.json"))
    print(f"Found {len(sentences_files)} sentence files")
    print(f"Entity types: {', '.join(ENTITY_TYPES)}")
    print(f"Relations: {', '.join(RELATIONS)}")
    print(f"Processing by DOI, Delay: {REQUEST_DELAY}s")
    print("-" * 60)

    # statistics
    all_stats = []

    for sentences_file in sentences_files:
        try:
            symbol, sent_count, triplet_count = process_symbol_file(sentences_file)
            all_stats.append((symbol, sent_count, triplet_count))
        except Exception as e:
            print(f"Error processing {sentences_file}: {e}")
            continue

    # summary
    print("\n" + "=" * 60)
    print("All processing completed!")
    total_sents = sum(s[1] for s in all_stats)
    total_triplets = sum(s[2] for s in all_stats)
    print(f"  Total symbols: {len(all_stats)}")
    print(f"  Total sentences: {total_sents}")
    print(f"  Total quintuplets: {total_triplets}")
    print(f"  Average triplets per sentence: {total_triplets/total_sents:.2f}")
    print(f"  Output directory: {TRIPLETS_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
