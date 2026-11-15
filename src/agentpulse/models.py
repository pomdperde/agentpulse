from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceName = Literal[
    "arxiv", "openreview", "openalex", "crossref", "europepmc", "pubmed",
    "semantic_scholar", "hackernews", "doaj", "biorxiv", "hal", "zenodo",
]


class Paper(BaseModel):
    """统一的论文/热点条目：所有数据源都归一到这个结构。"""

    uid: str
    source: SourceName
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    published: date | None = None
    url: str = ""
    venue: str = ""
    categories: list[str] = Field(default_factory=list)
    citation_count: int = 0
    heat: float = 0.0
    score: float = 0.0
    score_detail: dict[str, float] = Field(default_factory=dict)


class Plan(BaseModel):
    """Planner 的产出：检索意图的结构化视图。"""

    topic: str
    subthemes: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    notes: str = ""


class Analysis(BaseModel):
    """单篇论文的结构化精读结果。"""

    uid: str
    problem: str = ""
    method: str = ""
    contribution: str = ""
    limitations: str = ""
    reusable: str = ""
    keywords: list[str] = Field(default_factory=list)
    relevance: float = 0.0


class Theme(BaseModel):
    name: str
    summary: str
    uids: list[str] = Field(default_factory=list)
    trend: str = ""


class Synthesis(BaseModel):
    themes: list[Theme] = Field(default_factory=list)
    fields: list[Theme] = Field(default_factory=list)
    trends: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    must_read: list[str] = Field(default_factory=list)


class Critique(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list)
    score: float = 0.0
    mode: str = "rule"
    dimensions: dict[str, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)


class RunMeta(BaseModel):
    topic: str
    days: int
    sources: list[str]
    fetched: int
    kept: int
    llm_used: bool
    offline: bool = False
    field: str = ""
    queries: list[str] = Field(default_factory=list)
    generated_at: datetime
    window_start: date
    window_end: date
