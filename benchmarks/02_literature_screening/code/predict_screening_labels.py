#!/usr/bin/env python3
"""
Judge two batches of literature records for plant-transporter relevance via the DeepSeek API (resumable).
- data/benchmark_records.jsonl -> raw judge output under predictions/
"""
import json
import os
import time
import requests

# DeepSeek API configuration
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# File paths: default to this package's layout (parent of code/, i.e. where data/ and results/ live);
# override with JUDGE_BASE_DIR
BASE_DIR = os.environ.get(
    "JUDGE_BASE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

TASKS = [
    {
        "input": f"{BASE_DIR}/data/benchmark_records.jsonl",
        "output": f"{BASE_DIR}/predictions/llm_judge_raw.jsonl",
        "progress": f"{BASE_DIR}/predictions/.llm_judge_raw.progress.json",
    },
]

# Prompt
SYSTEM_PROMPT = """You are an expert bioinformatician judging whether an academic publication is relevant to plant transporters.

Criteria (all must be strictly met):
1. The study organism must be a plant.
2. The study must concern transport proteins (transporter, carrier, channel, etc.).
3. Relevant topics include, but are not limited to:
   - Any transporters in plants (ABC transporters, NRT, SUT, APT, etc.)
   - Plant nutrient transport (nitrate, phosphate, potassium, sucrose, etc.)
   - Transport of plant secondary metabolites
   - Plant hormone transport
   - Plant heavy-metal transport/resistance

Not relevant:
- Studies of bacteria, fungi, or animals (unless explicitly compared with plants)
- Purely gene-expression analyses that do not involve transport function
- Purely computational simulations of transporters without plant experimental validation
- Other types of plant proteins (non-transporters)

Output format (strictly follow):
If relevant: yes|brief reason (max 50 words)
If not relevant: no|brief reason (max 50 words)

Examples:
yes|Study of the dual-affinity switching mechanism of the Arabidopsis nitrate transporter NRT1.1
yes|Cloning and characterization of the grapevine ABC transporter VvABCB15
no|Study of membrane transport mechanisms in Escherichia coli; not a plant
no|Study of plant enzymatic activity; no transporter involvement
"""


def judge_paper(title, abstract):
    """Call the DeepSeek API to judge one paper."""
    user_prompt = f"""Judge whether the following publication is relevant to plant transporters:

Title: {title}

Abstract: {abstract}

Strictly follow the output format: yes|reason or no|reason"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 200
    }

    try:
        response = requests.post(
            f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=60
        )

        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content'].strip()

            if result.lower().startswith('yes'):
                parts = result.split('|', 1)
                reason = parts[1].strip() if len(parts) > 1 else "relevant to plant transporters"
                return "yes", reason
            elif result.lower().startswith('no'):
                parts = result.split('|', 1)
                reason = parts[1].strip() if len(parts) > 1 else "not relevant to plant transporters"
                return "no", reason
            else:
                return "no", f"unparseable API response: {result}"
        else:
            print(f"  API error: {response.status_code} - {response.text}")
            return "no", f"API error: {response.status_code}"

    except Exception as e:
        print(f"  API call error: {e}")
        return "no", f"API call failed: {str(e)}"


def load_progress(progress_file):
    """Load the resume checkpoint."""
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            return progress.get('last_index', 0), progress.get('judged_papers', [])
        except Exception as e:
            print(f"  failed to read checkpoint: {e}; starting from scratch")
            return 0, []
    return 0, []


def save_progress(progress_file, last_index, judged_papers):
    """Save the resume checkpoint."""
    progress = {
        'last_index': last_index,
        'judged_papers': judged_papers,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def process_task(task):
    """Process one batch task."""
    input_file = task["input"]
    output_file = task["output"]
    progress_file = task["progress"]

    print("=" * 60)
    print(f"task: {os.path.basename(input_file)} -> {os.path.basename(output_file)}")
    print("=" * 60)

    # read the literature records
    print(f"\nreading input: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        papers = [json.loads(line) for line in f if line.strip()]

    total_papers = len(papers)
    print(f"{total_papers} papers to judge")

    # load checkpoint
    start_index, judged_papers = load_progress(progress_file)

    if start_index > 0:
        print(f"\ncheckpoint found, resuming from paper {start_index + 1}...")
        print(f"processed: {start_index}")
    else:
        print("\nstarting from scratch...")

    # continue from the last checkpoint
    for i in range(start_index, total_papers):
        paper = papers[i]
        title = paper.get('title', '')
        abstract = paper.get('abstract', '')

        print(f"\n[{i+1}/{total_papers}] processing: {(title or 'no title')[:60]}...")

        if not title or not abstract:
            if title:
                is_need, reason = judge_paper(title, "no abstract")
                print(f"  result (title only): {is_need} | {reason}")
                paper['is_need'] = is_need
                paper['reason'] = reason
                judged_papers.append(paper)
            else:
                print("  no title, marked irrelevant")
                paper['is_need'] = 'no'
                paper['reason'] = 'missing title'
                judged_papers.append(paper)
        else:
            is_need, reason = judge_paper(title, abstract)
            print(f"  result: {is_need} | {reason}")
            paper['is_need'] = is_need
            paper['reason'] = reason
            judged_papers.append(paper)

        # save the checkpoint after every paper
        save_progress(progress_file, i + 1, judged_papers)

        # 1 s between requests (API rate limit)
        time.sleep(1)

        # progress summary every 10 papers
        if (i + 1) % 10 == 0:
            yes_count = sum(1 for p in judged_papers if p.get('is_need') == 'yes')
            no_count = sum(1 for p in judged_papers if p.get('is_need') == 'no')
            print(f"\n  progress:")
            print(f"    processed: {i+1}/{total_papers} ({(i+1)/total_papers*100:.1f}%)")
            print(f"    relevant: {yes_count} | irrelevant: {no_count}")

    # save final results
    print(f"\nsaving predictions: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        for rec in judged_papers:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # remove the checkpoint file
    if os.path.exists(progress_file):
        os.remove(progress_file)
        print(f"checkpoint removed")

    # summary
    yes_count = sum(1 for p in judged_papers if p.get('is_need') == 'yes')
    no_count = sum(1 for p in judged_papers if p.get('is_need') == 'no')

    print(f"\n{'='*60}")
    print(f"task done: {os.path.basename(input_file)}")
    print(f"  total papers: {len(judged_papers)}")
    print(f"  plant-transporter relevant: {yes_count} ({yes_count/len(judged_papers)*100:.1f}%)")
    print(f"  not relevant: {no_count} ({no_count/len(judged_papers)*100:.1f}%)")
    print(f"{'='*60}\n")


def main():
    print("=" * 60)
    print("Batch plant-transporter literature judge (resumable)")
    print(f"API key: {'set' if DEEPSEEK_API_KEY else 'NOT SET'}")
    print("=" * 60)

    if not DEEPSEEK_API_KEY:
        print("\nERROR: DEEPSEEK_API_KEY is not set")
        return

    for idx, task in enumerate(TASKS, 1):
        print(f"\n{'#'*60}")
        print(f"# task {idx}/{len(TASKS)}")
        print(f"{'#'*60}")
        process_task(task)

    print("\nall tasks complete!")


if __name__ == "__main__":
    main()
