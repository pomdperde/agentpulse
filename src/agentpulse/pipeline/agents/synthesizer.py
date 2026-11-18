from __future__ import annotations

import json
import re
from collections import Counter

from agentpulse.pipeline.agents.base import Agent
from agentpulse.core.models import Analysis, Paper, Synthesis, Theme
from agentpulse.core.taxonomy import classify_field

_SYSTEM = """你是综述归纳专家。基于给定的论文结构化分析，产出主题聚类与趋势判断。
要求：
1. themes 给 3-6 个，每个主题的 uids 只能从输入里选取，不得新增；
2. 每篇论文尽量归入且只归入一个主题，覆盖尽量完整；
3. summary 用中文 2-3 句，只陈述输入中有依据的内容，不得编造结论或数字；
4. must_read 选 3-5 篇最有代表性的 uid；
5. 只输出 JSON：{"themes":[{"name":"...","summary":"...","uids":["..."],"trend":"..."}],"trends":["..."],"gaps":["..."],"must_read":["..."]}"""


def _compact(papers: list[Paper], analyses: list[Analysis], limit: int = 60) -> list[dict[str, str]]:
    by_uid = {a.uid: a for a in analyses}
    rows = []
    for paper in papers[:limit]:
        analysis = by_uid.get(paper.uid)
        rows.append(
            {
                "uid": paper.uid,
                "title": paper.title,
                "keywords": ", ".join(analysis.keywords[:5]) if analysis else "",
                "problem": (analysis.problem[:200] if analysis else ""),
                "contribution": (analysis.contribution[:200] if analysis else ""),
            }
        )
    return rows


# 预置主题词典：规则模式按词命中归属主题，避免用高频词硬拼主题名。
_THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("多智能体协作", ("multi-agent", "multiagent", "multi agent", "collaboration",
                 "coordination", "orchestration", "autogen", "metagpt", "agentverse", "camel")),
    ("工具调用", ("tool use", "tool-use", "toolformer", "function calling", "api call",
               "tool learning", "toolllm", "gorilla", "mcp")),
    ("记忆与上下文", ("memory", "memgpt", "context window", "long-term", "long context",
                 "memory stream", "forgetting")),
    ("规划与推理", ("planning", "reasoning", "react", "reflection", "reflexion",
                "task decomposition", "chain-of-thought", "self-correct", "self-refine")),
    ("评测与基准", ("benchmark", "evaluation", "evaluator", "judge", "agentbench",
                "webarena", "osworld", "metric", "testbed")),
    ("安全与对齐", ("safety", "security", "prompt injection", "attack", "jailbreak",
                "alignment", "privacy", "adversarial")),
    ("代码与软件工程", ("coding agent", "software engineering", "swe-bench", "swe-agent",
                  "code generation", "program repair", "programming", "repository")),
    ("网页与 GUI", ("web agent", "browser", "gui", "computer use", "desktop",
                   "web navigation", "screen")),
    ("检索增强", ("retrieval-augmented", "retrieval augmented", "rag", "retriever",
              "knowledge base", "search agent", "grounding")),
    ("训练与优化", ("fine-tuning", "finetune", "reinforcement learning", "rlhf",
                "post-training", "distillation", "instruction tuning")),
    ("具身智能", ("embodied", "robot", "robotics", "minecraft", "navigation", "manipulation")),
    ("垂直应用", ("medical", "clinical", "finance", "legal", "drug", "chip",
               "scientific", "education", "customer service")),
]

_GENERIC_WORDS = {
    "agent", "agents", "llm", "llms", "model", "models", "method", "methods",
    "approach", "approaches", "framework", "frameworks", "system", "systems",
    "paper", "result", "results", "experiment", "experiments", "show", "shows",
    "propose", "proposed", "new", "task", "tasks", "learning", "language",
    "large", "based", "using", "via", "data", "performance", "study", "analysis",
    "work", "existing", "ability", "abilities", "improve", "improves",
    "multi", "self", "long", "web", "use", "uses", "end", "one", "two",
    "across", "toward", "towards", "level", "case", "cases", "high", "low",
}


def _keyword_ok(word: str) -> bool:
    cleaned = word.strip().lower()
    if len(cleaned) < 3 or cleaned.isdigit():
        return False
    if not any(ch.isalpha() for ch in cleaned):
        return False
    return cleaned not in _GENERIC_WORDS


def _count_phrase(text: str, phrase: str) -> int:
    """短词用词边界匹配，避免 react 命中 interact、rag 命中 storage。"""
    if " " in phrase or "-" in phrase:
        return text.count(phrase)
    return len(re.findall(rf"\b{re.escape(phrase)}\b", text))


def _assign_theme(paper: Paper, analysis: Analysis | None) -> str:
    title = (paper.title or "").lower()
    body = " ".join(
        [
            title,
            (paper.abstract or "").lower(),
            " ".join(k.lower() for k in (analysis.keywords if analysis else [])),
        ]
    )
    best_name, best_score = "", 0
    for name, phrases in _THEME_RULES:
        score = sum(_count_phrase(title, p) * 3 + _count_phrase(body, p) for p in phrases)
        if score > best_score:
            best_name, best_score = name, score
    return best_name or "其他议题"


def _field_groups(papers: list[Paper]) -> list[Theme]:
    """按 Google Scholar 式学科门类聚合，供报告展示与检索过滤。"""
    grouped: dict[str, list[str]] = {}
    for paper in papers:
        grouped.setdefault(classify_field(paper), []).append(paper.uid)
    ordered = sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    return [
        Theme(name=name, summary=f"该门类覆盖 {len(uids)} 篇论文。", uids=uids)
        for name, uids in ordered
    ]


def heuristic_synthesis(papers: list[Paper], analyses: list[Analysis]) -> Synthesis:
    """不依赖模型的主题聚类：预置词典归类 + 关键词清洗，宁少不编。"""
    by_uid = {a.uid: a for a in analyses}
    grouped: dict[str, list[str]] = {}
    for paper in papers:
        name = _assign_theme(paper, by_uid.get(paper.uid))
        grouped.setdefault(name, []).append(paper.uid)

    ordered = sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    merged: list[tuple[str, list[str]]] = ordered[:5]
    if len(ordered) > 5:
        rest = [uid for _, uids in ordered[5:] for uid in uids]
        merged.append(("其他议题", rest))

    themes: list[Theme] = []
    for name, uids in merged:
        freq = Counter(
            k for uid in uids if uid in by_uid for k in by_uid[uid].keywords[:6]
            if _keyword_ok(k)
        )
        top = "、".join(word for word, _ in freq.most_common(4))
        themes.append(
            Theme(
                name=name,
                summary=f"该主题覆盖 {len(uids)} 篇论文，高频关键词：{top or '无'}。",
                uids=uids,
            )
        )

    year_count = Counter(p.published.year for p in papers if p.published)
    trends = [f"语料时间分布：{', '.join(f'{y}年 {c} 篇' for y, c in sorted(year_count.items()))}"]
    if themes and papers:
        top_theme = themes[0]
        share = len(top_theme.uids) / len(papers) * 100
        trends.append(f"最集中的方向是「{top_theme.name}」（{len(top_theme.uids)} 篇，占 {share:.0f}%）。")
    return Synthesis(
        themes=themes,
        fields=_field_groups(papers),
        trends=trends,
        gaps=[],
        must_read=[p.uid for p in papers[:5]],
    )


class SynthesizerAgent(Agent):
    name = "synthesizer"

    def _validate(self, data: dict, valid_uids: set[str]) -> Synthesis:
        themes: list[Theme] = []
        for item in data.get("themes", []) or []:
            uids = [str(u) for u in item.get("uids", []) if str(u) in valid_uids]
            if not uids:
                continue
            themes.append(
                Theme(
                    name=str(item.get("name", "")).strip() or "未命名主题",
                    summary=str(item.get("summary", "")),
                    uids=uids,
                    trend=str(item.get("trend", "")),
                )
            )
        return Synthesis(
            themes=themes,
            trends=[str(t) for t in data.get("trends", [])][:6],
            gaps=[str(g) for g in data.get("gaps", [])][:6],
            must_read=[str(u) for u in data.get("must_read", []) if str(u) in valid_uids][:5],
        )

    async def run(self, papers: list[Paper], analyses: list[Analysis]) -> Synthesis:
        valid = {p.uid for p in papers}
        if self.llm.enabled:
            user = json.dumps(_compact(papers, analyses), ensure_ascii=False)
            try:
                data = await self.llm.json(_SYSTEM, user.replace("```", "'''"), temperature=0.2)
                synthesis = self._validate(data, valid)
                if len(synthesis.themes) >= 2:
                    synthesis.fields = _field_groups(papers)
                    self.log(mode="llm", themes=len(synthesis.themes))
                    return synthesis
                self.log(mode="fallback", reason="模型返回的主题不足")
            except Exception as exc:
                self.log(mode="fallback", error=f"{type(exc).__name__}: {exc}")

        synthesis = heuristic_synthesis(papers, analyses)
        self.log(mode="heuristic", themes=len(synthesis.themes))
        return synthesis
