"""Unit/integration tests for symbol_literature_mapper"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import symbol_literature_mapper as slm  # noqa: E402


MINI_SYMBOLS = [
    'NPF', 'NPF2.13', 'PIP', 'PIP2;1', 'HAK5', 'GORK', '2A6',
    '3BETAHSD/D1', 'ACLA-1',
]


@pytest.fixture
def mini_symbol_list(tmp_path):
    p = tmp_path / 'symbol.list'
    p.write_text('\n'.join(MINI_SYMBOLS) + '\n', encoding='utf-8')
    return p


def make_paper(filename='10.1007_test.md', doi='10.1007/test', title='Test paper',
               abstract='', sections=None, **extra):
    return {
        'filename': filename, 'title': title, 'author': 'Author A; Author B',
        'abstract': abstract, 'extraction_method': 'md_sections',
        'DOI': doi, 'doc_type': 'Unknown',
        'sections': sections if sections is not None else {},
        **extra,
    }


def write_papers_json(path, papers):
    path.write_text(json.dumps(papers, ensure_ascii=False), encoding='utf-8')
    return path


class TestFamilyKey:
    @pytest.mark.parametrize('sym,expected', [
        ('NPF2.13', 'NPF'),      # every trailing numeric group stripped
        ('NPF2', 'NPF'),
        ('PIP2;1', 'PIP'),       # aligns with the library family entry PIP
        ('HAK5', 'HAK'),
        ('CAX11', 'CAX'),
        ('ACLA-1', 'ACLA'),      # hyphenated number
        ('2A6', '2A'),           # stops after one strip
        ('3BETAHSD/D1', '3BETAHSD/D'),  # stops at the letter tail
        ('A1', 'A1'),            # guard: length < 2 after stripping falls back to the original
        ('NPF', 'NPF'),          # family name itself
        ('GORK', 'GORK'),        # all-caps without digits
    ])
    def test_family_key(self, sym, expected):
        assert slm.family_key(sym) == expected


class TestLoadPapersArticles:
    def test_field_mapping(self, tmp_path):
        papers = [make_paper(
            doi='10.1/x', title='T', abstract='abs',
            sections={'Results': 'body text'},
        )]
        path = write_papers_json(tmp_path / 'papers.json', papers)
        arts = slm.load_papers_articles(path)
        assert len(arts) == 1
        a = arts[0]
        assert a['doi'] == '10.1/x'
        assert a['authors'] == 'Author A; Author B'
        assert a['title'] == 'T' and a['abstract'] == 'abs'
        assert a['journal'] == '' and a['pub_date'] == '' and a['pmid'] == ''
        assert a['doc_type'] == 'Unknown' and a['filename'] == '10.1007_test.md'
        assert a['sections'] == {'Results': 'body text'}
        assert a['_status'] == 'approved' and a['_source'] == 'papers.json'
        # filename fills the id slot as the article_identity fallback
        assert a['id'] == '10.1007_test.md'

    def test_non_dict_skipped(self, tmp_path):
        path = write_papers_json(tmp_path / 'papers.json',
                                 [make_paper(), 'not a dict', 42])
        arts = slm.load_papers_articles(path)
        assert len(arts) == 1

    def test_sections_non_dict_becomes_empty(self, tmp_path):
        p = make_paper()
        p['sections'] = ['Results', 'oops']
        path = write_papers_json(tmp_path / 'papers.json', [p])
        assert slm.load_papers_articles(path)[0]['sections'] == {}

    def test_identity_uses_doi_then_filename(self, tmp_path):
        path = write_papers_json(tmp_path / 'papers.json',
                                 [make_paper(doi='10.1/a'),
                                  make_paper(doi='', filename='f.md')])
        a1, a2 = slm.load_papers_articles(path)
        assert slm.article_identity(a1) == 'doi:10.1/a'
        assert slm.article_identity(a2) == 'id:f.md'

    def test_malformed_json_exits(self, tmp_path):
        bad = tmp_path / 'bad.json'
        bad.write_text('{not valid', encoding='utf-8')
        with pytest.raises(SystemExit):
            slm.load_papers_articles(bad)

    def test_top_level_not_list_exits(self, tmp_path):
        path = write_papers_json(tmp_path / 'papers.json', {'a': 1})
        with pytest.raises(SystemExit):
            slm.load_papers_articles(path)


def build_matcher(symbol_list):
    lib = slm.SymbolLibrary()
    lib.load([symbol_list])
    return slm.Matcher(lib), lib


class TestSectionsMatching:
    def test_section_hit_recorded_with_sec_prefix(self, mini_symbol_list):
        matcher, _ = build_matcher(mini_symbol_list)
        art = make_paper(title='NPF2.13 study', abstract='nothing here',
                         sections={'Results': 'NPF2.13 transports nitrate.'})
        hits = matcher.match_article(art, ['title', 'abstract', 'sections'])
        assert 'NPF2.13' in hits
        assert hits['NPF2.13']['field_counts'] == {'title': 1, 'sec:Results': 1}

    def test_abstract_vs_section_distinguished(self, mini_symbol_list):
        matcher, _ = build_matcher(mini_symbol_list)
        art = make_paper(abstract='PIP2;1 is an aquaporin.',
                         sections={'Introduction': 'PIP family is large.'})
        hits = matcher.match_article(art, ['title', 'abstract', 'sections'])
        assert hits['PIP2;1']['field_counts'] == {'abstract': 1}
        assert hits['PIP']['field_counts'] == {'sec:Introduction': 1}

    def test_semicolon_in_section_name_normalized(self, mini_symbol_list):
        matcher, _ = build_matcher(mini_symbol_list)
        art = make_paper(sections={'Results; discussion': 'HAK5 expressed.'})
        hits = matcher.match_article(art, ['sections'])
        assert hits['HAK5']['field_counts'] == {'sec:Results, discussion': 1}

    def test_sections_empty_or_missing_ok(self, mini_symbol_list):
        matcher, _ = build_matcher(mini_symbol_list)
        assert matcher.match_article(make_paper(), ['sections']) == {}

    def test_valid_fields_contains_sections(self):
        assert 'sections' in slm.VALID_FIELDS
        assert slm.DEFAULT_FIELDS == ('title', 'abstract', 'filter_reason')  # run-mode defaults unchanged


class TestPapersModeIntegration:
    def run_script(self, *cli_args, check=True):
        import os
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('LLM_', 'USE_LLM_API'))}  # never trigger real API calls in tests
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'symbol_literature_mapper.py'), *cli_args],
            capture_output=True, text=True, check=check, cwd=SCRIPT_DIR, env=env)

    def test_end_to_end(self, tmp_path, mini_symbol_list):
        papers = [
            make_paper(doi='10.1/a', filename='a.md',
                       title='NPF2.13 function in rice',
                       abstract='Nitrate transport.',
                       sections={'Results': 'NPF2.13 and HAK5 were expressed.'}),
            make_paper(doi='10.1/b', filename='b.md',
                       title='Aquaporin PIP2;1',
                       abstract='PIP2;1 localizes to plasma membrane.',
                       sections={'Introduction': 'The PIP family is large.'}),
            make_paper(doi='10.1/c', filename='c.md',
                       title='Unrelated ecology',
                       abstract='Forest dynamics over decades.',
                       sections={'Results': 'Trees grew.'}),
        ]
        pj = write_papers_json(tmp_path / 'papers_metadata_md.json', papers)
        out = tmp_path / 'out'
        # --no-ath-filter: this case covers the legacy full-library behavior;
        # ath mode is covered by TestAthFilterMode
        r = self.run_script('--papers-json', str(pj), '--symbol-list', str(mini_symbol_list),
                            '--output-dir', str(out), '--no-ath-filter')
        assert r.returncode == 0, r.stderr + r.stdout
        payload = json.loads((out / 'symbol_literature_map.json').read_text(encoding='utf-8'))
        assert payload['papers_json'] == str(pj.resolve())
        assert payload['runs_processed'] == ['papers_metadata_md']
        assert payload['stats']['unique_articles'] == 3
        assert set(payload['symbols']) == {'NPF2.13', 'HAK5', 'PIP2;1', 'PIP'}
        npt = next(a for a in payload['symbols']['NPF2.13']['articles'] if a['doi'] == '10.1/a')
        assert 'sec:Results' in npt['match_counts']
        # options records the resolved fields
        assert payload['options']['fields'] == ['title', 'abstract', 'sections']
        assert payload['stats']['articles_without_symbols'] == 1
        assert payload['symbols']['NPF2.13']['family'] == 'NPF'
        assert payload['families']['NPF']['member_symbols'] == ['NPF2.13']
        assert payload['families']['PIP']['member_symbols'] == ['PIP', 'PIP2;1']
        assert payload['stats']['families'] == len(payload['families'])
        assert (out / 'symbol_family_map.tsv').exists()
        # provenance fields
        assert npt['filename'] == 'a.md'
        tsv = (out / 'symbol_literature_map.tsv').read_text(encoding='utf-8').splitlines()
        assert tsv[0].split('\t')[-1] == 'source_file'

    def test_bad_path_exits(self, tmp_path, mini_symbol_list):
        r = self.run_script('--papers-json', str(tmp_path / 'nope.json'),
                            '--symbol-list', str(mini_symbol_list), check=False)
        assert r.returncode != 0

    def test_symbol_list_parent_fallback(self, tmp_path, monkeypatch):
        # self-contained layout: no symbol.list in the script dir, one in the
        # parent directory -> the parent copy is used (the release may colocate
        # the script and library, so the real tree cannot be assumed here)
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'symbol.list').write_text('X\n', encoding='utf-8')
        assert Path(slm.default_symbol_list(tmp_path / 'sub')) == (tmp_path / 'symbol.list').resolve()
        # a symbol.list next to the script wins over the parent copy
        (tmp_path / 'sub' / 'symbol.list').write_text('Y\n', encoding='utf-8')
        assert Path(slm.default_symbol_list(tmp_path / 'sub')) == (tmp_path / 'sub' / 'symbol.list').resolve()


class TestFamilyRollup:
    def test_build_families_index(self):
        sym_arts = {
            'NPF2.13': {'family': 'NPF', 'articles': [
                {'doi': '10.1/a', 'pmid': '', 'title': 'T', 'journal': '', 'pub_date': '',
                 'filename': 'a.md', 'status': 'approved', 'runs': ['papers_metadata_md'],
                 '_identity': 'doi:10.1/a'}]},
            'HAK5': {'family': 'HAK', 'articles': [
                {'doi': '10.1/a', 'pmid': '', 'title': 'T', 'journal': '', 'pub_date': '',
                 'filename': 'a.md', 'status': 'approved', 'runs': ['papers_metadata_md'],
                 '_identity': 'doi:10.1/a'}]},
        }
        fams = slm.build_families_index(sym_arts)
        assert fams['NPF']['member_symbols'] == ['NPF2.13']
        assert fams['NPF']['article_count'] == 1
        art = fams['NPF']['articles'][0]
        assert art['member_symbols'] == ['NPF2.13'] and art['doi'] == '10.1/a'
        assert fams['HAK']['member_symbols'] == ['HAK5']

    def test_family_union_across_members(self):
        sym_arts = {
            'PIP': {'family': 'PIP', 'articles': [
                {'doi': '10.1/x', '_identity': 'doi:10.1/x', 'pmid': '', 'title': 'A',
                 'journal': '', 'pub_date': '', 'filename': 'x.md', 'status': 'approved',
                 'runs': ['s']}]},
            'PIP2;1': {'family': 'PIP', 'articles': [
                {'doi': '10.1/x', '_identity': 'doi:10.1/x', 'pmid': '', 'title': 'A',
                 'journal': '', 'pub_date': '', 'filename': 'x.md', 'status': 'approved',
                 'runs': ['s']},
                {'doi': '10.1/y', '_identity': 'doi:10.1/y', 'pmid': '', 'title': 'B',
                 'journal': '', 'pub_date': '', 'filename': 'y.md', 'status': 'approved',
                 'runs': ['s']}]},
        }
        fams = slm.build_families_index(sym_arts)
        assert fams['PIP']['member_symbols'] == ['PIP', 'PIP2;1']
        assert fams['PIP']['article_count'] == 2
        by_doi = {a['doi']: a for a in fams['PIP']['articles']}
        assert by_doi['10.1/x']['member_symbols'] == ['PIP', 'PIP2;1']
        assert by_doi['10.1/y']['member_symbols'] == ['PIP2;1']

    def test_family_tsv_written(self, tmp_path):
        fams = {'NPF': {'member_symbols': ['NPF2.13'], 'article_count': 1, 'articles': [
            {'doi': '10.1/a', 'pmid': '', 'title': 'T', 'journal': '', 'pub_date': '',
             'filename': 'a.md', 'status': 'approved', 'runs': ['papers_metadata_md'],
             'member_symbols': ['NPF2.13'], '_identity': 'doi:10.1/a'}]}}
        path = tmp_path / 'fam.tsv'
        n = slm.write_family_tsv(fams, path)
        assert n == 1
        lines = path.read_text(encoding='utf-8').splitlines()
        assert lines[0].split('\t') == ['family', 'member_symbols', 'DOI', 'PMID', 'title',
                                        'journal', 'pub_date', 'source_file', 'source_runs', 'status']
        assert lines[1].split('\t')[0] == 'NPF'
        assert lines[1].split('\t')[1] == 'NPF2.13'

    def test_new_symbol_entries_have_family(self):
        from collections import Counter
        ns = {'npflike1': {'forms': Counter({'NPFLIKE1': 1}),
                           'articles': {'doi:10.1/z': False}}}
        payload, entries = slm.build_new_symbols_output(ns, {}, ())
        assert entries[0]['family'] == 'NPFLIKE'


class TestRunModeRegression:
    def test_run_mode_still_works(self, tmp_path, mini_symbol_list):
        import os
        run = tmp_path / 'results' / 'run_20260909'
        run.mkdir(parents=True)
        articles = [{
            'doi': '10.9/z', 'title': 'GORK channel', 'authors': 'A', 'journal': 'J',
            'pub_date': '2024', 'abstract': 'GORK mediates K+ efflux.', 'filter_reason': 'r',
        }]
        (run / 'filtered.json').write_text(json.dumps(articles), encoding='utf-8')
        out = tmp_path / 'out'
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('LLM_', 'USE_LLM_API'))}
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'symbol_literature_mapper.py'),
             '--results-dir', str(tmp_path / 'results'), '--symbol-list', str(mini_symbol_list),
             '--output-dir', str(out), '--no-ath-filter'],
            capture_output=True, text=True, check=True, cwd=SCRIPT_DIR, env=env)
        payload = json.loads((out / 'symbol_literature_map.json').read_text(encoding='utf-8'))
        assert payload['runs_processed'] == ['run_20260909']
        assert 'GORK' in payload['symbols']
        # run-mode default fields unchanged; source-file column empty; family view also written
        assert payload['options']['fields'] == ['title', 'abstract', 'filter_reason']
        tsv = (out / 'symbol_literature_map.tsv').read_text(encoding='utf-8').splitlines()
        assert tsv[0].split('\t')[-1] == 'source_file'
        assert tsv[1].split('\t')[-1] == ''
        assert (out / 'symbol_family_map.tsv').exists()
        assert payload['symbols']['GORK']['family'] == 'GORK'


# ============================ ath normalization mode ============================

def write_mini_ath(tmp_path):
    """Synthetic ath authority table + Araport bridge table (no dependency on the real large files)"""
    ath = tmp_path / 'ath_symbol_tc_final_v2.tsv'
    ath.write_text(
        'Ath_id\tSymbol\ttc\n'
        'AT1G01010\tNPF\t2.A.17\n'
        'AT1G02020\tHAK\t2.A.72\n'
        'AT1G02020\tKUP\t2.A.72\n'      # same Ath_id carrying two names -> HAK5 becomes multi-label
        'AT1G03030\tPIP\t1.A.8\n'
        'AT1G04040\tEDS5\t2.A.3\n',     # family name ending in a digit: regression case where family_key is not identity
        encoding='utf-8')
    araport = tmp_path / 'Araport11_mRNA_info.tsv'
    araport.write_text(
        'parent_gene\tgene_symbol\n'
        'AT1G01010\tNPF2.13\n'
        'AT1G02020\tHAK5\n'
        'AT1G03030\tPIP2;1\n'
        'AT9G99999\t2A6\n',              # Arabidopsis gene but not in the ath transporter table
        encoding='utf-8')
    return ath, araport


class TestAthFilterUnit:
    def test_apply_ath_filter_and_convert(self, mini_symbol_list):
        from collections import Counter
        lib = slm.SymbolLibrary()
        lib.load([mini_symbol_list])
        ath_id_to_syms = {'AT1G02020': {'HAK', 'KUP'}, 'AT1G01010': {'NPF'}, 'AT1G03030': {'PIP'}}
        # isomorphic to build_symbol_mapping output: same-name library entries self-map (NPF/PIP)
        sym_to_ath = {'NPF': {'NPF'}, 'NPF2.13': {'NPF'}, 'PIP': {'PIP'},
                      'PIP2;1': {'PIP'}, 'HAK5': {'HAK', 'KUP'}}
        lib.apply_ath_filter(sym_to_ath, {'NPF', 'HAK', 'KUP', 'PIP'},
                             {'NPF': {'2.A.17'}, 'HAK': {'2.A.72'}, 'KUP': {'2.A.72'}, 'PIP': {'1.A.8'}},
                             {'NPF': {'AT1G01010'}, 'HAK': {'AT1G02020'}, 'KUP': {'AT1G02020'}, 'PIP': {'AT1G03030'}})
        # non-ath entries dropped, family names added
        assert lib.lookup('GORK') is None and lib.lookup('2A6') is None
        assert set(lib.excluded_non_ath) >= {'GORK', '2A6', 'ACLA-1'}
        for fam in ('NPF', 'HAK', 'KUP', 'PIP'):
            assert lib.lookup(fam) == fam
        # hit-key conversion: HAK5 multi-label expansion, counts/members merged
        hits = {'HAK5': {'forms': {'OsHAK5'}, 'vias': Counter({'prefix': 1}),
                         'field_counts': Counter({'abstract': 2})}}
        converted = slm.convert_hits_to_ath({'doi:x': hits}, sym_to_ath)
        assert set(converted['doi:x']) == {'HAK', 'KUP'}
        for c in converted['doi:x'].values():
            assert c['forms'] == {'OsHAK5'} and c['members'] == {'HAK5'}
            assert c['field_counts'] == {'abstract': 2}

    def test_merge_llm_results_drops_new_in_ath_mode(self, mini_symbol_list):
        lib = slm.SymbolLibrary()
        lib.load([mini_symbol_list])
        matcher = slm.Matcher(lib)
        records = {'doi:x': {'identity': 'doi:x',
                             'article': {'doi': '10.1/x', 'title': 'T'}, 'status': 'approved', 'runs': ['r']}}
        hits = {}
        llm = {'doi:x': [{'symbol': 'HAK5', 'is_main': True},   # hits the library -> recorded
                         {'symbol': 'XYZ9', 'is_main': False}]}  # non-synonymous -> dropped in ath mode
        dropped = {}
        new_syms = slm.merge_llm_results(records, hits, llm, matcher, lib,
                                         record_new=False, dropped=dropped)
        assert set(hits['doi:x']) == {'HAK5'}
        assert new_syms == {}
        assert dropped.get('llm_new_dropped') == 1


class TestAthFilterMode:
    def run_script(self, *cli_args, check=True):
        import os
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('LLM_', 'USE_LLM_API'))}
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'symbol_literature_mapper.py'), *cli_args],
            capture_output=True, text=True, check=check, cwd=SCRIPT_DIR, env=env)

    def test_end_to_end_ath(self, tmp_path, mini_symbol_list):
        ath, araport = write_mini_ath(tmp_path)
        papers = [
            make_paper(doi='10.1/a', filename='a.md',
                       title='NPF2.13 function in rice',
                       abstract='Nitrate transport by NPF2.13.',
                       sections={'Results': 'NPF2.13, OsHAK5 and GORK were expressed.'}),
            make_paper(doi='10.1/b', filename='b.md',
                       title='Aquaporin PIP2;1 and EDS5',
                       abstract='PIP2;1 localizes to plasma membrane.',
                       sections={'Introduction': 'The PIP family is large; EDS5 exports.'}),
            make_paper(doi='10.1/c', filename='c.md',
                       title='Unrelated ecology',
                       abstract='Forest dynamics over decades.',
                       sections={'Results': 'Trees grew.'}),
        ]
        pj = write_papers_json(tmp_path / 'papers.json', papers)
        out = tmp_path / 'out'
        r = self.run_script('--papers-json', str(pj), '--symbol-list', str(mini_symbol_list),
                            '--ath-table', str(ath), '--araport', str(araport),
                            '--output-dir', str(out))
        assert r.returncode == 0, r.stderr + r.stdout
        payload = json.loads((out / 'symbol_literature_map.json').read_text(encoding='utf-8'))
        assert payload['options']['ath_filter'] is True
        # all output keys are ath symbols; GORK (in the library but not ath) is not recorded
        assert set(payload['symbols']) == {'NPF', 'HAK', 'KUP', 'PIP', 'EDS5'}
        assert 'GORK' not in payload['symbols']
        # multi-label expansion + members/original forms kept
        assert payload['symbols']['HAK']['member_symbols'] == ['HAK5']
        assert payload['symbols']['HAK']['tc'] == ['2.A.72']
        npf_art = next(a for a in payload['symbols']['NPF']['articles'] if a['doi'] == '10.1/a')
        assert 'NPF2.13' in npf_art['matched_forms']
        # zero-hit papers are a normal outcome
        assert payload['stats']['articles_without_symbols'] == 1
        # TSV ath-mode headers
        tsv = (out / 'symbol_literature_map.tsv').read_text(encoding='utf-8').splitlines()
        assert tsv[0].split('\t')[:3] == ['ath_symbol', 'tc', 'member_symbols']
        # ath-mode family view is identity-grouped (EDS5 keeps its form, not truncated to EDS)
        assert set(payload['families']) >= {'NPF', 'HAK', 'KUP', 'PIP', 'EDS5'}
        assert payload['families']['HAK']['member_symbols'] == ['HAK']
        assert payload['symbols']['EDS5']['family'] == 'EDS5'
        assert (out / 'symbol_family_map.tsv').exists()
