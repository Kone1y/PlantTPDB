#!/usr/bin/env python3
"""
PubMed Literature Search for Plant Transporters
Searches PubMed for recent papers on plant transporters and channels
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
import urllib.request
import urllib.error
import time

try:
    import requests
except ImportError:
    print("Error: requests module not found. Install with: pip install requests")
    sys.exit(1)


class PubMedSearcher:
    """PubMed literature search and parser"""

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    # Search keywords for plant transporters
    SEARCH_TERMS = [
        "plant AND (transporter OR channel)",
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

    def esearch(self, term, batch_size=500):
        """Search PubMed and return PMIDs with pagination"""
        # Build search query with date filter
        date_filter = f"({self.start_date.strftime('%Y/%m/%d')}:{self.end_date.strftime('%Y/%m/%d')}[Date - Publication])"
        query = f"({term}) AND {date_filter}"

        all_pmids = []
        retstart = 0

        print(f"  Searching: {term}")

        while True:
            params = {
                'db': 'pubmed',
                'term': query,
                'retmode': 'json',
                'retmax': str(batch_size),
                'retstart': str(retstart),
                'sort': 'date'
            }

            url = f"{self.BASE_URL}esearch.fcgi?{urlencode(params)}"

            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()

                idlist = data.get('esearchresult', {}).get('idlist', [])
                count = int(data.get('esearchresult', {}).get('count', 0))

                if not idlist:
                    break

                all_pmids.extend(idlist)
                retstart += len(idlist)

                print(f"    Fetched {len(idlist)} PMIDs (total: {len(all_pmids)}/{count})")

                # Stop if we've retrieved all results
                if len(all_pmids) >= count:
                    break

                # Be nice to PubMed API
                time.sleep(0.34)  # PubMed allows 3 requests per second without API key

            except Exception as e:
                print(f"    Error searching PubMed: {e}")
                break

        print(f"    Found {len(all_pmids)} total PMIDs for '{term}'")
        return all_pmids

    def efetch(self, pmids, batch_size=200):
        """Fetch article details for given PMIDs in batches"""
        if not pmids:
            return {}

        all_articles = {}
        total_pmids = len(pmids)

        for i in range(0, total_pmids, batch_size):
            batch = pmids[i:i + batch_size]
            print(f"    Fetching details: {i+1}-{min(i+batch_size, total_pmids)}/{total_pmids}")

            params = {
                'db': 'pubmed',
                'id': ','.join(batch),
                'retmode': 'xml',
                'rettype': 'abstract'
            }

            url = f"{self.BASE_URL}efetch.fcgi?{urlencode(params)}"

            try:
                response = requests.get(url)
                response.raise_for_status()
                articles = self._parse_medline_xml(response.text)
                all_articles.update(articles)

                # Be nice to PubMed API
                if i + batch_size < total_pmids:
                    time.sleep(0.34)

            except Exception as e:
                print(f"    Error fetching batch {i//batch_size + 1}: {e}")
                # Continue with next batch even if this one fails
                continue

        print(f"    Fetched {len(all_articles)} article details")
        return all_articles

    def _parse_medline_xml(self, xml_text):
        """Parse MEDLINE XML response"""
        # Simple XML parsing without external dependencies
        articles = {}

        current_pmid = None
        current_article = {}

        # Extract articles using regex (simplified approach)
        import re

        pmid_pattern = r'<PMID[^>]*>(\d+)</PMID>'
        article_pattern = r'<PubmedArticle>(.*?)</PubmedArticle>'

        for match in re.finditer(article_pattern, xml_text, re.DOTALL):
            article_xml = match.group(1)

            # Extract PMID
            pmid_match = re.search(pmid_pattern, article_xml)
            if pmid_match:
                pmid = pmid_match.group(1)

                # Extract title
                title = self._extract_xml_field(article_xml, 'ArticleTitle')

                # Extract abstract
                abstract = self._extract_xml_field(article_xml, 'AbstractText')

                # Extract journal
                journal = self._extract_xml_field(article_xml, 'Title', 'Journal')
                if not journal:
                    journal = self._extract_xml_field(article_xml, 'ISOAbbreviation', 'Journal')

                # Extract publication date
                pub_date = self._extract_pub_date(article_xml)

                # Extract authors
                authors = self._extract_authors(article_xml)

                # Extract DOI
                doi = self._extract_doi(article_xml)

                articles[pmid] = {
                    'pmid': pmid,
                    'title': title,
                    'journal': journal,
                    'pub_date': pub_date,
                    'authors': authors,
                    'abstract': abstract,
                    'doi': doi
                }

        return articles

    def _extract_xml_field(self, xml, field_name, parent=None):
        """Extract field value from XML"""
        import re

        if parent:
            pattern = rf'<{parent}>.*?<{field_name}>(.*?)</{field_name}>.*?</{parent}>'
        else:
            pattern = rf'<{field_name}>(.*?)</{field_name}>'

        match = re.search(pattern, xml, re.DOTALL)
        if match:
            # Remove XML tags from content
            text = re.sub(r'<[^>]+>', ' ', match.group(1))
            return ' '.join(text.split())
        return ""

    def _normalize_date(self, year, month, day):
        """Normalize date to YYYY-MM-DD format"""
        # Month name to number mapping
        month_map = {
            'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
            'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
            'january': '01', 'february': '02', 'march': '03', 'april': '04',
            'june': '06', 'july': '07', 'august': '08', 'september': '09',
            'october': '10', 'november': '11', 'december': '12'
        }

        if not year:
            return ""

        # Normalize month
        if month:
            month_lower = month.lower()
            if month_lower in month_map:
                month = month_map[month_lower]
            else:
                # Already numeric, ensure 2 digits
                month = str(month).zfill(2)
        else:
            month = "01"

        # Normalize day
        if day:
            day = str(day).zfill(2)
        else:
            day = "01"

        return f"{year}-{month}-{day}"

    def _extract_pub_date(self, xml):
        """Extract online publication date (ArticleDate Electronic) first, fallback to PubDate.
        Returns date in YYYY-MM-DD format."""
        import re

        # Priority 1: ArticleDate with DateType="Electronic" (online publication date)
        edate_pattern = r'<ArticleDate DateType="Electronic">(.*?)</ArticleDate>'
        edate_match = re.search(edate_pattern, xml, re.DOTALL)

        if edate_match:
            edate_block = edate_match.group(1)
            year_match = re.search(r'<Year>(\d{4})</Year>', edate_block)
            month_match = re.search(r'<Month>(\d+)</Month>', edate_block)
            day_match = re.search(r'<Day>(\d+)</Day>', edate_block)

            if year_match and month_match and day_match:
                return self._normalize_date(year_match.group(1), month_match.group(1), day_match.group(1))

        # Priority 2: PubDate (journal publication date)
        pubdate_pattern = r'<PubDate>(.*?)</PubDate>'
        pubdate_match = re.search(pubdate_pattern, xml, re.DOTALL)

        if not pubdate_match:
            return ""

        pubdate_block = pubdate_match.group(1)
        year_match = re.search(r'<Year>(\d{4})</Year>', pubdate_block)
        month_match = re.search(r'<Month>(\w+)</Month>', pubdate_block)
        day_match = re.search(r'<Day>(\d+)</Day>', pubdate_block)

        if year_match:
            return self._normalize_date(
                year_match.group(1),
                month_match.group(1) if month_match else None,
                day_match.group(1) if day_match else None
            )

        return ""

    def _extract_authors(self, xml):
        """Extract author list"""
        import re

        authors = []

        author_pattern = r'<Author>(.*?)</Author>'
        for match in re.finditer(author_pattern, xml, re.DOTALL):
            author_xml = match.group(1)

            last_name = self._extract_xml_field(author_xml, 'LastName')
            initials = self._extract_xml_field(author_xml, 'Initials')

            if last_name:
                if initials:
                    authors.append(f"{last_name} {initials}")
                else:
                    authors.append(last_name)

        return "; ".join(authors[:10])  # Limit to first 10 authors

    def _extract_doi(self, xml):
        """Extract DOI from ArticleIdList"""
        import re

        doi_pattern = r'<ArticleId IdType="doi">(.*?)</ArticleId>'
        match = re.search(doi_pattern, xml)
        if match:
            return match.group(1)
        return ""

    def _parse_date_for_filter(self, date_str):
        """Parse date string to datetime object for filtering"""
        import re
        from datetime import datetime

        if not date_str:
            return None

        # Month name to number mapping
        month_map = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'june': 6, 'july': 7, 'august': 8, 'september': 9,
            'october': 10, 'november': 11, 'december': 12
        }

        try:
            parts = date_str.split('-')
            if len(parts) >= 1:
                year = int(parts[0])
                month = 1
                day = 1

                if len(parts) >= 2:
                    month_str = parts[1].lower()
                    if month_str.isdigit():
                        month = int(month_str)
                    else:
                        month = month_map.get(month_str, 1)

                if len(parts) >= 3:
                    day = int(parts[2])

                return datetime(year, month, day)
        except (ValueError, IndexError):
            pass

        return None

    def search_all(self):
        """Search all transporter-related terms"""
        all_pmids = set()

        print(f"Searching PubMed for articles from {self.start_date.date()} to {self.end_date.date()}...")

        for term in self.SEARCH_TERMS:
            print(f"  Searching: {term}")
            pmids = self.esearch(term)
            all_pmids.update(pmids)
            time.sleep(0.5)  # Be nice to PubMed API

        print(f"Found {len(all_pmids)} unique articles")
        return list(all_pmids)

    def filter_by_date(self, articles):
        """Filter articles by actual publication date (secondary filter)"""
        filtered = {}
        removed_count = 0

        # Compare dates only (ignore time part)
        start_date_only = self.start_date.date()
        end_date_only = self.end_date.date()

        for pmid, article in articles.items():
            pub_date = article.get('pub_date', '')
            date_obj = self._parse_date_for_filter(pub_date)

            if date_obj:
                date_only = date_obj.date()
                if date_only < start_date_only or date_only > end_date_only:
                    removed_count += 1
                    continue

            filtered[pmid] = article

        if removed_count > 0:
            print(f"    Secondary date filter removed {removed_count} articles outside date range")

        return filtered


def generate_ai_interpretation(article):
    """Generate AI interpretation for article (placeholder)"""
    title = article.get('title', '')
    abstract = article.get('abstract', '')
    journal = article.get('journal', '')

    interpretation = f"This article was published in {journal}."

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
        'abc': 'ABC transporters',
        'abc transporter': 'ABC transporters'
    }

    for keyword, type_name in transporter_types.items():
        if keyword in keywords_lower:
            interpretation += f" May involve research on {type_name}."
            break

    # Method hints
    if 'crystal structure' in keywords_lower or 'structure' in keywords_lower:
        interpretation += " Includes structural biology analysis."
    if 'mutant' in keywords_lower or 'mutation' in keywords_lower:
        interpretation += " Uses mutants for functional validation."
    if 'expression' in keywords_lower:
        interpretation += " Involves gene expression analysis."
    if 'knockout' in keywords_lower or 'knockdown' in keywords_lower:
        interpretation += " Uses gene knockout/knockdown techniques."

    return interpretation


def write_tsv(articles, output_path):
    """Write articles to TSV file"""
    headers = ['PMID', 'Title', 'Authors', 'Journal', 'Publication Date', 'DOI', 'Abstract', 'AI Interpretation']

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(headers) + '\n')

        for pmid, article in sorted(articles.items()):
            # Generate AI interpretation
            ai_interpretation = generate_ai_interpretation(article)

            # Escape tabs and newlines
            def escape(s):
                if s is None:
                    return ""
                return str(s).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')

            row = [
                escape(article.get('pmid', '')),
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
    parser = argparse.ArgumentParser(description='Search PubMed for plant transporter literature')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--output-prefix', default='plant_transporter', help='Output file prefix')
    parser.add_argument('--days-ago', type=int, default=7, help='Number of days to search back')

    args = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Create searcher and search
    searcher = PubMedSearcher(days_ago=args.days_ago)
    pmids = searcher.search_all()

    if not pmids:
        print("No articles found in the specified time period")
        # Create empty file with headers
        output_path = os.path.join(args.output_dir, f"{args.output_prefix}.tsv")
        headers = ['PMID', 'Title', 'Authors', 'Journal', 'Publication Date', 'DOI', 'Abstract', 'AI Interpretation']
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(headers) + '\n')
        return

    # Fetch article details
    print("Fetching article details...")
    articles = searcher.efetch(pmids)

    # Apply secondary date filter to remove articles outside the date range
    print("Applying secondary date filter...")
    articles = searcher.filter_by_date(articles)

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
        print("No article details could be retrieved")


if __name__ == '__main__':
    main()
