#!/usr/bin/env python3
"""
Judge Semantic Scholar records for plant-transporter relevance via the DeepSeek API (resumable).
"""
import json
import os
import time
import requests

# DeepSeek API configuration
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# File paths: override with FILTER_BASE_DIR; defaults are the current working directory
BASE_DIR = os.environ.get("FILTER_BASE_DIR", ".")
INPUT_FILE = f"{BASE_DIR}/semanticscholar_abstract.json"
OUTPUT_ALL_FILE = f"{BASE_DIR}/semanticscholar_abstract_judged.json"
OUTPUT_FILTERED_FILE = f"{BASE_DIR}/semanticscholar_abstract_is_transporter.json"
PROGRESS_FILE = f"{BASE_DIR}/.semanticscholar_abstract_filter_progress.json"

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
    """
    Call the DeepSeek API to judge one paper.

    Args:
        title: paper title
        abstract: paper abstract

    Returns:
        (is_need, reason): is_need is "yes" or "no"; reason is the judgement basis
    """
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

            # parse the response
            if result.lower().startswith('yes'):
                parts = result.split('|', 1)
                reason = parts[1].strip() if len(parts) > 1 else "relevant to plant transporters"
                return "yes", reason
            elif result.lower().startswith('no'):
                parts = result.split('|', 1)
                reason = parts[1].strip() if len(parts) > 1 else "not relevant to plant transporters"
                return "no", reason
            else:
                # unparseable; return the default
                return "no", f"unparseable API response: {result}"
        else:
            print(f"  API error: {response.status_code} - {response.text}")
            return "no", f"API error: {response.status_code}"

    except Exception as e:
        print(f"  API call error: {e}")
        return "no", f"API call failed: {str(e)}"


def load_progress():
    """Load the resume checkpoint."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            return progress.get('last_index', 0), progress.get('judged_papers', []), progress.get('transporter_papers', [])
        except Exception as e:
            print(f"failed to read checkpoint: {e}; starting from scratch")
            return 0, [], []
    return 0, [], []


def save_progress(last_index, judged_papers, transporter_papers):
    """Save the resume checkpoint."""
    progress = {
        'last_index': last_index,
        'judged_papers': judged_papers,
        'transporter_papers': transporter_papers,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def main():
    """Main entry point."""
    print("="*60)
    print("Semantic Scholar screening tool (resumable)")
    print("="*60)

    # read the literature records
    print(f"\nreading input: {INPUT_FILE}")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        papers = json.load(f)

    total_papers = len(papers)
    print(f"{total_papers} papers to judge")

    # load checkpoint
    start_index, judged_papers, transporter_papers = load_progress()

    if start_index > 0:
        print(f"\ncheckpoint found, resuming from paper {start_index + 1}...")
        print(f"processed: {start_index}")
        print(f"relevant papers so far: {len(transporter_papers)}")
    else:
        print("\nstarting from scratch...")

    print(f"API key: {'set' if DEEPSEEK_API_KEY else 'NOT SET'}")

    if not DEEPSEEK_API_KEY:
        print("\nERROR: DEEPSEEK_API_KEY is not set")
        return

    # continue from the last checkpoint
    for i in range(start_index, total_papers):
        paper = papers[i]
        title = paper.get('title', '')
        abstract = paper.get('abstract', '')

        print(f"\n[{i+1}/{total_papers}] processing: {(title or 'no title')[:60]}...")

        # papers without title/abstract are marked irrelevant but not skipped
        if not title or not abstract:
            # judge by title when a title exists but no abstract
            if title:
                # title only, no abstract
                is_need, reason = judge_paper(title, "no abstract")
                print(f"  result (title only): {is_need} | {reason}")
                paper['is_need'] = is_need
                paper['reason'] = reason
                judged_papers.append(paper)
                if is_need == 'yes':
                    transporter_papers.append(paper)
            else:
                # neither title nor abstract
                print("  no title, marked irrelevant")
                paper['is_need'] = 'no'
                paper['reason'] = 'missing title'
                judged_papers.append(paper)
        else:
            # call the API
            is_need, reason = judge_paper(title, abstract)
            print(f"  result: {is_need} | {reason}")

            # record the judgement
            paper['is_need'] = is_need
            paper['reason'] = reason
            judged_papers.append(paper)

            # if yes, append to the screened list
            if is_need == 'yes':
                transporter_papers.append(paper)

        # save the checkpoint after every paper
        save_progress(i + 1, judged_papers, transporter_papers)

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
    print(f"\n\nsaving all judged records: {OUTPUT_ALL_FILE}")
    with open(OUTPUT_ALL_FILE, 'w', encoding='utf-8') as f:
        json.dump(judged_papers, f, ensure_ascii=False, indent=2)

    print(f"saving plant-transporter-relevant records: {OUTPUT_FILTERED_FILE}")
    with open(OUTPUT_FILTERED_FILE, 'w', encoding='utf-8') as f:
        json.dump(transporter_papers, f, ensure_ascii=False, indent=2)

    # remove the checkpoint file
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print(f"\ncheckpoint removed")

    # summary
    yes_count = sum(1 for p in judged_papers if p.get('is_need') == 'yes')
    no_count = sum(1 for p in judged_papers if p.get('is_need') == 'no')

    print(f"\n" + "="*60)
    print("screening done!")
    print("="*60)
    print(f"total papers: {len(judged_papers)}")
    print(f"plant-transporter relevant: {yes_count} ({yes_count/len(judged_papers)*100:.1f}%)")
    print(f"not relevant: {no_count} ({no_count/len(judged_papers)*100:.1f}%)")
    print(f"\nrelevant records saved to: {OUTPUT_FILTERED_FILE}")
    print(f"all judged records saved to: {OUTPUT_ALL_FILE}")
    print("="*60)


if __name__ == "__main__":
    main()
