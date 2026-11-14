#!/usr/bin/env python3
"""
Literature Filter for Plant Transporters
Filters collected articles to ensure they are truly related to plant transporters
"""

import argparse
import json
import os
import re
from typing import List, Dict, Tuple
from collections import Counter


class TransporterFilter:
    """Filter for plant transporter-related literature"""

    # Core keywords that must be present (at least one match)
    REQUIRED_KEYWORDS = {
        'transporter', 'channel', 'carrier', 'pump',
        'abc', 'abc transporter',
        'aquaporin', 'mip',
        'slt', 'sugar transporter',
        'hkt', 'high-affinity potassium',
        'nitrate transporter', 'nrt',
        'amino acid transporter', 'aat',
        'glutamate transporter',
        'pin', 'auxin efflux carrier',
        'atp-binding cassette',
        'membrane transport',
        'yts1', 'yts',
        'slt1', 'slt2',
        'kup', 'kat1',
        'nhx', 'clca',
        'ion homeostasis',
        'nutrient uptake',
        'ion transport',
        'cation channel',
        'anion channel',
        'solute carrier',
        'ion flux',
        'transport protein',
        'permease',
        'translocase'
    }

    # Plant-related keywords (at least one match)
    PLANT_KEYWORDS = {
        'plant', 'plants', 'arabidopsis', 'rice', 'maize', 'wheat',
        'zea mays', 'oryza', 'nicotiana', 'tobacco',
        'physcomitrium', 'moss', 'fern', 'pteridophyte',
        'brachypodium', 'solanum', 'tobacco',
        'photosynthetic', 'chloroplast',
        'phyllid', 'gametophyte'
    }

    # Exclusion patterns (immediate rejection on match). Consistent with the
    # LLM-based filter: only explicit medical terms are excluded to avoid
    # discarding legitimate plant science papers. Generic animal words
    # (human/mouse/rat) are intentionally absent because heterologous
    # expression studies legitimately use those systems.
    EXCLUDE_PATTERNS = [
        # Explicit medical/clinical terms (instant rejection)
        r'\bclinical trial\b',
        r'\boncology\b',
        r'\bpatient\b',
        r'\bhospital\b',
        r'\bpharmacokinetics\b',
        r'\bdrug delivery\b',
        r'\bcancer therapy\b',
        r'\btreatment efficacy\b'
    ]

    # Context patterns - detect a strong plant-transporter association
    # (consistent with the LLM-based filter)
    CONTEXT_PATTERNS = [
        r'\bplants?.{0,20}transporter\b',
        r'\btransporter.{0,20}(in|of).{0,10}(plants?|arabidopsis|rice|maize)\b',
        r'\bchannel.{0,20}in.{0,10}plants?\b',
        r'\b(at|os|zm)[a-z0-9]+\b',  # plant gene locus prefixes (Arabidopsis/rice/maize)
        r'\btransporter in plant\b',
        r'\bplant transporter\b',
        r'\bchannel in plant\b',
        r'\bplant channel\b',
        r'\bin arabidopsis\b',
        r'\bin rice\b',
        r'\bin maize\b',
        r'\bin plants\b'
    ]

    def __init__(self):
        self.approved = []
        self.rejected = []
        self.exclude_regex = [re.compile(pattern, re.IGNORECASE) for pattern in self.EXCLUDE_PATTERNS]
        self.context_regex = [re.compile(pattern, re.IGNORECASE) for pattern in self.CONTEXT_PATTERNS]

    def normalize_text(self, text: str) -> str:
        """Normalize text for matching"""
        if not text:
            return ""
        return text.lower().strip()

    def check_exclusions(self, title: str, abstract: str, journal: str) -> Tuple[bool, str]:
        """Check if article should be excluded based on exclusion patterns"""
        # Handle None values
        title = title or ""
        abstract = abstract or ""
        journal = journal or ""
        text = self.normalize_text(title + " " + abstract + " " + journal)

        # Check exclusion patterns using regex (only explicit medical terms, as in the LLM-based filter)
        for regex in self.exclude_regex:
            if regex.search(text):
                matched = regex.pattern
                return True, f"matched exclusion pattern: {matched}"

        # No animal-transporter keyword check here: heterologous expression
        # (e.g. Arabidopsis genes expressed in yeast/oocytes) is legitimate
        # plant transporter research.

        # Special check: plant extract studied in an animal system (pure pharmacology, not transport research)
        if 'extract' in text:
            plant_extract_patterns = [
                r'\bplant extract\b',
                r'\bphytochemical\b',
                r'\bherbal\b',
                r'\bnatural product\b'
            ]
            animal_patterns = [
                r'\bmouse\b',
                r'\brat\b',
                r'\bhuman\b',
                r'\bpig\b',
                r'\banimal\b'
            ]

            has_plant_extract = any(re.search(p, text) for p in plant_extract_patterns)
            has_animal = any(re.search(a, text) for a in animal_patterns)

            # Exclude only when both plant-extract and animal terms appear and no
            # transporter term is present (a transporter term may indicate
            # transport mechanism research, which must be kept)
            transporter_keywords = ['transporter', 'channel', 'carrier', 'uptake', 'absorption']
            has_transporter = any(kw in text for kw in transporter_keywords)

            if has_plant_extract and has_animal and not has_transporter:
                return True, f"plant extract studied in animal research (not a transport mechanism)"

        return False, ""

    def check_inclusions(self, title: str, abstract: str) -> Tuple[bool, str]:
        """Check if article is about plant transporters"""
        # Handle None values
        title = title or ""
        abstract = abstract or ""
        text = self.normalize_text(title + " " + abstract)

        # Check plant keywords - word boundaries prevent substring false matches (e.g. "implant" matching "plant")
        plant_pattern = r'\b(' + '|'.join(re.escape(kw) for kw in self.PLANT_KEYWORDS) + r')\b'
        has_plant = re.search(plant_pattern, text, re.IGNORECASE) is not None
        if not has_plant:
            return False, "missing plant-related keywords"

        # Check transporter keywords - same word-boundary approach
        transporter_pattern = r'\b(' + '|'.join(re.escape(kw) for kw in self.REQUIRED_KEYWORDS) + r')\b'
        has_transporter = re.search(transporter_pattern, text, re.IGNORECASE) is not None
        if not has_transporter:
            return False, "missing transporter-related keywords"

        # Context check using the precompiled CONTEXT_PATTERNS (consistent with the LLM-based filter)
        has_context = any(regex.search(text) for regex in self.context_regex)

        if has_context:
            return True, "matches plant transporter topic (strong association)"

        # Both keyword groups present but no strong association: pass as a weak
        # match and let the LLM stage make the final judgment
        return True, "contains basic keywords (weak association, needs further LLM confirmation)"

    def filter_article(self, article: Dict) -> Tuple[bool, str]:
        """Filter a single article"""
        title = article.get('title', '')
        abstract = article.get('abstract', '')
        journal = article.get('journal', '')
        doi = article.get('doi', '')

        # First check exclusions
        should_exclude, exclude_reason = self.check_exclusions(title, abstract, journal)
        if should_exclude:
            return False, f"[excluded] {exclude_reason}"

        # Then check inclusions
        should_include, include_reason = self.check_inclusions(title, abstract)
        if should_include:
            return True, f"[pass] {include_reason}"
        else:
            return False, f"[excluded] {include_reason}"

    def filter_articles(self, articles: List[Dict]) -> Dict:
        """Filter all articles"""
        results = {
            'approved': [],
            'rejected': []
        }

        for article in articles:
            approved, reason = self.filter_article(article)

            article_copy = article.copy()
            article_copy['filter_reason'] = reason

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
        print(f"\n{'='*60}")
        print(f"Filtering Summary")
        print(f"{'='*60}")
        print(f"Total articles: {total}")
        if total > 0:
            print(f"Passed: {len(self.approved)} ({len(self.approved)/total*100:.1f}%)")
            print(f"Rejected: {len(self.rejected)} ({len(self.rejected)/total*100:.1f}%)")
        else:
            print(f"Passed: 0")
            print(f"Rejected: 0")
        print(f"{'='*60}")

        # Show rejection reasons
        if self.rejected:
            print("\nRejection reason breakdown:")
            reasons = Counter()
            for article in self.rejected:
                reason = article.get('filter_reason', '')
                # Extract the main reason
                if '] ' in reason:
                    main_reason = reason.split('] ')[1]
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
                escape(article.get('ai_interpretation', '')),  # Preserve if exists
                escape(article.get('filter_reason', ''))
            ]

            f.write('\t'.join(row) + '\n')

    print(f"Written {len(articles)} articles to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Filter plant transporter literature')
    parser.add_argument('--input', required=True, help='Input JSON file')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--output-prefix', default='plant_transporter_filtered', help='Output file prefix')
    parser.add_argument('--show-rejected', action='store_true', help='Show rejected articles in separate file')

    args = parser.parse_args()

    # Load articles
    with open(args.input, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    if not articles:
        print("No articles to filter")
        return

    # Filter articles
    print(f"Filtering {len(articles)} articles...")
    filter = TransporterFilter()
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
