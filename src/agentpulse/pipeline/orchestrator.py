from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from agentpulse.pipeline.agents import AnalystAgent, CuratorAgent, PlannerAgent, SynthesizerAgent
from agentpulse.pipeline.agents.critic import CriticAgent
from agentpulse.core.config import Settings
from agentpulse.pipeline.html_report import render_html
from agentpulse.infra.http import HttpClient
from agentpulse.infra.llm import BudgetExceeded, LLMClient
from agentpulse.infra.metrics import fetch_venue_metrics, load_overrides
from agentpulse.core.models import Paper, Plan, RunMeta
from agentpulse.infra.providers.offline import load_fixtures
from agentpulse.pipeline.render import render_report
from agentpulse.infra.sources import fetch_all, is_ieee, search_openalex_venue
from agentpulse.core.taxonomy import paper_fields, paper_matches_subfield
from agentpulse.infra.trace import Trace
from agentpulse.core.venues import venue_matches

ALL_SOURCES = (
    "arxiv",
    "openreview",
    "openalex",
    "crossref",
    "europepmc",
    "pubmed",
    "semantic_scholar",
    "hackernews",
    "doaj",
    "biorxiv",
    "hal",
    "zenodo",
)


def _slug(topic: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", topic).strip("-")
    return (cleaned or "run")[:30]


class Pipeline:
    """编排器：持有唯一真态，各 Agent 无状态，按阶段推进并落盘。

    listener 把阶段事件实时推给 Web 面板；offline 时用内置语料跳过网络。
    """

    def __init__(
        self,
        settings: Settings,
        out_root: Path,
        no_llm: bool = False,
        offline: bool = False,
        listener: Callable[[dict], None] | None = None,
    ):
        self.settings = settings
        self.out_root = out_root
        self.no_llm = no_llm
        self.offline = offline
        self.listener = listener
        self.effective = settings.model_copy(update={"chat_api_key": ""}) if no_llm else settings

    def _stage(self, trace: Trace, agent: str, status: str, message: str, **extra: object) -> None:
        trace.event("stage", agent=agent, status=status, message=message, **extra)

    @staticmethod
    async def _write(path: Path, text: str) -> None:
        """大文件落盘放线程池，避免阻塞事件循环（SSE 推送会因此卡顿）。"""
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    async def run(
        self,
        topic: str | None = None,
        days: int = 14,
        sources: list[str] | None = None,
        max_papers: int = 20,
        from_json: Path | None = None,
        field: str | None = None,
        subfield: str | None = None,
        subfield3: str | None = None,
        ieee_only: bool = False,
        venue: str | None = None,
        topics: list[str] | None = None,
    ) -> Path:
        cleaned = [item.strip() for item in (topics or [topic or ""]) if item and item.strip()]
        cleaned = list(dict.fromkeys(cleaned))[:5]
        if not cleaned:
            raise ValueError("至少需要一个研究主题")
        label = " / ".join(cleaned)
        sources = sources or list(ALL_SOURCES)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.out_root / f"{stamp}-{_slug(label)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        trace = Trace(run_dir / "events.jsonl", listener=self.listener)

        try:
            async with HttpClient(
                self.settings.request_timeout,
                self.settings.http_retries,
                max_connections=max(8, self.settings.max_concurrency * 4),
            ) as http, LLMClient(self.effective) as llm:
                planner = PlannerAgent(llm, self.effective, trace)
                curator = CuratorAgent(llm, self.effective, trace)
                analyst = AnalystAgent(llm, self.effective, trace)
                synthesizer = SynthesizerAgent(llm, self.effective, trace)
                critic = CriticAgent(llm, self.effective, trace)

                trace.event(
                    "run.start", topic=label, topics=cleaned,
                    days=days, sources=sources, offline=self.offline,
                )

                # 离线语料与本地 JSON 与主题无关，只跑一次避免重复
                scheduled = cleaned[:1] if (self.offline or from_json is not None) else cleaned
                kept: list[Paper] = []
                fetched_total = 0
                plans: list[Plan] = []
                fetch_errors: list[str] = []

                for index, current in enumerate(scheduled, 1):
                    remaining = max_papers - len(kept)
                    if remaining <= 0:
                        trace.event("topic.skip", topic=current, reason="已满足精选篇数")
                        break

                    prefix = f"[{index}/{len(scheduled)}] " if len(scheduled) > 1 else ""
                    self._stage(
                        trace, "planner", "running",
                        f"{prefix}正在把主题「{current}」拆成子主题与检索式",
                    )
                    plan = await planner.run(current, days)
                    plans.append(plan)
                    self._stage(
                        trace, "planner", "done",
                        f"{prefix}生成 {len(plan.queries)} 条检索式", queries=plan.queries,
                    )

                    papers, topic_errors = await self._gather(
                        http, plan, current, days, sources, from_json, venue, trace, prefix,
                    )
                    fetched_total += len(papers)
                    fetch_errors.extend(topic_errors)
                    papers = await self._filter(
                        http, papers, current, plan, days, field, subfield, subfield3,
                        ieee_only, venue, from_json, trace, prefix,
                    )

                    if not papers:
                        trace.event("topic.empty", topic=current)
                        self._stage(trace, "search", "done", f"{prefix}主题「{current}」无匹配论文，继续下一个")
                        continue

                    metrics = {}
                    if not self.offline:
                        try:
                            metrics = await fetch_venue_metrics(
                                http,
                                [p.venue for p in papers],
                                self.settings.contact_email,
                                load_overrides(),
                                concurrency=self.settings.max_concurrency,
                            )
                            trace.event("metrics.venue", count=len(metrics))
                        except Exception as exc:
                            trace.event(
                                "metrics.error", error=f"{type(exc).__name__}: {exc}"[:160]
                            )

                    note = f"（已接入 {len(metrics)} 个期刊/会议的影响力评分）" if metrics else ""
                    self._stage(
                        trace, "curator", "running",
                        f"{prefix}去重、打分、截断{note}",
                    )
                    part = curator.run(papers, plan, remaining, metrics=metrics)
                    kept.extend(part)
                    trace.event(
                        "topic.done", index=index, topic=current,
                        fetched=len(papers), kept=len(part), total=len(kept),
                    )
                    self._stage(
                        trace, "curator", "done",
                        f"{prefix}主题「{current}」精选 {len(part)} 篇，累计 {len(kept)}/{max_papers}",
                    )

                if not kept:
                    scope = f"期刊/会议「{venue}」" if venue else "当前筛选条件"
                    hint = "，可放宽回溯窗口或更换数据源" if not venue or days >= 365 else \
                        "；会议论文集中在年度会期，建议把回溯窗口调到 1 年以上"
                    raise RuntimeError(f"没有匹配到符合{scope}的论文{hint}")

                plan = Plan(
                    topic=label,
                    subthemes=list(dict.fromkeys(s for p in plans for s in p.subthemes))[:12],
                    queries=list(dict.fromkeys(q for p in plans for q in p.queries))[:12],
                    notes="；".join(p.notes for p in plans if p.notes)[:400],
                )
                await self._write(run_dir / "plan.json", plan.model_dump_json(indent=2))
                await self._write(
                    run_dir / "candidates.json",
                    json.dumps(
                        [p.model_dump(mode="json") for p in kept],
                        ensure_ascii=False,
                        indent=2,
                    ),
                )

                self._stage(trace, "analyst", "running", "逐篇抽取问题、方法、贡献")
                analyses = await analyst.run(kept)
                await self._write(
                    run_dir / "analyses.json",
                    json.dumps(
                        [a.model_dump(mode="json") for a in analyses],
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                self._stage(trace, "analyst", "done", f"完成 {len(analyses)} 篇精读")

                self._stage(trace, "synthesizer", "running", "聚类主题、归纳趋势")
                synthesis = await synthesizer.run(kept, analyses)
                self._stage(
                    trace, "synthesizer", "done", f"归纳 {len(synthesis.themes)} 个主题"
                )

                self._stage(trace, "critic", "running", "校验引用与覆盖率，评估报告质量")
                critique = await critic.run(kept, analyses, synthesis, topic=label)
                if not critique.ok and llm.enabled:
                    trace.event("critic.retry")
                    synthesis = await synthesizer.run(kept, analyses)
                    critique = await critic.run(kept, analyses, synthesis, topic=label)
                self._stage(
                    trace, "critic", "done",
                    f"{'通过' if critique.ok else '未通过'} · {critique.score:.0f} 分"
                    f"（{'模型评分' if critique.mode == 'llm' else '规则评分'}）",
                    ok=critique.ok, score=critique.score, mode=critique.mode,
                )

                await self._write(
                    run_dir / "synthesis.json", synthesis.model_dump_json(indent=2)
                )
                await self._write(
                    run_dir / "critique.json", critique.model_dump_json(indent=2)
                )

                window_end = date.today()
                meta = RunMeta(
                    topic=label,
                    days=days,
                    sources=sources,
                    fetched=fetched_total,
                    kept=len(kept),
                    llm_used=llm.calls > 0,
                    offline=self.offline,
                    field=field or "",
                    queries=plan.queries,
                    generated_at=datetime.now(),
                    window_start=window_end - timedelta(days=days),
                    window_end=window_end,
                )
                report_md = await asyncio.to_thread(
                    render_report, meta, plan, kept, analyses, synthesis, critique, fetch_errors
                )
                await self._write(run_dir / "report.md", report_md)
                report_html = await asyncio.to_thread(
                    render_html, meta, plan, kept, analyses, synthesis, critique, fetch_errors
                )
                await self._write(run_dir / "report.html", report_html)
                self._stage(trace, "writer", "done", "Markdown 与 HTML 报告已落盘")

                trace.event(
                    "run.done",
                    kept=len(kept),
                    tokens=llm.tokens_used,
                    llm_calls=llm.calls,
                    critique_ok=critique.ok,
                )
                return run_dir
        except BudgetExceeded as exc:
            trace.event("run.budget_exceeded", error=str(exc))
            raise
        except Exception as exc:
            trace.event("run.error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            trace.close()

    async def _gather(
        self,
        http: HttpClient,
        plan: Plan,
        topic: str,
        days: int,
        sources: list[str],
        from_json: Path | None,
        venue: str | None,
        trace: Trace,
        prefix: str,
    ) -> tuple[list[Paper], list[str]]:
        if from_json is not None:
            raw = json.loads(from_json.read_text(encoding="utf-8"))
            papers = [Paper.model_validate(item) for item in raw]
            trace.event("fetch.from_json", path=str(from_json), count=len(papers))
            self._stage(trace, "search", "done", f"{prefix}从本地 JSON 载入 {len(papers)} 篇")
            return papers, []

        if self.offline:
            papers = load_fixtures()
            trace.event("fetch.offline", count=len(papers))
            self._stage(trace, "search", "done", f"{prefix}离线语料 {len(papers)} 篇")
            return papers, ["离线模式：使用内置演示语料，未访问网络"]

        jobs = self._build_jobs(plan.queries, topic, sources, venue)
        self._stage(trace, "search", "running", f"{prefix}并发检索 {len(jobs)} 个查询")
        papers, errors = await fetch_all(http, self.settings, jobs, days)
        trace.event("fetch.done", topic=topic, count=len(papers), errors=len(errors))
        self._stage(
            trace, "search", "done",
            f"{prefix}召回 {len(papers)} 篇", degraded=len(errors),
        )
        return papers, errors

    async def _filter(
        self,
        http: HttpClient,
        papers: list[Paper],
        topic: str,
        plan: Plan,
        days: int,
        field: str | None,
        subfield: str | None,
        subfield3: str | None,
        ieee_only: bool,
        venue: str | None,
        from_json: Path | None,
        trace: Trace,
        prefix: str,
    ) -> list[Paper]:
        if venue and from_json is None and not self.offline:
            self._stage(trace, "search", "running", f"{prefix}直接抓取「{venue}」的会议/期刊库")
            try:
                extra, resolved = await search_openalex_venue(
                    http,
                    plan.queries[0] if plan.queries else topic,
                    venue,
                    self.settings.per_source_limit,
                    date.today() - timedelta(days=days),
                    self.settings.contact_email,
                )
            except Exception as exc:
                extra, resolved = [], ""
                trace.event(
                    "fetch.venue.error", venue=venue,
                    error=f"{type(exc).__name__}: {exc}"[:160],
                )
            if extra:
                papers.extend(extra)
                trace.event("fetch.venue", venue=venue, source=resolved, count=len(extra))
                self._stage(
                    trace, "search", "done",
                    f"{prefix}「{resolved}」来源库补充 {len(extra)} 篇",
                )
            elif resolved:
                trace.event("fetch.venue", venue=venue, source=resolved, count=0)

        if field:
            before = len(papers)
            papers = [p for p in papers if field in paper_fields(p)]
            trace.event(
                "filter.field", field=field, kept=len(papers), dropped=before - len(papers)
            )
            self._stage(
                trace, "search", "done",
                f"{prefix}学科门类「{field}」过滤后保留 {len(papers)} 篇",
            )

        if field and subfield:
            before = len(papers)
            papers = [p for p in papers if paper_matches_subfield(p, field, subfield, subfield3)]
            scope = subfield if not subfield3 else f"{subfield} / {subfield3}"
            trace.event(
                "filter.subfield", field=field, subfield=subfield,
                subfield3=subfield3, kept=len(papers), dropped=before - len(papers),
            )
            self._stage(
                trace, "search", "done",
                f"{prefix}学科范围「{field} · {scope}」过滤后保留 {len(papers)} 篇",
            )

        if ieee_only:
            before = len(papers)
            papers = [p for p in papers if is_ieee(p)]
            trace.event("filter.ieee", kept=len(papers), dropped=before - len(papers))
            self._stage(
                trace, "search", "done",
                f"{prefix}IEEE 出版物（10.1109）过滤后保留 {len(papers)} 篇",
            )

        if venue:
            before = len(papers)
            papers = [p for p in papers if venue_matches(p.venue, venue)]
            trace.event(
                "filter.venue", venue=venue, kept=len(papers), dropped=before - len(papers)
            )
            if papers:
                message = f"{prefix}期刊/会议「{venue}」过滤后保留 {len(papers)} 篇"
            elif days < 365:
                message = (
                    f"{prefix}未检索到「{venue}」的论文：会议论文集中在年度会期，"
                    "建议把回溯窗口调到 1 年以上"
                )
            else:
                message = (
                    f"{prefix}未检索到「{venue}」的相关论文，可放宽主题或改用 Crossref / OpenAlex"
                )
            self._stage(trace, "search", "done", message)

        return papers

    def _build_jobs(
        self, queries: list[str], topic: str, sources: list[str], venue: str | None = None
    ) -> list[tuple[str, str]]:
        """每个源取少量查询，避免一个源把额度吃光；HN 只查主题词。"""
        jobs: list[tuple[str, str]] = []
        primary = queries[:2] or [topic]
        for source in sources:
            if source not in ALL_SOURCES:
                continue
            if source == "hackernews":
                jobs.append(("hackernews", topic))
            else:
                jobs.extend((source, query) for query in primary)

        # 指定期刊/会议时，向支持自由文本检索的源追加一条组合查询，提高召回
        if venue:
            combined = f"{topic} {venue}".strip()
            for source in ("crossref", "openalex", "doaj", "europepmc", "pubmed", "hal", "zenodo"):
                if source in sources and (source, combined) not in jobs:
                    jobs.append((source, combined))
        return jobs
