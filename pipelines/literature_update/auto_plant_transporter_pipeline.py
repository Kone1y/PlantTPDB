#!/usr/bin/env python3
"""
Orchestrator for automated plant transporter literature collection and filtering.

Run periodically to execute the full search-to-filter workflow.

By default uses an LLM API (v2) for intelligent filtering; configure the
following environment variables:
- LLM_API_URL: API endpoint (e.g. https://api.deepseek.com)
- LLM_API_KEY: API key
- LLM_MODEL: model name (e.g. deepseek-chat)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Set


class CollectedArticlesManager:
    """Deduplication registry for previously collected articles."""

    def __init__(self, output_base_dir: Path):
        self.output_base_dir = output_base_dir
        self.collected_file = output_base_dir / "collected_articles.json"
        self.collected_dois: Set[str] = set()
        self.collected_pmids: Set[str] = set()
        self._load()

    def _load(self):
        """Load the record of previously collected articles."""
        if self.collected_file.exists():
            try:
                with open(self.collected_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.collected_dois = set(data.get('dois', []))
                self.collected_pmids = set(data.get('pmids', []))
                print(f"Loaded deduplication records: {len(self.collected_dois)} DOIs, {len(self.collected_pmids)} PMIDs")
            except Exception as e:
                print(f"Failed to load deduplication records: {e}")
                self.collected_dois = set()
                self.collected_pmids = set()
        else:
            print("No previous deduplication records found; a new record will be created")

    def _save(self):
        """Persist the record of collected articles."""
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        data = {
            'last_updated': datetime.now().isoformat(),
            'dois': list(self.collected_dois),
            'pmids': list(self.collected_pmids)
        }
        with open(self.collected_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def is_collected(self, article: Dict) -> bool:
        """Check whether an article has already been collected."""
        doi = article.get('doi', '').strip().lower()
        pmid = article.get('pmid', '').strip()

        if doi and doi in self.collected_dois:
            return True
        if pmid and pmid in self.collected_pmids:
            return True
        return False

    def mark_collected(self, article: Dict):
        """Mark an article as collected."""
        doi = article.get('doi', '').strip().lower()
        pmid = article.get('pmid', '').strip()

        if doi:
            self.collected_dois.add(doi)
        if pmid:
            self.collected_pmids.add(pmid)

    def mark_batch_collected(self, articles: List[Dict]):
        """Mark a batch of articles as collected."""
        for article in articles:
            self.mark_collected(article)
        self._save()
        print(f"Recorded deduplication info for {len(articles)} newly collected articles")

    def filter_new_articles(self, articles: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Split articles into (new articles, already collected articles)."""
        new_articles = []
        already_collected = []

        for article in articles:
            if self.is_collected(article):
                already_collected.append(article)
            else:
                new_articles.append(article)

        if already_collected:
            print(f"  Deduplication: {len(already_collected)} already collected articles found")

        return new_articles, already_collected


class LiteraturePipeline:
    """Automated literature processing pipeline.

    By default uses an LLM API (literature_filter_llm_api_v2.py) for intelligent
    filtering. To use the LLM, set the environment variables:
        LLM_API_URL, LLM_API_KEY, LLM_MODEL

    To run without the LLM, set use_llm=False or pass --no-llm.
    """

    def __init__(self, output_base_dir: str, days_ago: int = 7, use_llm: bool = True):
        self.output_base_dir = Path(output_base_dir)
        self.days_ago = days_ago
        self.use_llm = use_llm
        self.timestamp = datetime.now().strftime("%Y%m%d")
        self.run_dir = self.output_base_dir / f"run_{self.timestamp}"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.script_dir = Path(__file__).parent

        self.collected_manager = CollectedArticlesManager(self.output_base_dir)

    def step1_search_all_sources(self) -> List[Path]:
        """Step 1: search all data sources."""
        print("\n" + "=" * 60)
        print("Step 1: Search all data sources")
        print("=" * 60)

        sources = [
            ("openalex", "openalex_search.py"),
            ("pubmed", "pubmed_search.py"),
            ("crossref", "crossref_search.py")
        ]

        raw_files = []

        for source_name, script_name in sources:
            script_path = self.script_dir / script_name
            if not script_path.exists():
                print(f"  Skipping {source_name}: script not found")
                continue

            output_file = self.run_dir / f"{source_name}_raw.json"

            cmd = [
                sys.executable, str(script_path),
                "--output-dir", str(self.run_dir),
                "--output-prefix", f"{source_name}_raw",
                "--days-ago", str(self.days_ago)
            ]

            print(f"\n  Searching {source_name}...")
            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                print(result.stdout)
                if output_file.exists():
                    raw_files.append(output_file)
                    print(f"    Result: {output_file}")
            except subprocess.TimeoutExpired:
                print(f"    Timed out: search took too long")
            except subprocess.CalledProcessError as e:
                print(f"    Failed: {e}")
                print(f"    Error output: {e.stderr}")

        return raw_files

    def step2_merge_and_deduplicate(self, raw_files: List[Path]) -> Path:
        """Step 2: merge and deduplicate (within this run and against history)."""
        print("\n" + "=" * 60)
        print("Step 2: Merge and deduplicate")
        print("=" * 60)

        all_articles = []
        seen_dois = set()
        seen_pmids = set()
        source_counts = {}

        for raw_file in raw_files:
            source_name = raw_file.stem.replace('_raw', '')
            try:
                with open(raw_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # source scripts may emit either a list or a dict
                if isinstance(data, dict):
                    articles = list(data.values())
                else:
                    articles = data

                source_counts[source_name] = len(articles)

                for article in articles:
                    doi = article.get('doi', '').strip().lower()
                    pmid = article.get('pmid', '').strip()

                    if doi and doi in seen_dois:
                        continue
                    if doi:
                        seen_dois.add(doi)

                    if pmid and pmid in seen_pmids:
                        continue
                    if pmid:
                        seen_pmids.add(pmid)

                    article['_source'] = source_name
                    all_articles.append(article)

            except Exception as e:
                print(f"  Failed to read {raw_file}: {e}")

        print(f"  Articles merged this run: {len(all_articles)}")
        new_articles, already_collected = self.collected_manager.filter_new_articles(all_articles)
        all_articles = new_articles

        merged_file = self.run_dir / "merged_raw.json"
        with open(merged_file, 'w', encoding='utf-8') as f:
            json.dump(all_articles, f, ensure_ascii=False, indent=2)

        print(f"  Articles per source:")
        for source, count in source_counts.items():
            print(f"    - {source}: {count}")
        print(f"  After within-run deduplication: {len(all_articles) + len(already_collected)} articles")
        if already_collected:
            print(f"  After deduplication against history (already collected excluded): {len(all_articles)} articles")
        print(f"  Saved: {merged_file}")

        return merged_file

    def step3_filter_articles(self, input_file: Path) -> Path:
        """Step 3: two-stage intelligent filtering (coarse keyword filter, then fine LLM filter)."""
        print("\n" + "=" * 60)
        print("Step 3: Two-stage intelligent filtering")
        print("=" * 60)

        if not self.use_llm:
            filter_script = "literature_filter.py"
            mode = "fast keyword filtering"

            filter_path = self.script_dir / filter_script
            if not filter_path.exists():
                print(f"  Filter script not found: {filter_path}")
                empty_file = self.run_dir / "filtered.json"
                with open(empty_file, 'w', encoding='utf-8') as f:
                    json.dump([], f)
                return empty_file

            cmd = [
                sys.executable, str(filter_path),
                "--input", str(input_file),
                "--output-dir", str(self.run_dir),
                "--output-prefix", "filtered",
                "--show-rejected"
            ]

            print(f"  Mode: {mode}")
            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                print(result.stdout)
                print(f"  Filtering complete!")
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                print(f"  Filtering failed: {e}")

            return self.run_dir / "filtered.json"

        print(f"  Mode: two-stage filtering (coarse keyword filter + fine LLM filter)")

        # ========== Stage 1: coarse keyword filter ==========
        print("\n  [Stage 1] Coarse keyword filtering...")
        coarse_filter_script = self.script_dir / "literature_filter.py"
        coarse_output_file = self.run_dir / "coarse_filtered.json"

        if coarse_filter_script.exists():
            cmd = [
                sys.executable, str(coarse_filter_script),
                "--input", str(input_file),
                "--output-dir", str(self.run_dir),
                "--output-prefix", "coarse_filtered",
                "--show-rejected"
            ]

            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                print(result.stdout)

                if coarse_output_file.exists():
                    with open(coarse_output_file, 'r', encoding='utf-8') as f:
                        coarse_articles = json.load(f)
                    print(f"  Coarse filtering result: {len(coarse_articles)} articles passed")
                else:
                    print(f"  Coarse filtering output file not found; using the original file")
                    coarse_articles = json.loads(input_file.read_text(encoding='utf-8'))

            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                print(f"  Coarse filtering failed: {e}; proceeding directly to fine LLM filtering")
                with open(input_file, 'r', encoding='utf-8') as f:
                    coarse_articles = json.load(f)
        else:
            print(f"  Coarse filter script not found; skipping")
            with open(input_file, 'r', encoding='utf-8') as f:
                coarse_articles = json.load(f)

        # ========== Stage 2: fine LLM filter ==========
        print("\n  [Stage 2] Fine LLM filtering...")
        llm_filter_script = self.script_dir / "literature_filter_llm_api_v2.py"

        if not llm_filter_script.exists():
            print(f"  LLM filter script not found: {llm_filter_script}")
            # fall back to the coarse-filtered results as the final output
            final_file = self.run_dir / "filtered.json"
            with open(final_file, 'w', encoding='utf-8') as f:
                json.dump(coarse_articles, f, ensure_ascii=False, indent=2)
            return final_file

        # write the coarse-filtered results to a temp file for the LLM filter
        llm_input_file = self.run_dir / "llm_input.json"
        with open(llm_input_file, 'w', encoding='utf-8') as f:
            json.dump(coarse_articles, f, ensure_ascii=False, indent=2)

        cmd = [
            sys.executable, str(llm_filter_script),
            "--input", str(llm_input_file),
            "--output-dir", str(self.run_dir),
            "--output-prefix", "filtered",
            "--show-rejected",
            "--use-llm-api"
        ]

        try:
            # output is not captured so the progress bar renders in real time
            result = subprocess.run(
                cmd,
                check=True,
                timeout=7200  # up to 2 hours
            )
            print(f"  Fine LLM filtering complete!")
        except subprocess.TimeoutExpired:
            print(f"  Warning: LLM filtering timed out; using the coarse-filtered results")
            final_file = self.run_dir / "filtered.json"
            with open(final_file, 'w', encoding='utf-8') as f:
                json.dump(coarse_articles, f, ensure_ascii=False, indent=2)
            return final_file
        except subprocess.CalledProcessError as e:
            print(f"  LLM filtering failed: {e}")
            print(f"  Error output: {e.stderr}")
            print(f"  Using the coarse-filtered results")
            final_file = self.run_dir / "filtered.json"
            with open(final_file, 'w', encoding='utf-8') as f:
                json.dump(coarse_articles, f, ensure_ascii=False, indent=2)
            return final_file

        filtered_file = self.run_dir / "filtered.json"
        return filtered_file

    def step3_filter_articles_fallback(self, input_file: Path) -> Path:
        """Fall back to keyword-only filtering."""
        print(f"  Falling back to keyword filtering mode...")

        filter_script = "literature_filter.py"
        filter_path = self.script_dir / filter_script

        cmd = [
            sys.executable, str(filter_path),
            "--input", str(input_file),
            "--output-dir", str(self.run_dir),
            "--output-prefix", "filtered",
            "--show-rejected"
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"  Keyword filtering failed as well: {e}")

        return self.run_dir / "filtered.json"

    def step4_generate_report(self, filtered_file: Path) -> Tuple[Path, Path]:
        """Step 4: generate reports."""
        print("\n" + "=" * 60)
        print("Step 4: Generate reports")
        print("=" * 60)

        try:
            with open(filtered_file, 'r', encoding='utf-8') as f:
                articles = json.load(f)
        except Exception as e:
            print(f"  Failed to read filtering results: {e}")
            articles = []

        if not articles:
            print(f"  No articles passed the filters")
            report_file = self.run_dir / "report.json"
            md_file = self.run_dir / "report.md"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump({'total_approved': 0}, f)
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write("# Plant Transporter Literature Daily Report\n\nNo articles matched the filtering criteria.\n")
            return report_file, md_file

        # transporter category labels -> keyword lists used for classification
        transporter_types = {
            'ABC transporters': ['abc', 'atp-binding'],
            'Aquaporins': ['aquaporin', 'mip', 'water channel'],
            'Sugar transporters': ['sugar transporter', 'slt', 'sucrose'],
            'Nitrate transporters': ['nitrate transporter', 'nrt'],
            'Potassium transporters': ['potassium', 'kat', 'kup', 'hkt'],
            'Amino acid transporters': ['amino acid transporter', 'aat'],
            'PIN proteins': ['pin', 'auxin efflux'],
            'Ion channels': ['ion channel', 'channel']
        }

        type_counts = {k: 0 for k in transporter_types.keys()}

        for article in articles:
            text = (article.get('title', '') + ' ' +
                   article.get('abstract', '')).lower()

            counted = False
            for type_name, keywords in transporter_types.items():
                if not counted and any(kw in text for kw in keywords):
                    type_counts[type_name] += 1
                    counted = True

        # plant species labels -> keyword lists used for classification
        plant_keywords = {
            'Arabidopsis': ['arabidopsis', 'arabidopsis thaliana'],
            'Rice': ['rice', 'oryza', 'oryza sativa'],
            'Maize': ['maize', 'zea mays', 'corn'],
            'Wheat': ['wheat', 'triticum'],
            'Tobacco': ['tobacco', 'nicotiana'],
            'Mosses': ['moss', 'physcomitrium', 'physcomitrella'],
            'Other plants': ['plant', 'plants']
        }

        plant_counts = {k: 0 for k in plant_keywords.keys()}

        for article in articles:
            text = (article.get('title', '') + ' ' +
                   article.get('abstract', '') + ' ' +
                   article.get('journal', '')).lower()

            counted = False
            for plant_name, keywords in plant_keywords.items():
                if not counted and any(kw in text for kw in keywords):
                    plant_counts[plant_name] += 1
                    counted = True

        journal_counts = {}
        for article in articles:
            journal = article.get('journal', 'Unknown')
            journal_counts[journal] = journal_counts.get(journal, 0) + 1

        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.days_ago)

        report = {
            'timestamp': self.timestamp,
            'datetime': datetime.now().isoformat(),
            'days_ago': self.days_ago,
            'total_approved': len(articles),
            'search_period': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            'transporter_types': {k: v for k, v in type_counts.items() if v > 0},
            'plant_species': {k: v for k, v in plant_counts.items() if v > 0},
            'top_journals': dict(sorted(journal_counts.items(), key=lambda x: -x[1])[:10]),
            'articles': articles
        }

        report_file = self.run_dir / "report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        md_file = self.run_dir / "report.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# Plant Transporter Literature Daily Report\n\n")
            f.write(f"**Date**: {self.timestamp}\n")
            f.write(f"**Search period**: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}\n")
            f.write(f"**Passed filtering**: {len(articles)} articles\n\n")

            f.write(f"## Transporter Type Distribution\n\n")
            for type_name, count in type_counts.items():
                if count > 0:
                    f.write(f"- **{type_name}**: {count} articles\n")
            f.write(f"\n")

            f.write(f"## Plant Species Distribution\n\n")
            for plant_name, count in plant_counts.items():
                if count > 0:
                    f.write(f"- **{plant_name}**: {count} articles\n")
            f.write(f"\n")

            f.write(f"## Top Journals\n\n")
            f.write(f"| Journal | Articles |\n")
            f.write(f"|------|--------|\n")
            for journal, count in sorted(journal_counts.items(), key=lambda x: -x[1])[:10]:
                f.write(f"| {journal} | {count} |\n")
            f.write(f"\n")

            f.write(f"## Article List\n\n")
            for i, article in enumerate(articles, 1):
                f.write(f"### {i}. {article.get('title', 'N/A')}\n\n")
                f.write(f"- **Authors**: {article.get('authors', 'N/A')}\n")
                f.write(f"- **Journal**: {article.get('journal', 'N/A')}\n")
                f.write(f"- **Date**: {article.get('pub_date', 'N/A')}\n")
                f.write(f"- **DOI**: {article.get('doi', 'N/A')}\n")
                f.write(f"- **Filter reason**: {article.get('filter_reason', 'N/A')}\n")

                abstract = article.get('abstract', '')
                if abstract:
                    # preview: first 200 characters of the abstract
                    abstract_preview = abstract[:200] + '...' if len(abstract) > 200 else abstract
                    f.write(f"- **Abstract**: {abstract_preview}\n")
                f.write(f"\n")

        print(f"  Reports generated:")
        print(f"    JSON: {report_file}")
        print(f"    Markdown: {md_file}")

        print(f"\n  Summary statistics:")
        print(f"    Total articles: {len(articles)}")
        print(f"    Transporter types: {sum(1 for v in type_counts.values() if v > 0)}")
        print(f"    Plant species: {sum(1 for v in plant_counts.values() if v > 0)}")
        print(f"    Journals: {len(journal_counts)}")

        if articles:
            print(f"\n  Updating deduplication records...")
            self.collected_manager.mark_batch_collected(articles)

        return report_file, md_file

    def run(self):
        """Run the full pipeline."""
        print(f"\n{'='*60}")
        print(f"Plant Transporter Literature Automated Processing Pipeline")
        print(f"{'='*60}")
        print(f"Output directory: {self.run_dir}")
        print(f"Search period: last {self.days_ago} days")
        print(f"Filter mode: {'LLM API v2 (intelligent)' if self.use_llm else 'keyword'}")

        start_time = datetime.now()

        try:
            raw_files = self.step1_search_all_sources()

            if not raw_files:
                print("\nNo articles retrieved; pipeline terminated")
                return

            merged_file = self.step2_merge_and_deduplicate(raw_files)

            filtered_file = self.step3_filter_articles(merged_file)

            report_file, md_file = self.step4_generate_report(filtered_file)

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            print("\n" + "=" * 60)
            print("Pipeline complete!")
            print("=" * 60)
            print(f"Results directory: {self.run_dir}")
            print(f"Elapsed time: {duration:.1f}s")

        except KeyboardInterrupt:
            print("\n\nInterrupted by user; pipeline terminated")
        except Exception as e:
            print(f"\n\nPipeline error: {e}")
            import traceback
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description='Plant transporter literature automated processing pipeline (uses LLM API v2 by default)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Daily run (search the last 1 day, filter with the LLM API)
  export LLM_API_URL="https://api.deepseek.com"
  export LLM_API_KEY="sk-your-key"
  export LLM_MODEL="deepseek-chat"
  python3 auto_plant_transporter_pipeline.py --output-dir ./results --days-ago 1

  # Weekly run (search the last 7 days)
  python3 auto_plant_transporter_pipeline.py --output-dir ./results --days-ago 7

  # Fast keyword-only mode (no LLM)
  python3 auto_plant_transporter_pipeline.py --output-dir ./results --days-ago 7 --no-llm
        """
    )
    parser.add_argument(
        '--output-dir',
        default='./plant_transporter_results',
        help='base output directory (default: ./plant_transporter_results)'
    )
    parser.add_argument(
        '--days-ago',
        type=int,
        default=7,
        help='search articles from the last N days (default: 7)'
    )
    parser.add_argument(
        '--no-llm',
        action='store_true',
        help='disable LLM filtering and use keyword mode'
    )

    args = parser.parse_args()

    pipeline = LiteraturePipeline(
        output_base_dir=args.output_dir,
        days_ago=args.days_ago,
        use_llm=not args.no_llm
    )
    pipeline.run()


if __name__ == '__main__':
    main()
