from __future__ import annotations

import math
import re
from datetime import date

from agentpulse.metrics import VenueMetric, quality_for
from agentpulse.models import Paper

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with", "by",
    "is", "are", "we", "our", "this", "that", "these", "those", "from", "as",
    "at", "be", "can", "it", "its", "their", "which", "using", "based", "via",
    "paper", "approach", "method", "results", "show", "propose", "proposed",
}

_SOURCE_WEIGHT = {
    "arxiv": 1.0,
    "openreview": 0.95,
    "openalex": 0.95,
    "europepmc": 0.95,
    "pubmed": 0.95,
    "crossref": 0.9,
    "semantic_scholar": 0.95,
    "hackernews": 0.7,
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def paper_key(paper: Paper) -> str:
    """去重键：按标题归一化，跨源也能合并同一篇论文。"""
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", paper.title.lower()).strip()
    return f"title:{title}" if title else f"uid:{paper.uid}"


def dedupe(papers: list[Paper]) -> list[Paper]:
    """同一篇论文被多个源召回时合并，保留信息更全的那条。"""
    merged: dict[str, Paper] = {}
    for paper in papers:
        key = paper_key(paper)
        if key not in merged:
            merged[key] = paper
            continue
        kept = merged[key]
        kept.abstract = kept.abstract or paper.abstract
        kept.authors = kept.authors or paper.authors
        kept.published = kept.published or paper.published
        kept.url = kept.url or paper.url
        kept.venue = kept.venue or paper.venue
        kept.citation_count = max(kept.citation_count, paper.citation_count)
        kept.heat = max(kept.heat, paper.heat)
        if kept.source == "hackernews" and paper.source != "hackernews":
            # 论文源优先，但保留热点分
            heat = kept.heat
            paper.heat = heat
            merged[key] = paper
    return list(merged.values())


def _keyword_score(paper: Paper, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    title_tokens = set(tokenize(paper.title))
    abstract_tokens = set(tokenize(paper.abstract))
    hit_title = len(query_tokens & title_tokens) / len(query_tokens)
    hit_abstract = len(query_tokens & abstract_tokens) / len(query_tokens)
    return min(1.0, 0.7 * hit_title + 0.3 * hit_abstract)


def _recency_score(paper: Paper, today: date, half_life_days: float) -> float:
    if paper.published is None:
        return 0.2
    age = max(0, (today - paper.published).days)
    return 0.5 ** (age / half_life_days)


def score_paper(
    paper: Paper,
    query_tokens: set[str],
    today: date,
    half_life_days: float = 10.0,
    venue_quality: float | None = None,
) -> Paper:
    """规则打分：关键词 38%、新鲜度 25%、引用 12%、期刊/会议质量 17%、社区热度 8%。

    期刊/会议质量来自影响因子代理值与分区（见 metrics.py）。若该论文没有可用的
    影响因子/分区，则不计入这一项，其余维度按比例归一化，避免用中性分稀释。
    """
    keyword = _keyword_score(paper, query_tokens)
    recency = _recency_score(paper, today, half_life_days)
    citations = min(1.0, math.log1p(max(paper.citation_count, 0)) / math.log1p(1000))
    heat = min(1.0, math.log1p(max(paper.heat, 0.0)) / math.log1p(500))

    detail = {
        "keyword": round(keyword, 4),
        "recency": round(recency, 4),
        "citations": round(citations, 4),
        "heat": round(heat, 4),
    }
    base = 0.38 * keyword + 0.25 * recency + 0.12 * citations + 0.08 * heat
    if venue_quality is None:
        total = base / 0.83  # 无影响力数据：去掉 17% 的权重后归一化
    else:
        quality = max(0.0, min(1.0, venue_quality))
        total = base + 0.17 * quality
        detail["venue"] = round(quality, 4)
    total *= _SOURCE_WEIGHT.get(paper.source, 0.8)

    paper.score = round(total, 4)
    paper.score_detail = detail
    return paper


def rank(
    papers: list[Paper],
    topic: str,
    subthemes: list[str],
    queries: list[str],
    today: date,
    limit: int,
    metrics: dict[str, VenueMetric] | None = None,
) -> list[Paper]:
    query_tokens = set(tokenize(" ".join([topic, *subthemes, *queries])))
    scored = [
        score_paper(
            p,
            query_tokens,
            today,
            venue_quality=quality_for(p.venue, metrics) if metrics else None,
        )
        for p in papers
    ]
    scored.sort(key=lambda p: (p.score, p.published or date.min), reverse=True)
    return scored[:limit]
