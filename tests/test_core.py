from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentpulse import cleanup, qa, settings_store
from agentpulse.agents.critic import CriticAgent
from agentpulse.agents.planner import PlannerAgent
from agentpulse.agents.synthesizer import heuristic_synthesis
from agentpulse.config import Settings
from agentpulse.metrics import (
    VenueMetric,
    apply_quartiles,
    load_overrides,
    quality_for,
)
from agentpulse.models import Analysis, Paper, Synthesis, Theme
from agentpulse.orchestrator import ALL_SOURCES, Pipeline
from agentpulse.scoring import dedupe, rank
from agentpulse.sources import (
    is_ieee,
    parse_biorxiv,
    parse_crossref_items,
    parse_doaj,
    parse_europepmc,
    parse_hal,
    parse_openalex_works,
    parse_openreview_notes,
    parse_pubmed_xml,
    parse_zenodo,
    search_pubmed,
)
from agentpulse.taxonomy import (
    FIELD_SOURCES,
    TOPIC_PRESETS,
    children_of,
    classify_field,
    hotspot_terms,
    paper_fields,
    paper_matches_subfield,
    subfields_of,
)
from agentpulse.venues import (
    pick_source,
    venue_from_comment,
    venue_matches,
    venue_patterns,
)


class _DisabledLLM:
    enabled = False

    async def json(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        raise RuntimeError("llm disabled")


class _NullTrace:
    def event(self, *args, **kwargs):
        return None


def _agent() -> PlannerAgent:
    settings = Settings(chat_api_key="")
    return PlannerAgent(_DisabledLLM(), settings, _NullTrace())  # type: ignore[arg-type]


def _paper(uid: str, title: str, published: str, citations: int = 0) -> Paper:
    return Paper(
        uid=uid,
        source="arxiv",
        title=title,
        abstract="We propose a system. Experiments show improvements.",
        published=date.fromisoformat(published),
        url=f"https://arxiv.org/abs/{uid.split(':')[-1]}",
        citation_count=citations,
    )


class PlannerTest(unittest.TestCase):
    def test_chinese_topic_maps_to_english_queries(self):
        plan = asyncio.run(_agent().run("多智能体协作"))
        self.assertTrue(plan.queries)
        for query in plan.queries:
            self.assertFalse(
                any("\u4e00" <= ch <= "\u9fff" for ch in query),
                f"检索式不应包含中文：{query}",
            )
        joined = " ".join(plan.queries).lower()
        self.assertIn("multi-agent", joined)

    def test_english_topic_expands(self):
        plan = asyncio.run(_agent().run("agent memory"))
        self.assertTrue(any("agent memory" in q.lower() for q in plan.queries))

    def test_math_topic_maps_to_theorem_proving(self):
        plan = asyncio.run(_agent().run("定理证明 Agent"))
        joined = " ".join(plan.queries).lower()
        self.assertIn("theorem proving", joined)

    def test_general_hot_topics_map_to_english(self):
        cases = {
            "量子计算": "quantum computing",
            "mRNA 疫苗": "mrna vaccine",
            "气候变化": "climate change",
            "平台治理": "platform governance",
            "检索增强生成": "retrieval augmented generation",
        }
        for topic, expected in cases.items():
            plan = asyncio.run(_agent().run(topic))
            joined = " ".join(plan.queries).lower()
            self.assertIn(expected, joined, topic)


class OpenReviewTest(unittest.TestCase):
    def test_parse_filters_reviews_and_maps_fields(self):
        payload = {
            "notes": [
                {
                    "id": "abc123",
                    "forum": "abc123",
                    "cdate": 1790812800000,
                    "pdate": 1790812800000,
                    "content": {
                        "title": {"value": "Agent Benchmarking"},
                        "abstract": {"value": "We propose a new benchmark."},
                        "authors": {"value": ["Alice", "Bob"]},
                        "venue": {"value": "ICLR 2027"},
                    },
                },
                {
                    "id": "rev1",
                    "forum": "abc123",
                    "content": {"title": {"value": "Review of Agent Benchmarking"}},
                },
                {
                    "id": "old456",
                    "forum": "old456",
                    "cdate": 1600000000000,
                    "content": {"title": {"value": "Old Paper"}},
                },
            ]
        }
        papers = parse_openreview_notes(payload, date(2026, 9, 1))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.uid, "openreview:abc123")
        self.assertEqual(paper.source, "openreview")
        self.assertEqual(paper.authors, ["Alice", "Bob"])
        self.assertEqual(paper.published, date(2026, 10, 1))
        self.assertEqual(paper.venue, "ICLR 2027")
        self.assertIn("openreview.net/forum?id=abc123", paper.url)


class SynthesisTest(unittest.TestCase):
    def test_heuristic_uses_taxonomy_and_cleans_keywords(self):
        papers = [
            _paper("arxiv:9001", "Multi-Agent Collaboration with LLMs", "2026-11-01"),
            _paper("arxiv:9002", "Toolformer: Tool Use in Language Models", "2026-11-02"),
            _paper("arxiv:9003", "Storage Systems for Data Centers", "2026-11-03"),
        ]
        analyses = [
            Analysis(uid="arxiv:9001", keywords=["multi-agent", "collaboration", "0", "agent"]),
            Analysis(uid="arxiv:9002", keywords=["tool use", "api", "result"]),
            Analysis(uid="arxiv:9003", keywords=["storage", "data"]),
        ]
        synthesis = heuristic_synthesis(papers, analyses)
        names = {theme.name for theme in synthesis.themes}
        self.assertIn("多智能体协作", names)
        self.assertIn("工具调用", names)
        for theme in synthesis.themes:
            self.assertNotIn("0、", theme.summary)
            self.assertNotIn("、agent、", theme.summary)


class TaxonomyTest(unittest.TestCase):
    def test_arxiv_category_wins(self):
        paper = _paper("arxiv:9101", "Some Title", "2026-11-01")
        paper.categories = ["cs.MA", "cs.AI"]
        self.assertEqual(classify_field(paper), "计算机科学")

    def test_keyword_fallback(self):
        paper = _paper("openreview:9102", "Clinical Trial of a New Drug", "2026-11-01")
        paper.abstract = "We study patients with disease in a clinical setting."
        self.assertEqual(classify_field(paper), "医学")

    def test_unknown_defaults_to_other(self):
        paper = _paper("arxiv:9103", "Zzz Qqq", "2026-11-01")
        paper.abstract = "Zzz qqq."
        self.assertEqual(classify_field(paper), "其他")


class OpenAlexTest(unittest.TestCase):
    def test_rebuilds_abstract_and_maps_field(self):
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Agent Memory Systems",
                    "publication_date": "2026-11-01",
                    "primary_location": {
                        "source": {"display_name": "Zenodo"},
                        "landing_page_url": "https://example.org/x",
                    },
                    "primary_topic": {"field": {"display_name": "Computer Science"}},
                    "authorships": [{"author": {"display_name": "Alice"}}],
                    "cited_by_count": 5,
                    "doi": "https://doi.org/10.1/abc",
                    "abstract_inverted_index": {"Memory": [0], "matters": [1]},
                }
            ]
        }
        papers = parse_openalex_works(payload, date(2026, 10, 1))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.uid, "openalex:W123")
        self.assertEqual(paper.abstract, "Memory matters")
        self.assertEqual(paper.categories, ["Computer Science"])
        self.assertEqual(classify_field(paper), "计算机科学")


class CrossrefTest(unittest.TestCase):
    def test_strips_jats_and_splits_authors(self):
        payload = {
            "message": {
                "items": [
                    {
                        "DOI": "10.1/xyz",
                        "title": ["A Study"],
                        "abstract": "<jats:p>We propose &amp; test.</jats:p>",
                        "author": [{"given": "Bob", "family": "Lee"}],
                        "published": {"date-parts": [[2026, 11, 2]]},
                        "container-title": ["Journal of Tests"],
                        "subject": ["Computer Science Applications"],
                        "is-referenced-by-count": 7,
                        "URL": "https://doi.org/10.1/xyz",
                    }
                ]
            }
        }
        papers = parse_crossref_items(payload, date(2026, 10, 1))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.abstract, "We propose & test.")
        self.assertEqual(paper.authors, ["Bob Lee"])
        self.assertEqual(paper.published, date(2026, 11, 2))
        self.assertEqual(classify_field(paper), "计算机科学")


class EuropePmcTest(unittest.TestCase):
    def test_parses_biomedical_record(self):
        payload = {
            "resultList": {
                "result": [
                    {
                        "id": "PPR123",
                        "source": "PPR",
                        "title": "Clinical Agents",
                        "firstPublicationDate": "2026-11-03",
                        "abstractText": "Patients <b>benefit</b>.",
                        "authorString": "A, B",
                        "doi": "10.1/ppr",
                        "citedByCount": 2,
                    }
                ]
            }
        }
        papers = parse_europepmc(payload, date(2026, 10, 1))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.abstract, "Patients benefit.")
        self.assertEqual(paper.authors, ["A", "B"])
        self.assertEqual(paper.url, "https://doi.org/10.1/ppr")


class PubMedTest(unittest.TestCase):
    def test_parses_abstract_sections_and_authors(self):
        xml = """
        <PubmedArticleSet><PubmedArticle><MedlineCitation>
          <PMID>12345678</PMID>
          <Article>
            <Journal>
              <JournalIssue><PubDate><Year>2026</Year><Month>Nov</Month><Day>05</Day></PubDate></JournalIssue>
              <Title>Journal of Clinical AI</Title>
            </Journal>
            <ArticleTitle>Agents for <i>Clinical</i> Triage</ArticleTitle>
            <Abstract>
              <AbstractText Label="BACKGROUND">Patients need triage.</AbstractText>
              <AbstractText Label="RESULTS">Agents improve accuracy.</AbstractText>
            </Abstract>
            <AuthorList>
              <Author><ForeName>Jane</ForeName><LastName>Doe</LastName></Author>
            </AuthorList>
            <ELocationID EIdType="doi">10.1/pm</ELocationID>
          </Article>
        </MedlineCitation></PubmedArticle></PubmedArticleSet>
        """
        papers = parse_pubmed_xml(xml, date(2026, 10, 1))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.uid, "pubmed:12345678")
        self.assertEqual(paper.title, "Agents for Clinical Triage")
        self.assertIn("Patients need triage.", paper.abstract)
        self.assertIn("Agents improve accuracy.", paper.abstract)
        self.assertEqual(paper.authors, ["Jane Doe"])
        self.assertEqual(paper.published, date(2026, 11, 5))
        self.assertEqual(paper.venue, "Journal of Clinical AI")
        self.assertEqual(paper.url, "https://pubmed.ncbi.nlm.nih.gov/12345678/")


class _CaptureHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_json(self, url: str, params: dict) -> dict:
        self.calls.append((url, params))
        return {"esearchresult": {"idlist": []}}

    async def get_text(self, url: str, params: dict) -> str:
        self.calls.append((url, params))
        return "<PubmedArticleSet/>"


class PubMedRequestTest(unittest.IsolatedAsyncioTestCase):
    async def test_key_and_email_are_forwarded(self):
        http = _CaptureHttp()
        await search_pubmed(
            http, "agent memory", 5, date(2026, 10, 1), "KEY123", "dev@example.com"
        )
        params = http.calls[0][1]
        self.assertEqual(params["api_key"], "KEY123")
        self.assertEqual(params["email"], "dev@example.com")
        self.assertEqual(params["tool"], "agentpulse")
        self.assertEqual(params["datetype"], "pdat")

    async def test_no_key_means_no_param(self):
        http = _CaptureHttp()
        await search_pubmed(http, "agent memory", 5, date(2026, 10, 1))
        self.assertNotIn("api_key", http.calls[0][1])
        self.assertNotIn("email", http.calls[0][1])


class SettingsStoreTest(unittest.TestCase):
    def test_file_values_are_editable_and_effective(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings_store, "STORE_PATH", Path(tmp) / "settings.json"):
                info = settings_store.update(
                    {"contact_email": "dev@example.com", "ncbi_api_key": "abc12345"}
                )["fields"]
                self.assertTrue(info["ncbi_api_key"]["configured"])
                self.assertFalse(info["ncbi_api_key"]["locked"])
                self.assertEqual(info["ncbi_api_key"]["hint"], "****2345")
                self.assertEqual(
                    settings_store.effective_settings().contact_email, "dev@example.com"
                )

    def test_env_locks_field_and_rejects_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings_store, "STORE_PATH", Path(tmp) / "settings.json"):
                with patch.dict(os.environ, {"NCBI_API_KEY": "envkey12345"}):
                    info = settings_store.describe()["fields"]["ncbi_api_key"]
                    self.assertTrue(info["locked"])
                    self.assertEqual(info["source"], "env")
                    with self.assertRaises(PermissionError):
                        settings_store.update({"ncbi_api_key": "newkey12345"})

    def test_empty_value_clears_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings_store, "STORE_PATH", Path(tmp) / "settings.json"):
                settings_store.update({"contact_email": "a@b.com"})
                info = settings_store.update({"contact_email": ""})["fields"]
                self.assertFalse(info["contact_email"]["configured"])


class TaxonomyTest(unittest.TestCase):
    def test_presets_cover_non_agent_topics(self):
        self.assertIn("量子计算", TOPIC_PRESETS["物理与天文学"])
        self.assertIn("气候变化", TOPIC_PRESETS["地球与环境科学"])
        self.assertIn("肿瘤免疫", TOPIC_PRESETS["医学"])
        self.assertIn("平台治理", TOPIC_PRESETS["社会科学与心理学"])

    def test_subfield_registry_and_hotspot_terms(self):
        names = [node.name for node in subfields_of("计算机科学")]
        self.assertIn("自然语言处理", names)
        self.assertIn("机器学习", names)
        self.assertIn("retrieval augmented generation", hotspot_terms()["检索增强生成"])

    def test_paper_matches_subfield_by_category(self):
        paper = Paper(uid="s1", source="arxiv", title="t", categories=["cs.CL"])
        self.assertTrue(paper_matches_subfield(paper, "计算机科学", "自然语言处理"))
        self.assertFalse(paper_matches_subfield(paper, "计算机科学", "计算机视觉"))

    def test_all_fields_have_rich_subfields(self):
        self.assertEqual(len(subfields_of("")), 0)
        for field in TOPIC_PRESETS:
            if not field:
                continue
            self.assertGreaterEqual(len(subfields_of(field)), 5, field)

    def test_three_level_discipline(self):
        names = [node.name for node in children_of("计算机科学", "人工智能")]
        self.assertIn("大模型", names)
        paper = Paper(uid="s2", source="arxiv", title="t", categories=["cs.CL"])
        self.assertTrue(paper_matches_subfield(paper, "计算机科学", "人工智能", "大模型"))
        self.assertFalse(
            paper_matches_subfield(paper, "计算机科学", "计算机视觉", "三维视觉")
        )

    def test_children_cover_every_subfield(self):
        for field in TOPIC_PRESETS:
            if not field:
                continue
            for node in subfields_of(field):
                children = children_of(field, node.name)
                self.assertGreaterEqual(len(children), 2, f"{field}/{node.name}")

    def test_cross_listed_paper_matches_both_fields(self):
        paper = Paper(
            uid="u1", source="arxiv", title="t", categories=["cs.AI", "math.LO"]
        )
        self.assertEqual(classify_field(paper), "计算机科学")
        self.assertEqual(paper_fields(paper), {"计算机科学", "数学"})

    def test_math_primary_paper(self):
        paper = Paper(uid="u2", source="arxiv", title="t", categories=["math.LO"])
        self.assertEqual(classify_field(paper), "数学")
        self.assertEqual(paper_fields(paper), {"数学"})

    def test_expanded_keywords_fall_back_to_math(self):
        paper = Paper(
            uid="u3",
            source="pubmed",
            title="A proof assistant for formal verification",
            abstract="Lean and Coq proofs for geometry",
        )
        self.assertEqual(classify_field(paper), "数学")


class ScoringTest(unittest.TestCase):
    def test_dedupe_merges_duplicates(self):
        a = _paper("arxiv:2401.00001", "Same Title", "2026-10-01", 10)
        b = _paper("ss:abc", "Same Title", "2026-10-02", 99)
        merged = dedupe([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].citation_count, 99)

    def test_rank_prefers_recent_and_relevant(self):
        old = _paper("arxiv:2401.00002", "Unrelated topic about vision", "2025-01-01", 500)
        new = _paper("arxiv:2401.00003", "LLM agent with tool use", "2026-11-01", 0)
        result = rank(
            [old, new],
            topic="LLM agent",
            subthemes=[],
            queries=["tool use"],
            today=date(2026, 11, 13),
            limit=2,
        )
        self.assertEqual(result[0].uid, new.uid)

    def test_batch_quartiles_split_into_four_groups(self):
        metrics = [
            VenueMetric(name=str(value), impact_factor=float(value))
            for value in range(1, 9)
        ]
        apply_quartiles(metrics)
        quartiles = [metric.quartile for metric in metrics]
        self.assertEqual(quartiles.count("Q1"), 2)
        self.assertEqual(quartiles.count("Q2"), 2)
        self.assertEqual(quartiles.count("Q3"), 2)
        self.assertEqual(quartiles.count("Q4"), 2)

    def test_quality_for_matches_journal_name(self):
        metric = VenueMetric(
            name="Nature Communications", impact_factor=23.0, quartile="Q1", quality=0.9
        )
        self.assertEqual(
            quality_for("Nature Communications", {"nature communications": metric}), 0.9
        )
        self.assertIsNone(quality_for("arXiv", {"nature communications": metric}))

    def test_missing_impact_factor_skips_venue_dimension(self):
        paper = _paper("arxiv:2401.00009", "LLM agent with tool use", "2026-11-01")
        paper.venue = "arXiv"
        ranked = rank(
            [paper],
            topic="LLM agent",
            subthemes=[],
            queries=["tool use"],
            today=date(2026, 11, 13),
            limit=1,
            metrics={
                "nature communications": VenueMetric(
                    name="Nature Communications",
                    impact_factor=23.0,
                    quartile="Q1",
                    quality=0.9,
                )
            },
        )
        self.assertNotIn("venue", ranked[0].score_detail)
        self.assertGreater(ranked[0].score, 0)

    def test_venue_quality_changes_ranking(self):
        strong = _paper("openalex:1", "LLM agent with tool use", "2026-11-01")
        strong.venue = "Nature Communications"
        weak = _paper("openalex:2", "LLM agent with tool use", "2026-11-01")
        weak.venue = "Workshop on Unknown Topics"
        metrics = {
            "nature communications": VenueMetric(
                name="Nature Communications", impact_factor=23.0, quartile="Q1", quality=0.9
            )
        }
        result = rank(
            [weak, strong],
            topic="LLM agent",
            subthemes=[],
            queries=["tool use"],
            today=date(2026, 11, 13),
            limit=2,
            metrics=metrics,
        )
        self.assertEqual(result[0].uid, strong.uid)
        self.assertIn("venue", result[0].score_detail)

    def test_local_override_metrics_can_be_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal_metrics.json"
            path.write_text(
                json.dumps({"Nature": {"impact_factor": 64.8, "quartile": "Q1"}}),
                encoding="utf-8",
            )
            overrides = load_overrides(path)
        self.assertIn("nature", overrides)
        self.assertEqual(overrides["nature"]["quartile"], "Q1")


class OpenSourcesTest(unittest.TestCase):
    def test_doaj_parses_open_access_record(self):
        payload = {
            "results": [
                {
                    "id": "abc",
                    "bibjson": {
                        "title": "Open Science Study",
                        "abstract": "A study.",
                        "year": "2026",
                        "month": "10",
                        "author": [{"name": "Alice"}],
                        "journal": {"title": "Open Journal"},
                        "identifier": [{"type": "doi", "id": "10.1/doaj"}],
                    },
                }
            ]
        }
        papers = parse_doaj(payload, date(2026, 9, 1))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].source, "doaj")
        self.assertEqual(papers[0].published, date(2026, 10, 1))
        self.assertEqual(papers[0].url, "https://doi.org/10.1/doaj")

    def test_biorxiv_filters_by_query_and_splits_authors(self):
        payload = {
            "collection": [
                {
                    "doi": "10.1/bio",
                    "title": "Single-cell atlas of the brain",
                    "abstract": "We profile cells.",
                    "authors": "Alice; Bob",
                    "date": "2026-11-01",
                    "server": "biorxiv",
                    "category": "neuroscience",
                },
                {
                    "doi": "10.1/other",
                    "title": "Unrelated chemistry work",
                    "date": "2026-11-01",
                },
            ]
        }
        papers = parse_biorxiv(payload, date(2026, 10, 1), "single cell")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].authors, ["Alice", "Bob"])
        self.assertEqual(papers[0].venue, "biorxiv preprint")

    def test_hal_flattens_list_fields(self):
        payload = {
            "response": {
                "docs": [
                    {
                        "docid": "hal-1",
                        "title_s": ["Digital Humanities"],
                        "abstract_s": ["An abstract."],
                        "authFullName_s": ["Alice", "Bob"],
                        "producedDateY_i": 2026,
                        "uri_s": "https://hal.science/hal-1",
                    }
                ]
            }
        }
        papers = parse_hal(payload, date(2026, 1, 1))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].title, "Digital Humanities")
        self.assertEqual(papers[0].authors, ["Alice", "Bob"])

    def test_zenodo_parses_creators_and_date(self):
        payload = {
            "hits": {
                "hits": [
                    {
                        "id": 42,
                        "metadata": {
                            "title": "Dataset Paper",
                            "description": "<p>Desc</p>",
                            "publication_date": "2026-11-02",
                            "creators": [{"name": "Alice"}],
                            "doi": "10.1/zen",
                        },
                        "links": {"self_html": "https://zenodo.org/records/42"},
                    }
                ]
            }
        }
        papers = parse_zenodo(payload, date(2026, 10, 1))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].abstract, "Desc")
        self.assertEqual(papers[0].url, "https://zenodo.org/records/42")

    def test_europepmc_prefers_past_date_over_future_issue(self):
        payload = {
            "resultList": {
                "result": [
                    {
                        "id": "1",
                        "source": "MED",
                        "title": "Ahead of print paper",
                        "firstPublicationDate": "2027-01-01",
                        "electronicPublicationDate": "2027-01-01",
                        "firstIndexDate": "2026-10-19",
                    }
                ]
            }
        }
        papers = parse_europepmc(payload, date(2026, 1, 1))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].published, date(2026, 10, 19))

    def test_is_ieee_by_doi_or_venue(self):
        doi_paper = Paper(uid="x", source="crossref", title="t", url="https://doi.org/10.1109/TPEL.2026.1")
        venue_paper = Paper(uid="y", source="openalex", title="t", venue="IEEE Transactions on Power Electronics")
        other = Paper(uid="z", source="arxiv", title="t", venue="arXiv", url="https://arxiv.org/abs/1")
        self.assertTrue(is_ieee(doi_paper))
        self.assertTrue(is_ieee(venue_paper))
        self.assertFalse(is_ieee(other))

    def test_every_field_has_recommended_sources(self):
        for field, sources in FIELD_SOURCES.items():
            self.assertTrue(sources, field)
            for source in sources:
                self.assertIn(source, ALL_SOURCES, field)


class CriticTest(unittest.IsolatedAsyncioTestCase):
    def _fixtures(self) -> tuple[list[Paper], list[Analysis], Synthesis]:
        papers = [_paper(f"arxiv:{i}", f"Paper {i}", "2026-10-01") for i in range(4)]
        analyses = [
            Analysis(uid=p.uid, problem="p", contribution="c", relevance=0.8) for p in papers
        ]
        synthesis = Synthesis(
            themes=[Theme(name="T", summary="s", uids=[p.uid for p in papers])]
        )
        return papers, analyses, synthesis

    async def test_rule_mode_scores_without_llm(self):
        critic = CriticAgent(_DisabledLLM(), Settings(chat_api_key=""), _NullTrace())  # type: ignore[arg-type]
        critique = await critic.run(*self._fixtures())
        self.assertEqual(critique.mode, "rule")
        self.assertEqual(critique.score, 100.0)
        self.assertTrue(critique.ok)
        self.assertEqual(critique.dimensions["coverage"], 100.0)

    async def test_llm_score_is_fused_with_rule_score(self):
        class _ReviewLLM:
            enabled = True

            async def json(self, *args, **kwargs):
                return {"score": 80, "issues": ["缺少对比实验"], "strengths": ["结构清晰"]}

        critic = CriticAgent(_ReviewLLM(), Settings(chat_api_key="x"), _NullTrace())  # type: ignore[arg-type]
        critique = await critic.run(*self._fixtures(), topic="t")
        self.assertEqual(critique.mode, "llm")
        self.assertIn("缺少对比实验", critique.issues)
        self.assertAlmostEqual(critique.score, 88.0, places=1)
        self.assertTrue(critique.ok)

    async def test_llm_error_falls_back_to_rule(self):
        class _BrokenLLM:
            enabled = True

            async def json(self, *args, **kwargs):
                raise RuntimeError("timeout")

        critic = CriticAgent(_BrokenLLM(), Settings(chat_api_key="x"), _NullTrace())  # type: ignore[arg-type]
        critique = await critic.run(*self._fixtures())
        self.assertEqual(critique.mode, "rule")
        self.assertEqual(critique.score, 100.0)

    async def test_unknown_uid_blocks_even_with_high_model_score(self):
        class _ReviewLLM:
            enabled = True

            async def json(self, *args, **kwargs):
                return {"score": 99, "issues": [], "strengths": []}

        papers, analyses, synthesis = self._fixtures()
        synthesis = Synthesis(themes=[Theme(name="T", summary="s", uids=["ghost"])])
        critic = CriticAgent(_ReviewLLM(), Settings(chat_api_key="x"), _NullTrace())  # type: ignore[arg-type]
        critique = await critic.run(papers, analyses, synthesis, topic="t")
        self.assertFalse(critique.ok)


class VenueTest(unittest.TestCase):
    def test_acronym_matches_full_conference_name(self):
        venue = "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition"
        self.assertTrue(venue_matches(venue, "CVPR"))
        self.assertTrue(venue_matches("2025 IEEE/CVF Conference on CVPR", "cvpr"))

    def test_unknown_venue_falls_back_to_literal(self):
        self.assertTrue(venue_matches("Journal of Finance", "Journal of Finance"))
        self.assertFalse(venue_matches("Nature Communications", "CVPR"))

    def test_arxiv_comment_detects_venue(self):
        self.assertIn("CVPR", venue_from_comment("Accepted to CVPR 2025"))
        self.assertEqual(venue_from_comment("17 pages, 5 figures"), "")

    def test_legacy_acronym_and_year_suffix(self):
        self.assertIn("neurips", venue_patterns("NIPS 2017"))
        self.assertTrue(venue_matches("NeurIPS", "NIPS"))
        self.assertTrue(venue_matches("ICLR 2024", "ICLR 2024"))

    def test_pick_source_skips_workshop_and_related_series(self):
        results = [
            {
                "id": "https://openalex.org/S3",
                "display_name": "Advances in computer vision and pattern recognition",
                "works_count": 1101,
                "type": "book series",
            },
            {
                "id": "https://openalex.org/S2",
                "display_name": "IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW)",
                "works_count": 6064,
                "type": "conference",
            },
            {
                "id": "https://openalex.org/S1",
                "display_name": "IEEE Computer Society Conference on Computer Vision and Pattern Recognition",
                "works_count": 25295,
                "type": "conference",
            },
        ]
        picked = pick_source(results, "CVPR")
        self.assertEqual(picked["id"], "https://openalex.org/S1")
        self.assertIsNone(pick_source(results, "Fake Conference"))

    def test_pick_source_ignores_empty_venue(self):
        results = [
            {
                "id": "https://openalex.org/S0",
                "display_name": "Machine Learning for Health Workshop at NIPS",
                "works_count": 0,
                "type": "conference",
            },
            {
                "id": "https://openalex.org/S9",
                "display_name": "neural information processing systems",
                "works_count": 19262,
                "type": "conference",
            },
        ]
        self.assertEqual(pick_source(results, "NIPS")["id"], "https://openalex.org/S9")


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_venue_filter_keeps_only_matching_papers(self):
        settings = Settings(chat_api_key="")
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(settings, Path(tmp), no_llm=True, offline=True)
            run_dir = await pipeline.run("LLM agent", 30, ["arxiv"], 8, venue="NeurIPS")
            papers = json.loads(
                (run_dir / "candidates.json").read_text(encoding="utf-8")
            )
        self.assertTrue(papers)
        self.assertTrue(all("neurips" in p["venue"].lower() for p in papers))

    def test_build_jobs_adds_venue_query_for_text_sources(self):
        pipeline = Pipeline(Settings(chat_api_key=""), Path("."), no_llm=True, offline=True)
        jobs = pipeline._build_jobs(["q1"], "topic", ["crossref", "arxiv"], venue="Nature")
        self.assertIn(("crossref", "topic Nature"), jobs)
        self.assertNotIn(("arxiv", "topic Nature"), jobs)

    async def test_multiple_topics_merge_into_one_report(self):
        settings = Settings(chat_api_key="")
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(settings, Path(tmp), no_llm=True, offline=True)
            run_dir = await pipeline.run(
                topics=["LLM agent", "CUDA", "LLM agent"],
                days=30,
                sources=["arxiv"],
                max_papers=10,
            )
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
            events = [
                line for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual(plan["topic"], "LLM agent / CUDA")  # 去重且保持优先级
        self.assertTrue(events)

    async def test_offline_pipeline_generates_reports(self):
        settings = Settings(chat_api_key="")
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(settings, Path(tmp), no_llm=True, offline=True)
            run_dir = await pipeline.run(
                topic="多智能体协作",
                days=30,
                sources=["arxiv"],
                max_papers=10,
            )
            self.assertTrue((run_dir / "report.md").exists())
            self.assertTrue((run_dir / "report.html").exists())
            html = (run_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("AgentPulse", html)
            self.assertIn("多智能体协作", html)
            self.assertGreater(len(html), 2000)

    async def test_listener_receives_stage_events(self):
        settings = Settings(chat_api_key="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(
                settings, Path(tmp), no_llm=True, offline=True, listener=events.append
            )
            await pipeline.run(
                topic="agent evaluation", days=30, sources=["arxiv"], max_papers=5
            )
        stages = [e for e in events if e.get("event") == "stage"]
        agents = {e.get("agent") for e in stages}
        self.assertTrue({"planner", "search", "analyst", "writer"}.issubset(agents))


class QaTest(unittest.IsolatedAsyncioTestCase):
    def _run_dir(self, root: Path) -> Path:
        run_dir = root / "20260101-000000-demo"
        run_dir.mkdir(parents=True)
        papers = [
            {
                "uid": "arxiv:1",
                "title": "Retrieval Augmented Generation for Scientific Papers",
                "abstract": "We build a RAG pipeline over paper abstracts with citation grounding.",
                "venue": "ACL",
                "url": "https://example.org/1",
                "published": "2026-01-01",
            },
            {
                "uid": "arxiv:2",
                "title": "Multi Agent Debate for Reasoning",
                "abstract": "Several agents debate to improve reasoning accuracy on benchmarks.",
                "venue": "ICLR",
                "url": "https://example.org/2",
                "published": "2026-01-02",
            },
        ]
        analyses = [
            {
                "uid": "arxiv:1",
                "problem": "论文检索成本高",
                "method": "向量检索 + 引用约束生成",
                "contribution": "给出可溯源问答流程",
                "limitations": "依赖摘要质量",
                "reusable": "检索层可复用",
            },
            {
                "uid": "arxiv:2",
                "problem": "单模型推理不稳定",
                "method": "多智能体辩论后投票",
                "contribution": "提升推理准确率",
                "limitations": "推理成本翻倍",
                "reusable": "辩论协议可复用",
            },
        ]
        synthesis = {
            "themes": [
                {"name": "检索增强问答", "summary": "围绕论文库做检索增强", "uids": ["arxiv:1"]}
            ]
        }
        (run_dir / "candidates.json").write_text(json.dumps(papers, ensure_ascii=False), "utf-8")
        (run_dir / "analyses.json").write_text(json.dumps(analyses, ensure_ascii=False), "utf-8")
        (run_dir / "synthesis.json").write_text(json.dumps(synthesis, ensure_ascii=False), "utf-8")
        return run_dir

    def test_retrieve_matches_relevant_passage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(Path(tmp))
            passages = qa.build_passages(run_dir)
            ranked = qa.retrieve("RAG 论文问答的引用约束", passages)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][1].uids[0], "arxiv:1")

    async def test_rule_mode_answers_with_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(Path(tmp))
            result = await qa.answer("论文问答怎么保证引用可溯源", run_dir, Settings(chat_api_key=""))
        self.assertEqual(result["mode"], "rule")
        self.assertEqual(result["citations"][0]["uid"], "arxiv:1")
        self.assertTrue(result["answer"])

    async def test_unrelated_question_reports_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(Path(tmp))
            result = await qa.answer("今天北京天气如何", run_dir, Settings(chat_api_key=""))
        self.assertEqual(result["citations"], [])
        self.assertIn("没有找到", result["answer"])

    async def test_method_question_lists_each_paper_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(Path(tmp))
            result = await qa.answer("这些论文的主要方法有啥差异", run_dir, Settings(chat_api_key=""))
        self.assertIn("逐篇方法", result["answer"])
        self.assertIn("向量检索 + 引用约束生成", result["answer"])
        self.assertIn("多智能体辩论后投票", result["answer"])
        self.assertEqual([c["uid"] for c in result["citations"]], ["arxiv:1", "arxiv:2"])

    async def test_llm_mode_answers_with_citations(self):
        class _FakeClient:
            def __init__(self, settings):
                self.settings = settings

            async def json(self, system, user, temperature=0.2):
                if "keywords" in system:
                    return {"keywords": ["retrieval", "citation"]}
                return {"answer": "两者差异在检索层与引用约束。", "citations": ["arxiv:1"]}

            async def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(Path(tmp))
            settings = Settings(chat_api_key="test-key-123456")
            with patch.object(qa, "LLMClient", _FakeClient):
                result = await qa.answer("论文问答的引用如何保证", run_dir, settings)
        self.assertEqual(result["mode"], "llm")
        self.assertEqual(result["citations"][0]["uid"], "arxiv:1")


class CleanupTest(unittest.TestCase):
    def _root(self, tmp: Path, count: int = 5) -> Path:
        root = tmp / "reports"
        root.mkdir()
        for index in range(count):
            run_dir = root / f"2026010{index}-000000-topic"
            run_dir.mkdir()
            (run_dir / "report.md").write_text("x" * 1024, encoding="utf-8")
            stamp = 1_700_000_000 + index * 3600
            os.utime(run_dir, (stamp, stamp))
        (root / "notes").mkdir()  # 非报告目录不应被清理
        return root

    def test_plan_keeps_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            remove, kept = cleanup.plan(root, keep=2)
        self.assertEqual(len(remove), 3)
        self.assertEqual([path.name for path in kept], [
            "20260104-000000-topic",
            "20260103-000000-topic",
        ])

    def test_clean_dry_run_keeps_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            result = cleanup.clean(root, keep=2, dry_run=True)
            self.assertEqual(len(result.removed), 3)
            self.assertTrue(result.freed_bytes > 0)
            self.assertEqual(len(list(root.iterdir())), 6)

    def test_clean_removes_old_and_keeps_non_report_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            result = cleanup.clean(root, keep=2)
            self.assertEqual(len(result.removed), 3)
            self.assertTrue(all(not path.exists() for path in result.removed))
            self.assertTrue((root / "notes").is_dir())
            self.assertTrue((root / "20260104-000000-topic" / "report.md").exists())

    def test_days_option_removes_expired_even_if_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp), count=2)
            remove, kept = cleanup.plan(root, keep=5, days=1, now=1_700_000_000 + 20 * 86400)
        self.assertEqual(len(remove), 2)
        self.assertEqual(kept, [])

    def test_exclude_protects_active_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp), count=3)
            active = root / "20260102-000000-topic"
            remove, kept = cleanup.plan(root, keep=0, exclude={active})
        self.assertEqual(kept, [active])
        self.assertEqual(len(remove), 2)


if __name__ == "__main__":
    unittest.main()
