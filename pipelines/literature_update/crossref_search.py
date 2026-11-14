#!/usr/bin/env python3
"""
Crossref Literature Search for Plant Transporters
Uses Crossref REST API to search for recent papers
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode
import urllib.request
import urllib.error


class CrossrefSearcher:
    """Crossref literature search using their REST API"""

    API_BASE = "https://api.crossref.org/works"

    # Search queries for plant transporters
    QUERIES = [
        "plant transporter",
        "plant channel",
        "plant membrane transport",
        "plant ion channel",
        "plant sugar transporter",
        "plant amino acid transporter",
        "plant ABC transporter",
        "plant aquaporin"
    ]

    def __init__(self, days_ago=7):
        """Initialize searcher with time window"""
        self.days_ago = days_ago
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=days_ago)
        self.articles = []

    def search(self, query, batch_size=1000):
        """Search Crossref for articles with pagination"""
        # Use proper Crossref filter syntax
        start_date = self.start_date.strftime("%Y-%m-%d")
        end_date = self.end_date.strftime("%Y-%m-%d")

        all_items = []
        offset = 0

        print(f"  Searching: {query}")

        while True:
            params = {
                'query': query,
                'filter': f'from-pub-date:{start_date},until-pub-date:{end_date}',
                'rows': str(batch_size),
                'offset': str(offset),
                'sort': 'published',
                'order': 'desc',
                'select': 'title,author,published-print,published-online,DOI,container-title,abstract,type,container-title'
            }

            url = f"{self.API_BASE}?{urlencode(params)}"

            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    items = data.get('message', {}).get('items', [])

                    if not items:
                        break

                    all_items.extend(items)
                    offset += len(items)

                    print(f"    Fetched {len(items)} results (total: {len(all_items)})")

                    # Crossref has a hard limit of 10000 items per query
                    if len(items) < batch_size or len(all_items) >= 10000:
                        break

            except urllib.error.HTTPError as e:
                print(f"    HTTP Error: {e.code} - {e.reason}")
                if e.code == 400:
                    print(f"    Query URL (for debugging): {url}")
                break
            except Exception as e:
                print(f"    Error searching: {e}")
                break

        print(f"    Found {len(all_items)} total results for '{query}'")
        return all_items

    def parse_article(self, item):
        """Parse Crossref item to standard format"""
        # Extract authors
        authors = []
        if 'author' in item:
            for author in item['author'][:10]:  # Limit to first 10
                given = author.get('given', '')
                family = author.get('family', '')
                if family:
                    if given:
                        authors.append(f"{family}, {given}")
                    else:
                        authors.append(family)

        # Extract publication date (prioritize online publication date)
        # Format: YYYY-MM-DD with zero-padding
        pub_date = ""
        date_parts = None

        # Priority: published-online (actual online date) > published-print
        if 'published-online' in item:
            date_parts = item['published-online'].get('date-parts', [[]])[0]
        elif 'published-print' in item:
            date_parts = item['published-print'].get('date-parts', [[]])[0]

        if date_parts and len(date_parts) >= 1:
            year = str(date_parts[0]) if date_parts[0] else ""
            month = str(date_parts[1]).zfill(2) if len(date_parts) > 1 and date_parts[1] else "01"
            day = str(date_parts[2]).zfill(2) if len(date_parts) > 2 and date_parts[2] else "01"
            if year:
                pub_date = f"{year}-{month}-{day}"

        # Extract journal name
        journal = ""
        if 'container-title' in item and item['container-title']:
            journal = item['container-title'][0]
        elif 'journal-title' in item:
            journal = item['journal-title']

        # Extract title
        title = ""
        if 'title' in item and item['title']:
            title = item['title'][0]

        # Extract abstract (if available)
        abstract = item.get('abstract', '')

        return {
            'doi': item.get('DOI', ''),
            'title': title,
            'authors': '; '.join(authors),
            'journal': journal,
            'pub_date': pub_date,
            'abstract': abstract,
            'type': item.get('type', '')
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
        print(f"Searching Crossref for articles from {self.start_date.date()} to {self.end_date.date()}...")

        seen_dois = set()

        for query in self.QUERIES:
            items = self.search(query)

            for item in items:
                doi = item.get('DOI', '')

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
    title = article.get('title', '')
    abstract = article.get('abstract', '')
    journal = article.get('journal', '')
    article_type = article.get('type', '')

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

    return interpretation


def write_tsv(articles, output_path):
    """Write articles to TSV file"""
    headers = ['DOI', 'Title', 'Authors', 'Journal', 'Publication Date', 'Abstract', 'AI Interpretation']

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

            row = [
                escape(article.get('doi', '')),
                escape(article.get('title', '')),
                escape(article.get('authors', '')),
                escape(article.get('journal', '')),
                escape(article.get('pub_date', '')),
                escape(article.get('abstract', '')),
                escape(ai_interpretation)
            ]

            f.write('\t'.join(row) + '\n')

    print(f"Written {len(articles)} articles to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Search Crossref for plant transporter literature')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--output-prefix', default='plant_transporter', help='Output file prefix')
    parser.add_argument('--days-ago', type=int, default=7, help='Number of days to search back')

    args = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Create searcher and search
    searcher = CrossrefSearcher(days_ago=args.days_ago)
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
        headers = ['DOI', 'Title', 'Authors', 'Journal', 'Publication Date', 'Abstract', 'AI Interpretation']
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(headers) + '\n')


if __name__ == '__main__':
    main()
