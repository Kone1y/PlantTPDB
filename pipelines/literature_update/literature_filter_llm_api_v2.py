#!/usr/bin/env python3
"""
Intelligent Literature Filter for Plant Transporters using External LLM API (v2)
Uses OpenAI SDK for LLM interaction (supports OpenAI, DeepSeek, etc.)
"""

import argparse
import json
import os
import re
import sys
import time
from typing import List, Dict, Tuple, Optional
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    from openai import OpenAI
except ImportError:
    print("Error: the 'openai' package is missing; install it with: pip install openai")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    # Minimal fallback progress bar when tqdm is not installed
    class tqdm:
        def __init__(self, total, desc="", unit="it"):
            self.total = total
            self.desc = desc
            self.unit = unit
            self.n = 0
            self.last_print = 0

        def update(self, n=1):
            self.n += n
            # Print roughly every 5% progress
            if self.n - self.last_print >= max(1, self.total // 20) or self.n == self.total:
                pct = (self.n / self.total * 100) if self.total > 0 else 0
                print(f"  {self.desc}: {self.n}/{self.total} ({pct:.0f}%)")
                self.last_print = self.n

        def close(self):
            if self.n == self.total and self.n > 0:
                print(f"  {self.desc}: done")

# Default API configuration
DEFAULT_API_URL = "https://api.deepseek.com"
DEFAULT_API_KEY = "sk-your-api-key-here"  # replace with your API key
DEFAULT_MODEL = "deepseek-chat"

# Environment variable overrides
USE_LLM_API = os.environ.get('USE_LLM_API', 'false').lower() == 'true'
LLM_API_URL = os.environ.get('LLM_API_URL', DEFAULT_API_URL)
LLM_API_KEY = os.environ.get('LLM_API_KEY', DEFAULT_API_KEY)
LLM_MODEL = os.environ.get('LLM_MODEL', DEFAULT_MODEL)

# Request settings
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Concurrency settings
BATCH_SIZE = 10  # articles per batch
MAX_WORKERS = 5  # worker threads


class LLMAPIClient:
    """Client for calling external LLM API using OpenAI SDK"""

    def __init__(self, api_url: str = None, api_key: str = None, model: str = None):
        self.api_url = api_url or LLM_API_URL
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_url
        )

        if self.api_key == DEFAULT_API_KEY or self.api_url == DEFAULT_API_URL:
            print("Warning: default placeholder configuration in use; set environment variables or edit the script")
            print(f"API_URL: {self.api_url}")
            print(f"API_KEY: {self.api_key[:10]}...{self.api_key[-4:]}")
            print(f"MODEL: {self.model}")

    def call_model(self, prompt: str, max_tokens: int = 300) -> Optional[Dict]:
        """
        Call external LLM API using OpenAI SDK
        Returns: dict with 'is_related', 'score', 'evidence_level', 'reason', 'brief'
        """
        try:
            messages = [
                {
                    'role': 'system',
                    'content': '''# Role
You are an expert in plant transporter literature analysis, responsible for accurately identifying publications of research value from large volumes of abstracts.

## Core Task
Assess the research relevance of a publication to "plant transporters" and give a relevance score from 0 to 10.

## Stage 1: Attribute Scan (multi-dimensional check)
Evaluate the publication against the following dimensions:
1. **Research subject:** Is it a plant (Arabidopsis, rice, maize, wheat, soybean, Brassicaceae, etc.)?
2. **Protein type:** Does it involve transporters, ion channels, carriers, pumps, or related membrane proteins?
3. **Evidence level:**
   - [Level A] Experimental validation (electrophysiology, isotope studies, phenotypic complementation, subcellular localization, etc.)
   - [Level B] Targeted characterization (genome-wide identification, evolutionary analysis, or structure modeling dedicated to a specific family)
   - [Level C] Background mention (mentioned only as a pathway member or as background knowledge)
   - [Level D] Omics-only (appears only in a differential expression list or supplementary data)

## Stage 2: Scoring Criteria
- **9-10 (Highly Relevant):** Explicitly studies the function, expression regulation, or molecular mechanism of a plant transporter, with experimental evidence.
- **7-8 (Potentially Relevant):** Genome-wide systematic identification, structure determination, or evolutionary analysis dedicated to a transporter family (e.g., NRT, SULTR, AMT).
- **4-6 (Low Relevance):** Mentions transporters, but the research focus is on downstream metabolites or other pathways, or provides only preliminary transcriptomic associations.
- **0-3 (Irrelevant):** Animal/clinical studies, pharmacological studies of plant extracts, purely metabolic studies, non-transporter proteins.

**Special caution: give a low score (0-6) in the following cases even if transporters are mentioned:**
- Nanomaterial/hormone treatment experiments in which transporters are measured only as a response marker
- Transcription factor/signaling protein studies in which transporters are merely one of several downstream regulatory targets

## Stage 3: Exclusion Red Lines (any single hit means rejection)
- Non-plant studies (exception: heterologous expression of Arabidopsis proteins in yeast/oocytes).
- Analysis of pure chemical extracts that does not involve membrane transport mechanisms.
- Studies that mention "transporter" but actually investigate signal transduction without any transport-function experiments.

## Response Format
The answer must be valid JSON containing the following fields:
- is_related: boolean; true means relevant (score >= 6), false means not relevant (score < 6)
- score: relevance score from 0 to 10
- evidence_level: evidence level "A"/"B"/"C"/"D"
- reason: brief explanation (1-2 sentences)
- brief: short commentary on the publication (research subject and main conclusion)'''
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ]

            for attempt in range(MAX_RETRIES):
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.1,
                        max_tokens=max_tokens,
                        timeout=REQUEST_TIMEOUT
                    )

                    content = response.choices[0].message.content.strip()

                    parsed = self._parse_response(content)
                    if parsed:
                        return parsed
                    else:
                        print(f"  [Warning] could not parse LLM response: {content[:100]}")
                        return None

                except Exception as e:
                    error_str = str(e)

                    # Rate limiting
                    if 'rate' in error_str.lower() or '429' in error_str:
                        if attempt < MAX_RETRIES - 1:
                            wait_time = RETRY_DELAY * (attempt + 1)
                            print(f"  [Warning] API rate limited, waiting {wait_time} seconds before retrying...")
                            time.sleep(wait_time)
                            continue
                        else:
                            print(f"  [Error] API rate limited, max retries reached")
                            return None

                    # Timeout
                    elif 'timeout' in error_str.lower():
                        if attempt < MAX_RETRIES - 1:
                            print(f"  [Warning] API request timed out, retrying ({attempt + 1}/{MAX_RETRIES})...")
                            time.sleep(RETRY_DELAY)
                            continue
                        else:
                            print(f"  [Error] API request timed out, max retries reached")
                            return None

                    # Authentication failure
                    elif 'auth' in error_str.lower() or '401' in error_str:
                        print(f"  [Error] API authentication failed, please check the API key")
                        return None

                    # Any other error
                    else:
                        if attempt < MAX_RETRIES - 1:
                            print(f"  [Warning] API request error: {e}, retrying ({attempt + 1}/{MAX_RETRIES})...")
                            time.sleep(RETRY_DELAY)
                            continue
                        else:
                            print(f"  [Error] API request error: {e}")
                            return None

            return None

        except Exception as e:
            print(f"  [Error] LLM call failed: {e}")
            return None

    def _parse_response(self, content: str) -> Optional[Dict]:
        """
        Parse LLM response to extract JSON
        Handles various response formats
        """
        # Strip markdown code fences if present
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned)
        cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)

            # Validate required fields
            if 'is_related' not in parsed:
                print(f"  [Warning] LLM response is missing the 'is_related' field")
                return None

            return {
                'is_related': parsed.get('is_related', False),
                'score': parsed.get('score', 0),
                'evidence_level': parsed.get('evidence_level', 'D'),
                'reason': parsed.get('reason', 'no reason provided'),
                'brief': parsed.get('brief', '')
            }

        except json.JSONDecodeError as e:
            # Try to extract a JSON object from surrounding text
            json_match = re.search(r'\{[^{}]*"is_related"[^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(0))
                    if 'is_related' in parsed:
                        return {
                            'is_related': parsed.get('is_related', False),
                            'score': parsed.get('score', 0),
                            'evidence_level': parsed.get('evidence_level', 'D'),
                            'reason': parsed.get('reason', 'no reason provided'),
                            'brief': parsed.get('brief', '')
                        }
                except json.JSONDecodeError:
                    pass

            print(f"  [Warning] could not parse JSON: {e}")
            print(f"  Raw content: {cleaned[:200]}")
            return None

    def call_model_batch(self, articles: List[Dict], max_tokens: int = 800) -> List[Optional[Dict]]:
        """
        Process multiple articles in a single API call (more efficient)
        Returns a list with one assessment per article
        """
        if not articles:
            return []

        batch_prompt = "Assess the research relevance of the following publications to \"plant transporters\".\n\n"
        batch_prompt += f"There are {len(articles)} publications in total; return an independent assessment for each one.\n\n"
        batch_prompt += "Response format: must be a valid JSON array where each element contains the following fields:\n"
        batch_prompt += "- is_related: boolean\n"
        batch_prompt += "- score: 0-10 relevance score\n"
        batch_prompt += "- evidence_level: evidence level \"A\"/\"B\"/\"C\"/\"D\"\n"
        batch_prompt += "- reason: brief explanation\n\n"

        for i, article in enumerate(articles, 1):
            title = article.get('title', '')
            journal = article.get('journal', '')
            abstract = article.get('abstract', '')[:800]  # truncate to control prompt length

            batch_prompt += f"\n--- Publication {i} ---\n"
            batch_prompt += f"Title: {title}\n"
            batch_prompt += f"Journal: {journal}\n"
            batch_prompt += f"Abstract: {abstract}\n"

        batch_prompt += "\nReturn the assessment results for all publications as a JSON array:\n"
        batch_prompt += "[{\"is_related\": true, \"score\": 8, \"evidence_level\": \"B\", \"reason\": \"...\"}, ...]"

        try:
            messages = [
                {
                    'role': 'system',
                    'content': '''# Role
You are an expert in plant transporter literature analysis, responsible for batch-assessing the research relevance of multiple publications.

## Assessment Criteria
- **9-10:** Explicitly studies plant transporter function/mechanism, with experimental evidence
- **7-8:** Systematic identification/evolutionary analysis of a transporter family
- **4-6:** Mentions transporters but focuses on other pathways
- **0-3:** Animal studies, pharmacological studies, non-transporter proteins

## Exclusion Red Lines
- Non-plant studies (heterologous expression excepted)
- Analysis of pure chemical extracts
- Signal transduction studies (transporters are merely downstream targets)

## Response Requirements
You must return a valid JSON array with one element per publication.'''
                },
                {
                    'role': 'user',
                    'content': batch_prompt
                }
            ]

            for attempt in range(MAX_RETRIES):
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.1,
                        max_tokens=max_tokens,
                        timeout=REQUEST_TIMEOUT
                    )

                    content = response.choices[0].message.content.strip()
                    results = self._parse_batch_response(content, len(articles))

                    if results:
                        return results
                    else:
                        print(f"  [Warning] batch parsing failed, falling back to per-article processing")
                        return [self.call_model(self._article_to_prompt(a), max_tokens) for a in articles]

                except Exception as e:
                    error_str = str(e)

                    # Rate limiting
                    if 'rate' in error_str.lower() or '429' in error_str:
                        if attempt < MAX_RETRIES - 1:
                            wait_time = RETRY_DELAY * (attempt + 1)
                            print(f"  [Warning] API rate limited, waiting {wait_time} seconds before retrying...")
                            time.sleep(wait_time)
                            continue
                        else:
                            print(f"  [Error] API rate limited, falling back to per-article processing")
                            return [self.call_model(self._article_to_prompt(a), max_tokens) for a in articles]

                    # Timeout
                    elif 'timeout' in error_str.lower():
                        if attempt < MAX_RETRIES - 1:
                            print(f"  [Warning] batch request timed out, retrying...")
                            time.sleep(RETRY_DELAY)
                            continue
                        else:
                            print(f"  [Error] batch request timed out, falling back to per-article processing")
                            return [self.call_model(self._article_to_prompt(a), max_tokens) for a in articles]

                    # Any other error
                    else:
                        if attempt < MAX_RETRIES - 1:
                            print(f"  [Warning] batch request error: {e}, retrying...")
                            time.sleep(RETRY_DELAY)
                            continue
                        else:
                            print(f"  [Error] batch request error, falling back to per-article processing")
                            return [self.call_model(self._article_to_prompt(a), max_tokens) for a in articles]

            return [None] * len(articles)

        except Exception as e:
            print(f"  [Error] batch call failed: {e}")
            return [self.call_model(self._article_to_prompt(a), max_tokens) for a in articles]

    def _article_to_prompt(self, article: Dict) -> str:
        """Build the user prompt for a single article"""
        title = article.get('title', '')
        journal = article.get('journal', '')
        abstract = article.get('abstract', '')[:1500]

        return f"""Assess the research relevance of the following publication to "plant transporters".

Title: {title}
Journal: {journal}
Abstract: {abstract}

Give a relevance score from 0 to 10 according to the scoring criteria and determine the evidence level (A/B/C/D).

Return JSON in this format: {{"is_related": true/false, "score": 0-10, "evidence_level": "A/B/C/D", "reason": "brief explanation", "brief": "short commentary"}}"""

    def _parse_batch_response(self, content: str, expected_count: int) -> Optional[List[Dict]]:
        """Parse a batch LLM response"""
        # Strip markdown code fences if present
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned)
        cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)

            # Ensure the payload is an array
            if isinstance(parsed, dict):
                # May be wrapped as {"results": [...]}
                if 'results' in parsed:
                    parsed = parsed['results']
                else:
                    # A single result wrapped as an object
                    parsed = [parsed]

            if not isinstance(parsed, list):
                print(f"  [Warning] batch response is not a JSON array")
                return None

            # Validate the count
            if len(parsed) != expected_count:
                print(f"  [Warning] count mismatch: expected {expected_count}, got {len(parsed)}")
                # Pad or truncate to the expected count
                while len(parsed) < expected_count:
                    parsed.append({'is_related': False, 'score': 0, 'evidence_level': 'D', 'reason': 'parsing failed', 'brief': ''})
                parsed = parsed[:expected_count]

            # Normalize each result
            results = []
            for item in parsed:
                results.append({
                    'is_related': item.get('is_related', False),
                    'score': item.get('score', 0),
                    'evidence_level': item.get('evidence_level', 'D'),
                    'reason': item.get('reason', 'no reason provided'),
                    'brief': item.get('brief', '')
                })

            return results

        except json.JSONDecodeError as e:
            print(f"  [Warning] could not parse batch JSON: {e}")
            print(f"  Raw content: {cleaned[:200]}")
            return None


class IntelligentTransporterFilter:
    """Intelligent filter for plant transporter-related literature using LLM API"""

    # Precompiled class-level regexes for performance
    # Strict exclusion patterns - only medical terms clearly unrelated to plant transporters;
    # generic terms such as human/mouse/rat are deliberately omitted to avoid rejecting
    # legitimate plant science papers
    EXCLUDE_RE = re.compile(
        r'\b(clinical trial|oncology|patient|hospital|pharmacokinetics|'
        r'drug delivery|cancer therapy|treatment efficacy)\b',
        re.IGNORECASE
    )

    # Plant keywords - word boundaries prevent substring false positives
    # (e.g. "implantation" must not match "plant")
    PLANT_RE = re.compile(
        r'\b(plants?|arabidopsis|rice|maize|wheat|zea mays|oryza|'
        r'nicotiana|tobacco|physcomitrium|moss|fern|pteridophyte|'
        r'brachypodium|solanum|photosynthetic|chloroplast|'
        r'phyllid|gametophyte)\b',
        re.IGNORECASE
    )

    # Transporter keywords - word boundaries prevent substring false positives
    TRANSPORTER_RE = re.compile(
        r'\b(transporter|channel|carrier|pump|abc|aquaporin|mip|'
        r'slt|hkt|pin|kup|kat1|nhx|clca|'
        r'sugar transporter|nitrate transporter|amino acid transporter|'
        r'glutamate transporter|auxin efflux carrier|'
        r'membrane transport|ion homeostasis|nutrient uptake|'
        r'ion transport|cation channel|anion channel|'
        r'solute carrier|ion flux|transport protein)\b',
        re.IGNORECASE
    )

    # Context patterns - detect a strong association between plant and transporter terms
    CONTEXT_RE = re.compile(
        r'\b(plants?.{0,20}transporter|'
        r'transporter.{0,20}(in|of).{0,10}(plants?|arabidopsis|rice|maize)|'
        r'channel.{0,20}in.{0,10}plants?|'
        r'(at|os|zm)[a-z0-9]+\b)',  # plant gene identifier prefixes
        re.IGNORECASE
    )

    def __init__(self, use_llm_api=False, api_url=None, api_key=None, model=None):
        self.use_llm_api = use_llm_api

        if use_llm_api:
            self.llm_client = LLMAPIClient(api_url, api_key, model)
        else:
            self.llm_client = None

        self.approved = []
        self.rejected = []

    def normalize_text(self, text: str) -> str:
        """Normalize text for analysis"""
        if not text:
            return ""
        return text.lower().strip()

    def check_keyword_filters(self, title: str, abstract: str, journal: str) -> Tuple[bool, str]:
        """Check using keyword-based filtering (fallback/fast mode)"""
        text = self.normalize_text(title + " " + abstract + " " + journal)

        # 1. Strict exclusion - only explicit medical/clinical terms
        exclude_match = self.EXCLUDE_RE.search(text)
        if exclude_match:
            return False, f"[keyword exclude] matched medical/clinical term: {exclude_match.group()}"

        # 2. Require a plant keyword
        if not self.PLANT_RE.search(text):
            return False, "[keyword exclude] missing plant-related keywords"

        # 3. Require a transporter keyword
        if not self.TRANSPORTER_RE.search(text):
            return False, "[keyword exclude] missing transporter-related keywords"

        # 4. Context check - look for a strong plant-transporter association
        has_context = self.CONTEXT_RE.search(text) is not None

        if has_context:
            return True, "[keyword pass] strong plant-transporter association found"

        # Both keyword groups present but not strongly linked: weak match
        return True, "[keyword pass] contains basic keywords (weak association)"

    def check_with_llm_api(self, title: str, abstract: str, journal: str) -> Tuple[bool, str]:
        """Check using external LLM API semantic understanding"""
        if not self.use_llm_api or not self.llm_client:
            # Fall back to keyword mode when the LLM API is not configured
            return self.check_keyword_filters(title, abstract, journal)

        analysis_text = f"""Assess the research relevance of the following publication to "plant transporters".

Title: {title}
Journal: {journal}
Abstract: {abstract[:1500] if len(abstract) > 1500 else abstract}

Give a relevance score from 0 to 10 according to the scoring criteria and determine the evidence level (A/B/C/D).

Return JSON in this format: {{"is_related": true/false, "score": 0-10, "evidence_level": "A/B/C/D", "reason": "brief explanation", "brief": "short commentary"}}"""

        result = self.llm_client.call_model(analysis_text)

        if result:
            is_related = result.get('is_related', False)
            score = result.get('score', 0)
            evidence_level = result.get('evidence_level', 'D')
            reason = result.get('reason', 'could not parse reason')
            brief = result.get('brief', '')

            # Force the verdict from the score: only score >= 7 counts as related
            is_related = score >= 7

            status = "pass" if is_related else "excluded"
            return is_related, f"[LLM API score:{score}/10 evidence level:{evidence_level}] {status}: {reason}"
        else:
            # LLM call failed; fall back to keyword mode
            return self.check_keyword_filters(title, abstract, journal)

    def filter_article(self, article: Dict) -> Tuple[bool, str]:
        """Filter a single article"""
        title = article.get('title', '')
        abstract = article.get('abstract', '')
        journal = article.get('journal', '')
        doi = article.get('doi', '')

        if self.use_llm_api and self.llm_client:
            approved, reason = self.check_with_llm_api(title, abstract, journal)
        else:
            approved, reason = self.check_keyword_filters(title, abstract, journal)

        article_copy = article.copy()
        article_copy['filter_reason'] = reason

        return approved, reason

    def filter_articles(self, articles: List[Dict]) -> Dict:
        """Filter all articles with batch processing and concurrent requests"""
        results = {
            'approved': [],
            'rejected': []
        }

        mode = "LLM API intelligent mode (batched & concurrent)" if self.use_llm_api else "keyword fast mode"
        print(f"\nFiltering {len(articles)} articles with {mode}...")

        if not self.use_llm_api or not self.llm_client:
            # Keyword mode: process articles one by one
            print(f"\n  [keyword mode] starting filtering...\n")
            with tqdm(total=len(articles), desc="📄 Filtering progress", unit="articles") as pbar:
                for article in articles:
                    approved, reason = self.check_keyword_filters(
                        article.get('title', ''),
                        article.get('abstract', ''),
                        article.get('journal', '')
                    )

                    article_copy = article.copy()
                    article_copy['filter_reason'] = reason

                    if approved:
                        results['approved'].append(article_copy)
                    else:
                        results['rejected'].append(article_copy)

                    pbar.update(1)

        else:
            # LLM API mode: batched concurrent processing
            print(f"  [config] batch size: {BATCH_SIZE} articles/batch")
            print(f"  [config] worker threads: {MAX_WORKERS}\n")

            batches = [articles[i:i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]
            total_batches = len(batches)

            lock = threading.Lock()

            def process_batch(batch_args):
                batch_idx, batch = batch_args
                try:
                    batch_results = self.llm_client.call_model_batch(batch)

                    processed = []
                    for article, result in zip(batch, batch_results):
                        article_copy = article.copy()

                        if result:
                            is_related = result.get('is_related', False)
                            score = result.get('score', 0)
                            evidence_level = result.get('evidence_level', 'D')
                            reason = result.get('reason', 'no reason provided')

                            # Force the verdict from the score: only score >= 7 counts as related
                            is_related = score >= 7
                            status = "pass" if is_related else "excluded"
                            article_copy['filter_reason'] = f"[LLM score:{score}/10 level:{evidence_level}] {status}: {reason}"
                        else:
                            # LLM call failed; fall back to keyword filtering
                            article_copy['filter_reason'] = "[keyword mode] LLM call failed"

                        processed.append(article_copy)

                    return batch_idx, processed
                except Exception as e:
                    print(f"  [Error] batch {batch_idx + 1} failed: {e}")
                    return batch_idx, []

            with tqdm(total=len(articles), desc="📄 Filtering progress", unit="articles") as pbar:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_batch = {
                        executor.submit(process_batch, (i, batch)): i
                        for i, batch in enumerate(batches)
                    }

                    completed_batches = {}
                    for future in as_completed(future_to_batch):
                        batch_idx = future_to_batch[future]
                        try:
                            idx, processed = future.result()
                            completed_batches[batch_idx] = processed

                            batch_size = len(batches[batch_idx])
                            pbar.update(batch_size)

                        except Exception as e:
                            print(f"  [Error] batch {batch_idx + 1} raised an exception: {e}")
                            pbar.update(BATCH_SIZE)

            # Merge results in original batch order
            for i in range(total_batches):
                if i in completed_batches:
                    for article_copy in completed_batches[i]:
                        reason = article_copy.get('filter_reason', '')

                        # Classify approved vs rejected from the reason string
                        if '[LLM' in reason and 'excluded' in reason:
                            results['rejected'].append(article_copy)
                        elif 'pass' in reason or 'contains basic keywords' in reason:
                            results['approved'].append(article_copy)
                        else:
                            # Unknown status; re-check with keyword filtering as a fallback
                            title = article_copy.get('title', '')
                            abstract = article_copy.get('abstract', '')
                            journal = article_copy.get('journal', '')
                            approved, kw_reason = self.check_keyword_filters(title, abstract, journal)
                            article_copy['filter_reason'] = f"[keyword fallback] {kw_reason}"

                            if approved:
                                results['approved'].append(article_copy)
                            else:
                                results['rejected'].append(article_copy)

        self.approved = results['approved']
        self.rejected = results['rejected']

        return results

    def print_summary(self):
        """Print filtering summary"""
        total = len(self.approved) + len(self.rejected)
        mode = "LLM API intelligent mode" if self.use_llm_api else "keyword fast mode"

        print(f"\n{'='*60}")
        print(f"Literature filtering summary ({mode})")
        print(f"{'='*60}")
        print(f"Total articles: {total}")
        if total > 0:
            print(f"✅ Passed: {len(self.approved)} ({len(self.approved)/total*100:.1f}%)")
            print(f"❌ Excluded: {len(self.rejected)} ({len(self.rejected)/total*100:.1f}%)")
        else:
            print(f"✅ Passed: 0")
            print(f"❌ Excluded: 0")
        print(f"{'='*60}")

        # Show rejection reasons
        if self.rejected:
            print("\nRejection reason statistics:")
            reasons = Counter()
            for article in self.rejected:
                reason = article.get('filter_reason', '')
                if '] ' in reason:
                    main_reason = '] '.join(reason.split('] ')[1:])
                else:
                    main_reason = reason
                reasons[main_reason] += 1

            for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
                print(f"  - {reason}: {count}")


def write_filtered_tsv(articles: List[Dict], output_path: str):
    """Write filtered articles to TSV file"""
    headers = ['PMID/ID', 'Title', 'Authors', 'Journal', 'Publication Date', 'DOI', 'Abstract', 'AI Interpretation', 'Filter Reason']

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')

        for article in articles:
            def escape(s):
                if s is None:
                    return ""
                return str(s).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')

            primary_id = article.get('doi', article.get('id', ''))

            row = [
                escape(primary_id),
                escape(article.get('title', '')),
                escape(article.get('authors', '')),
                escape(article.get('journal', '')),
                escape(article.get('pub_date', '')),
                escape(article.get('doi', '')),
                escape(article.get('abstract', '')),
                escape(article.get('ai_interpretation', '')),
                escape(article.get('filter_reason', ''))
            ]

            f.write('\t'.join(row) + '\n')

    print(f"Written {len(articles)} articles to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Intelligent filter for plant transporter literature using LLM API (v2 with OpenAI SDK)')
    parser.add_argument('--input', required=True, help='Input JSON file')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--output-prefix', default='plant_transporter_filtered', help='Output file prefix')
    parser.add_argument('--show-rejected', action='store_true', help='Show rejected articles in separate file')
    parser.add_argument('--use-llm-api', action='store_true', help='Use external LLM API for filtering')
    parser.add_argument('--api-url', help='LLM API URL (overrides LLM_API_URL env var)')
    parser.add_argument('--api-key', help='LLM API Key (overrides LLM_API_KEY env var)')
    parser.add_argument('--model', help='LLM model name (overrides LLM_MODEL env var)')

    args = parser.parse_args()

    use_llm_api = args.use_llm_api or USE_LLM_API

    if use_llm_api:
        print("="*60)
        print("LLM API intelligent mode enabled (using OpenAI SDK)")
        print("="*60)
        print(f"API URL: {args.api_url or LLM_API_URL}")
        print(f"API Key: {(args.api_key or LLM_API_KEY)[:10]}...{(args.api_key or LLM_API_KEY)[-4:]}")
        print(f"Model: {args.model or LLM_MODEL}")
        print("="*60)

    # Load articles
    try:
        with open(args.input, 'r', encoding='utf-8') as f:
            articles = json.load(f)
    except FileNotFoundError:
        print(f"Error: input file not found: {args.input}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: invalid input file format: {args.input}")
        sys.exit(1)

    if not articles:
        print("No articles to filter")
        return

    # Filter articles
    api_url = args.api_url or LLM_API_URL
    api_key = args.api_key or LLM_API_KEY
    model = args.model or LLM_MODEL

    filter = IntelligentTransporterFilter(
        use_llm_api=use_llm_api,
        api_url=api_url,
        api_key=api_key,
        model=model
    )

    results = filter.filter_articles(articles)

    # Print summary
    filter.print_summary()

    # Write approved articles
    os.makedirs(args.output_dir, exist_ok=True)
    approved_tsv = os.path.join(args.output_dir, f"{args.output_prefix}.tsv")
    write_filtered_tsv(results['approved'], approved_tsv)

    approved_json = os.path.join(args.output_dir, f"{args.output_prefix}.json")
    with open(approved_json, 'w', encoding='utf-8') as f:
        json.dump(results['approved'], f, ensure_ascii=False, indent=2)
    print(f"Saved approved articles to {approved_json}")

    # Write rejected articles if requested
    if args.show_rejected:
        rejected_tsv = os.path.join(args.output_dir, f"{args.output_prefix}_rejected.tsv")
        write_filtered_tsv(results['rejected'], rejected_tsv)

        rejected_json = os.path.join(args.output_dir, f"{args.output_prefix}_rejected.json")
        with open(rejected_json, 'w', encoding='utf-8') as f:
            json.dump(results['rejected'], f, ensure_ascii=False, indent=2)
        print(f"Saved rejected articles to {rejected_json}")


if __name__ == '__main__':
    main()
