from __future__ import annotations

import asyncio
import json
import re
from collections import Counter

from agentpulse.agents.base import Agent
from agentpulse.models import Analysis, Paper
from agentpulse.scoring import tokenize

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_METHOD_HINTS = ("we propose", "we present", "we introduce", "we design", "we develop", "our approach")
_RESULT_HINTS = ("outperform", "improve", "achiev", "results show", "experiments")
_LIMIT_HINTS = ("limitation", "future work", "however", "remains")

_SYSTEM = """你是论文精读分析员。逐篇阅读标题与摘要，抽取结构化信息。
要求：
1. 只依据给定文本，不得引入外部知识，不得编造数字；
2. limitations 若摘要未提及，填空字符串；
3. keywords 给 3-5 个英文短语；
4. 只输出 JSON：{"items":[{"uid":"...","problem":"...","method":"...","contribution":"...","limitations":"...","reusable":"...","keywords":["..."],"relevance":0.0}]}
relevance 取值 0-1，表示与检索主题的相关程度。"""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text or "") if s.strip()]


def heuristic_analysis(paper: Paper) -> Analysis:
    """零成本兜底：按句式模板从摘要里抽字段，抽不到就留空。"""
    sents = _sentences(paper.abstract)
    problem = sents[0] if sents else ""
    method = next((s for s in sents if any(h in s.lower() for h in _METHOD_HINTS)), "")
    contribution = next((s for s in sents if any(h in s.lower() for h in _RESULT_HINTS)), "")
    limitations = next((s for s in sents if any(h in s.lower() for h in _LIMIT_HINTS)), "")
    freq = Counter(tokenize(paper.title + " " + paper.abstract))
    keywords = [word for word, _ in freq.most_common(5)]
    return Analysis(
        uid=paper.uid,
        problem=problem,
        method=method,
        contribution=contribution,
        limitations=limitations,
        reusable="",
        keywords=keywords,
        relevance=paper.score,
    )


class AnalystAgent(Agent):
    name = "analyst"

    def __init__(self, *args: object, batch_size: int = 6, **kwargs: object):
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.batch_size = batch_size
        self._sem = asyncio.Semaphore(self.settings.max_concurrency)

    async def _analyze_batch(self, batch: list[Paper]) -> dict[str, Analysis]:
        payload = [
            {"uid": p.uid, "title": p.title, "abstract": p.abstract[:1200] or "(无摘要)"}
            for p in batch
        ]
        user = "请分析以下论文：\n" + json.dumps(payload, ensure_ascii=False)
        async with self._sem:
            data = await self.llm.json(_SYSTEM, user.replace("```", "'''"), temperature=0.1)
        items = data.get("items", []) if isinstance(data, dict) else []
        result: dict[str, Analysis] = {}
        for item in items:
            uid = str(item.get("uid", ""))
            if not uid:
                continue
            result[uid] = Analysis(
                uid=uid,
                problem=str(item.get("problem", "")),
                method=str(item.get("method", "")),
                contribution=str(item.get("contribution", "")),
                limitations=str(item.get("limitations", "")),
                reusable=str(item.get("reusable", "")),
                keywords=[str(k) for k in item.get("keywords", [])][:6],
                relevance=float(item.get("relevance") or 0.0),
            )
        return result

    async def run(self, papers: list[Paper]) -> list[Analysis]:
        analyses: dict[str, Analysis] = {}
        if self.llm.enabled:
            # 只有标题的条目（如 HN 热帖）不给模型，避免无依据编造
            llm_papers = [p for p in papers if p.abstract.strip()]
            batches = [
                llm_papers[i : i + self.batch_size]
                for i in range(0, len(llm_papers), self.batch_size)
            ]
            tasks = [self._analyze_batch(batch) for batch in batches]
            for batch, outcome in zip(batches, await asyncio.gather(*tasks, return_exceptions=True)):
                if isinstance(outcome, Exception):
                    self.log(error=f"{type(outcome).__name__}: {outcome}")
                    continue
                analyses.update(outcome)
                self.log(batch=len(batch), ok=len(outcome))

        # 模型漏掉或整体降级时，用规则补齐每一条，保证报告不缺块
        for paper in papers:
            analyses.setdefault(paper.uid, heuristic_analysis(paper))
        self.log(total=len(analyses), mode="llm" if self.llm.enabled else "heuristic")
        return [analyses[p.uid] for p in papers]
