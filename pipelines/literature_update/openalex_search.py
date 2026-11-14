#!/usr/bin/env python3
"""
OpenAlex Literature Search for Plant Transporters
Uses OpenAlex API (free, no API key required) to search for recent papers
https://docs.openalex.org
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode
import urllib.request
import urllib.error


class OpenAlexSearcher:
    """OpenAlex literature search using their free API"""

    API_BASE = "https://api.openalex.org/works"

    # Search queries for plant transporters
    QUERIES = [
        "plant transporter",
        "plant channel",
        "plant membrane transport",
        "plant ion channel",
        "plant sugar transporter",
        "plant amino acid transporter",
        "plant ABC transporter",
        "plant aquaporin",
        "plant nitrate transporter",
        "plant potassium transporter"
    ]

    def __init__(self, days_ago=7):
        """Initialize searcher with time window"""
        self.days_ago = days_ago
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=days_ago)
        self.articles = []

    def search(self, query, per_page=200, max_results=1000):
        """Search OpenAlex for articles with pagination"""
        # Format dates as YYYY-MM-DD
        start_date = self.start_date.strftime("%Y-%m-%d")
        end_date = self.end_date.strftime("%Y-%m-%d")

        # Build filter parameters
        filters = {
            "from_publication_date": start_date,
            "to_publication_date": end_date,
            "has_abstract": "true"
        }

        all_items = []
        cursor = "*"

        print(f"  Searching: {query}")

        while cursor and len(all_items) < max_results:
            params = {
                'search': query,
                'filter': ','.join([f"{k}:{v}" for k, v in filters.items()]),
                'per-page': str(per_page),
                'sort': 'publication_date:desc',
                'cursor': cursor
            }

            url = f"{self.API_BASE}?{urlencode(params)}"

            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    items = data.get('results', [])
                    meta = data.get('meta', {})

                    if not items:
                        break

                    # Only add items up to max_results
                    remaining = max_results - len(all_items)
                    items_to_add = items[:remaining]
                    all_items.extend(items_to_add)

                    # Get next cursor
                    cursor = meta.get('next_cursor')

                    print(f"    Fetched {len(items_to_add)} results (total: {len(all_items)})")

                    # Stop if we've reached max_results
                    if len(all_items) >= max_results or not cursor:
                        break

            except Exception as e:
                print(f"    Error searching: {e}")
                break

        print(f"    Found {len(all_items)} total results for '{query}'")
        return all_items

    def parse_article(self, item):
        """Parse OpenAlex item to standard format"""
        # Extract authors
        authors = []
        authorships = item.get('authorships', [])
        for authorship in authorships[:10]:  # Limit to first 10
            author = authorship.get('author', {})
            display_name = author.get('display_name', '')
            if display_name:
                authors.append(display_name)

        # Extract publication date
        pub_date = item.get('publication_date', '')

        # Extract journal name
        journal = ""
        primary_location = item.get('primary_location', {})
        source = primary_location.get('source', {})
        if source:
            journal = source.get('display_name', '')

        # Extract title
        title = item.get('title', '')

        # Extract abstract
        abstract = ""
        if 'abstract_inverted_index' in item:
            # Reconstruct abstract from inverted index
            inverted_index = item['abstract_inverted_index']
            if inverted_index:
                word_positions = {}
                for word, positions in inverted_index.items():
                    for pos in positions:
                        word_positions[pos] = word

                sorted_positions = sorted(word_positions.keys())
                abstract = ' '.join([word_positions[pos] for pos in sorted_positions])

        # Extract DOI
        doi = item.get('doi', '')
        if doi.startswith('https://doi.org/'):
            doi = doi[16:]  # Remove prefix

        # Get publication type
        type_str = item.get('type', '')

        return {
            'doi': doi,
            'title': title,
            'authors': '; '.join(authors),
            'journal': journal,
            'pub_date': pub_date,
            'abstract': abstract,
            'type': type_str,
            'id': item.get('id', '')
        }

    def _parse_date_for_filter(self, date_str):
        """Parse date string to datetime object for filtering"""
        if not date_str:
            return None

        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

        return None

    def filter_by_date(self, articles):
        """Filter articles by actual publication date (secondary filter)"""
        filtered = []
        removed_count = 0

        # Compare dates only (ignore time part)
        start_date_only = self.start_date.date()
        end_date_only = self.end_date.date()

        for article in articles:
            pub_date = article.get('pub_date', '')
            date_obj = self._parse_date_for_filter(pub_date)

            if date_obj:
                date_only = date_obj.date()
                if date_only < start_date_only or date_only > end_date_only:
                    removed_count += 1
                    continue

            filtered.append(article)

        if removed_count > 0:
            print(f"    Secondary date filter removed {removed_count} articles outside date range")

        return filtered

    def search_all(self):
        """Search all queries and combine results"""
        print(f"Searching OpenAlex for articles from {self.start_date.date()} to {self.end_date.date()}...")

        seen_dois = set()

        for query in self.QUERIES:
            items = self.search(query)

            for item in items:
                doi = item.get('doi', '')

                if doi and doi not in seen_dois:
                    seen_dois.add(doi)
                    article = self.parse_article(item)
                    self.articles.append(article)

        print(f"Found {len(self.articles)} unique articles")

        # Apply secondary date filter
        print("Applying secondary date filter...")
        self.articles = self.filter_by_date(self.articles)
        print(f"After filtering: {len(self.articles)} articles")

        return self.articles


def generate_ai_interpretation(article):
    """Generate AI interpretation for article"""
    title = article.get('title') or ''
    abstract = article.get('abstract') or ''
    journal = article.get('journal') or ''
    article_type = article.get('type') or ''

    interpretation = f"This is a {article_type} published in {journal}."

    # Simple keyword-based interpretation
    keywords_lower = (title + ' ' + abstract).lower()

    if 'transporter' in keywords_lower and 'channel' in keywords_lower:
        interpretation += " The study involves transporters and ion channels, likely related to transport and signal transduction in plants."
    elif 'transporter' in keywords_lower:
        interpretation += " Focuses on plant transporters, addressing transmembrane transport mechanisms of specific substrates."
    elif 'channel' in keywords_lower:
        interpretation += " Studies ion channel proteins, relevant to ion uptake and signaling in plants."

    # Detect specific transporter types
    transporter_types = {
        'sugar': 'sugar transporters',
        'amino acid': 'amino acid transporters',
        'nitrate': 'nitrate transporters',
        'potassium': 'potassium transporters',
        'aquaporin': 'aquaporins',
        'abc': 'ABC transporters'
    }

    for keyword, type_name in transporter_types.items():
        if keyword in keywords_lower:
            interpretation += f" May involve research on {type_name}."
            break

    # Method hints
    if 'crystal' in keywords_lower or 'structure' in keywords_lower:
        interpretation += " Likely includes structural biology analysis."
    if 'mutant' in keywords_lower or 'mutation' in keywords_lower or 'knockout' in keywords_lower:
        interpretation += " May use genetic approaches for functional validation."
    if 'expression' in keywords_lower:
        interpretation += " Involves gene expression analysis."
    if 'transport' in keywords_lower and 'activity' in keywords_lower:
        interpretation += " May include transport activity assays."
    if 'xenopus' in keywords_lower or 'oocyte' in keywords_lower:
        interpretation += " May use the Xenopus oocyte expression system for functional analysis."
    if 'yeast' in keywords_lower:
        interpretation += " May use a yeast heterologous expression system."

    return interpretation


def write_tsv(articles, output_path):
    """Write articles to TSV file"""
    headers = ['PMID/ID', 'Title', 'Authors', 'Journal', 'Publication Date', 'DOI', 'Abstract', 'AI Interpretation']

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')

        for article in articles:
            # Generate AI interpretation
            ai_interpretation = generate_ai_interpretation(article)

            # Escape tabs and newlines
            def escape(s):
                if s is None:
                    return ""
                return str(s).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')

            # Use DOI as primary ID, fallback to OpenAlex ID
            primary_id = article.get('doi', article.get('id', ''))

            row = [
                escape(primary_id),
                escape(article.get('title', '')),
                escape(article.get('authors', '')),
                escape(article.get('journal', '')),
                escape(article.get('pub_date', '')),
                escape(article.get('doi', '')),
                escape(article.get('abstract', '')),
                escape(ai_interpretation)
            ]

            f.write('\t'.join(row) + '\n')

    print(f"Written {len(articles)} articles to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Search OpenAlex for plant transporter literature')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--output-prefix', default='plant_transporter', help='Output file prefix')
    parser.add_argument('--days-ago', type=int, default=7, help='Number of days to search back')

    args = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Create searcher and search
    searcher = OpenAlexSearcher(days_ago=args.days_ago)
    articles = searcher.search_all()

    if articles:
        # Write to TSV
        output_path = os.path.join(args.output_dir, f"{args.output_prefix}.tsv")
        write_tsv(articles, output_path)

        # Also save as JSON for backup
        json_path = os.path.join(args.output_dir, f"{args.output_prefix}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"Also saved JSON backup to {json_path}")
    else:
        print("No articles found in the specified time period")
        # Create empty file with headers
        output_path = os.path.join(args.output_dir, f"{args.output_prefix}.tsv")
        headers = ['PMID/ID', 'Title', 'Authors', 'Journal', 'Publication Date', 'DOI', 'Abstract', 'AI Interpretation']
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(headers) + '\n')


if __name__ == '__main__':
    main()
