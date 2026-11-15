from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpulse.config import Settings
from agentpulse.llm import LLMClient

_ASCII = re.compile(r"[a-z0-9][a-z0-9.\-+#]*")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")

MAX_PASSAGES = 6
MAX_PASSAGE_CHARS = 700
MAX_ANSWER_CHARS = 1200
LLM_TIMEOUT = 60

# 中文提问、英文论文很常见；用小词表做跨语言查询扩展，避免中文问句只能命中中文主题句。
_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "方法": ("method", "approach"),
    "模型": ("model", "models"),
    "框架": ("framework", "architecture"),
    "实验": ("experiment", "results"),
    "评测": ("evaluation", "benchmark"),
    "基准": ("benchmark",),
    "数据集": ("dataset", "data"),
    "贡献": ("contribution",),
    "创新": ("novel",),
    "局限": ("limitation",),
    "问题": ("problem", "challenge"),
    "挑战": ("challenge",),
    "差异": ("difference", "compare"),
    "比较": ("compare", "comparison"),
    "趋势": ("trend",),
    "训练": ("training", "train"),
    "推理": ("reasoning", "inference"),
    "智能体": ("agent", "agents"),
    "多智能体": ("multi-agent", "multiagent"),
    "检索": ("retrieval", "retrieve"),
    "生成": ("generation", "generate"),
    "综述": ("survey", "review"),
    "性能": ("performance",),
    "应用": ("application",),
    "安全": ("safety",),
    "对齐": ("alignment",),
    "提示": ("prompt", "prompting"),
    "微调": ("fine-tuning", "tuning"),
    "任务": ("task", "tasks"),
}

_SYSTEM = """你是论文阅读助手。只能依据用户提供的资料回答问题，不要引入资料之外的事实。
如果资料不足以回答，直接说明缺少哪类信息，不要猜测。
只输出 JSON：{"answer": "中文回答，可用要点列表", "citations": ["uid"], "insufficient": false}
citations 只能使用资料中给出的 uid。"""

_EXPAND_SYSTEM = """把用户的问题转写成英文检索关键词，覆盖其中的技术概念与任务对象。
只输出 JSON：{"keywords": ["..."]}，4-8 个英文关键词或短语，不要解释。"""

# 提问里的泛化词不参与相关性判断，否则「论文 / 方法 / 差异」会命中所有片段
_GENERIC_TERMS = {
    "论文", "文章", "这些", "那些", "这个", "那个", "主要", "什么", "有啥",
    "哪些", "如何", "怎么", "怎样", "区别", "差异", "不同", "对比", "比较",
    "方法", "研究", "相关", "一个", "可以", "是否", "以及", "并且", "请问",
    "一下", "多少", "为什么", "还有", "分别",
}

# 问题意图 → 从逐篇精读结果里取哪些字段
_INTENT_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "method": (("method", "方法"),),
    "contribution": (("contribution", "贡献"),),
    "limitation": (("limitations", "局限"),),
    "evaluation": (("method", "方法"), ("contribution", "结论")),
    "general": (("contribution", "贡献"), ("method", "方法"), ("problem", "问题")),
}
_INTENT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("method", ("方法", "技术路线", "做法", "实现方式", "怎么实现")),
    ("contribution", ("贡献", "创新", "亮点", "提出了什么")),
    ("limitation", ("局限", "不足", "缺点", "短板", "风险")),
    ("evaluation", ("实验", "评测", "数据集", "效果", "性能", "准确率")),
)


@dataclass
class Passage:
    """可检索的资料片段：一篇论文的摘要或精读结论，或一个主题归纳。"""

    key: str
    title: str
    kind: str
    text: str
    uids: list[str] = field(default_factory=list)
    terms: Counter = field(default_factory=Counter)


def _tokenize(text: str) -> list[str]:
    """英文按词、中文按二元组切分，够用且不需要额外依赖。"""
    lowered = (text or "").lower()
    tokens = _ASCII.findall(lowered)
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_passages(run_dir: Path) -> list[Passage]:
    """把一轮任务的落盘结果整理成可检索片段。"""
    papers = _load_json(run_dir / "candidates.json") or []
    analyses = _load_json(run_dir / "analyses.json") or []
    synthesis = _load_json(run_dir / "synthesis.json") or {}

    by_uid = {
        str(item.get("uid")): item
        for item in analyses
        if isinstance(item, dict) and item.get("uid")
    }

    passages: list[Passage] = []
    for index, paper in enumerate(papers, 1):
        if not isinstance(paper, dict):
            continue
        uid = str(paper.get("uid") or "")
        title = str(paper.get("title") or "")
        if not uid or not title:
            continue
        abstract = str(paper.get("abstract") or "")
        venue = str(paper.get("venue") or "")
        head = f"标题：{title}｜来源：{venue or '未知'}｜发表：{paper.get('published') or '未知'}"
        body = abstract[:MAX_PASSAGE_CHARS] if abstract else "（无摘要）"
        passages.append(
            Passage(key=f"p{index}", title=title, kind="摘要", text=f"{head}\n{body}", uids=[uid])
        )

        analysis = by_uid.get(uid)
        if analysis:
            fields = [
                ("问题", analysis.get("problem")),
                ("方法", analysis.get("method")),
                ("贡献", analysis.get("contribution")),
                ("局限", analysis.get("limitations")),
                ("可复用", analysis.get("reusable")),
            ]
            detail = "\n".join(
                f"{label}：{str(value)[:220]}" for label, value in fields if value
            )
            if detail:
                passages.append(
                    Passage(
                        key=f"a{index}",
                        title=title,
                        kind="精读",
                        text=f"标题：{title}\n{detail}"[:MAX_PASSAGE_CHARS],
                        uids=[uid],
                    )
                )

    themes = (synthesis.get("themes") or []) + (synthesis.get("fields") or [])
    for index, theme in enumerate(themes, 1):
        if not isinstance(theme, dict) or not theme.get("name"):
            continue
        uids = [str(uid) for uid in theme.get("uids") or []]
        text = "\n".join(
            part
            for part in (
                f"主题：{theme.get('name')}",
                f"概述：{theme.get('summary') or ''}",
                f"趋势：{theme.get('trend') or ''}",
            )
            if part.strip()
        )
        passages.append(
            Passage(
                key=f"t{index}",
                title=str(theme.get("name")),
                kind="主题",
                text=text[:MAX_PASSAGE_CHARS],
                uids=uids,
            )
        )

    for passage in passages:
        passage.terms = Counter(_tokenize(f"{passage.title}\n{passage.text}"))
    return passages


def retrieve(
    question: str,
    passages: list[Passage],
    top_k: int = MAX_PASSAGES,
    extra_terms: list[str] | None = None,
) -> list[tuple[float, Passage]]:
    """BM25 打分 + 片段类型权重：精读结论优先于宽泛的主题归纳。"""
    query_terms = Counter(_tokenize(question))
    lowered = (question or "").lower()
    for zh, english in _QUERY_EXPANSIONS.items():
        if zh in lowered:
            for word in english:
                query_terms[word] += 0.8
    for term in extra_terms or []:
        for token in _tokenize(term):
            query_terms[token] += 0.9
    if not query_terms or not passages:
        return []

    doc_freq: Counter = Counter()
    for passage in passages:
        for term in set(passage.terms):
            doc_freq[term] += 1

    total = len(passages)
    avg_len = sum(sum(p.terms.values()) for p in passages) / total or 1.0
    k1, b = 1.2, 0.75
    bonuses = {"精读": 1.15, "摘要": 1.0, "主题": 0.8}

    scored: list[tuple[float, Passage]] = []
    for passage in passages:
        length = sum(passage.terms.values()) or 1
        score = 0.0
        for term, weight in query_terms.items():
            tf = passage.terms.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (total - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += weight * idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_len))
        if score > 0:
            scored.append((score * bonuses.get(passage.kind, 1.0), passage))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def _paper_index(run_dir: Path) -> dict[str, dict]:
    papers = _load_json(run_dir / "candidates.json") or []
    index: dict[str, dict] = {}
    for paper in papers:
        if not isinstance(paper, dict) or not paper.get("uid"):
            continue
        index[str(paper["uid"])] = {
            "uid": str(paper["uid"]),
            "title": str(paper.get("title") or ""),
            "url": str(paper.get("url") or ""),
            "venue": str(paper.get("venue") or ""),
        }
    return index


def _content_terms(text: str) -> set[str]:
    return {token for token in _tokenize(text) if token not in _GENERIC_TERMS and len(token) > 1}


def _intent(question: str) -> str:
    lowered = (question or "").lower()
    for name, words in _INTENT_WORDS:
        if any(word in lowered for word in words):
            return name
    return "general"


def _load_analyses(run_dir: Path) -> list[dict]:
    raw = _load_json(run_dir / "analyses.json") or []
    return [item for item in raw if isinstance(item, dict) and item.get("uid")]


def _pick_sentences(question: str, text: str, limit: int = 2) -> str:
    query_terms = _content_terms(question) or set(_tokenize(question))
    sentences = [s.strip() for s in _SENTENCE.findall(text) if len(s.strip()) > 12]
    if not sentences:
        return text[:160]
    ranked = sorted(
        sentences,
        key=lambda s: len(query_terms & set(_tokenize(s))) / math.sqrt(len(s) + 1),
        reverse=True,
    )
    return "；".join(ranked[:limit])[:300]


def _field_answer(
    question: str,
    ranked: list[tuple[float, Passage]],
    analyses: list[dict],
    index: dict[str, dict],
) -> tuple[str, list[str]] | None:
    """规则模式下的逐篇结构化回答：按问题意图摘取方法 / 贡献 / 局限等字段。"""
    by_uid = {str(item["uid"]): item for item in analyses}
    intent = _intent(question)
    fields = _INTENT_FIELDS[intent]

    order: list[str] = []
    for _, passage in ranked:
        for uid in passage.uids:
            if uid in by_uid and uid not in order:
                order.append(uid)
    # 泛泛而问（如「这些论文的主要方法有啥差异」）时，补齐其余论文便于横向对比
    if not _content_terms(question):
        for uid in by_uid:
            if uid not in order:
                order.append(uid)

    label = {
        "method": "逐篇方法",
        "contribution": "逐篇贡献",
        "limitation": "逐篇局限",
        "evaluation": "逐篇方法与结论",
    }.get(intent, "逐篇精读")
    lines = [f"（规则模式：按本轮{label}整理，可横向对比；语义级归纳需配置模型）"]
    used: list[str] = []
    for uid in order[:6]:
        analysis = by_uid[uid]
        parts = [
            f"{text}：{str(analysis.get(key) or '').strip()[:180]}"
            for key, text in fields
            if str(analysis.get(key) or "").strip()
        ]
        if not parts:
            continue
        title = index.get(uid, {}).get("title") or uid
        lines.append(f"- 「{title[:80]}」" + "；".join(parts))
        used.append(uid)

    if not used:
        return None
    return "\n".join(lines)[:MAX_ANSWER_CHARS], used


def _extractive(
    question: str, ranked: list[tuple[float, Passage]], index: dict[str, dict]
) -> tuple[str, list[str]]:
    if not ranked:
        return "本次检索的论文与分析中没有找到与该问题相关的内容，可以换个问法或扩大回溯窗口后重跑。", []
    # 有论文级片段时不再用宽泛的主题归纳凑数
    paper_level = [item for item in ranked if item[1].kind != "主题"]
    picked = (paper_level or ranked)[:3]
    lines = ["（规则模式：以下为资料中最相关的原文摘录）"]
    uids: list[str] = []
    for _, passage in picked:
        snippet = _pick_sentences(question, passage.text)
        lines.append(f"- 「{passage.title}」（{passage.kind}）：{snippet}")
        for uid in passage.uids:
            if uid in index and uid not in uids:
                uids.append(uid)
    return "\n".join(lines)[:MAX_ANSWER_CHARS], uids[:8]


def _prompt(question: str, ranked: list[tuple[float, Passage]]) -> str:
    blocks = []
    for index, (_, passage) in enumerate(ranked, 1):
        uids = "、".join(passage.uids) or "无"
        blocks.append(
            f"[{index}] {passage.title}（{passage.kind}，uid={uids}）\n{passage.text}"
        )
    return "资料：\n" + "\n\n".join(blocks) + f"\n\n问题：{question}"


def _collect_citations(
    uids: list[str], index: dict[str, dict], fallback: list[Passage]
) -> list[dict]:
    ordered: list[str] = []
    for uid in uids:
        if uid in index and uid not in ordered:
            ordered.append(uid)
    if not ordered:
        for passage in fallback:
            for uid in passage.uids:
                if uid in index and uid not in ordered:
                    ordered.append(uid)
    return [index[uid] for uid in ordered[:8]]


def _rule_answer(
    question: str,
    ranked: list[tuple[float, Passage]],
    analyses: list[dict],
    index: dict[str, dict],
) -> dict:
    structured = _field_answer(question, ranked, analyses, index) if analyses else None
    if structured:
        text, uids = structured
    else:
        text, uids = _extractive(question, ranked, index)
    return {
        "answer": text,
        "mode": "rule",
        "citations": _collect_citations(uids, index, []),
    }


async def _expand(client: LLMClient, question: str) -> list[str]:
    """中文提问转英文关键词，提升英文论文的召回。失败时静默跳过。"""
    try:
        data = await asyncio.wait_for(
            client.json(_EXPAND_SYSTEM, question, temperature=0), timeout=20
        )
    except Exception:
        return []
    keywords = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(keywords, list):
        return []
    return [str(word).strip() for word in keywords if str(word).strip()][:8]


async def answer(question: str, run_dir: Path, settings: Settings) -> dict:
    """先检索再作答：模型可用时生成式回答，否则返回抽取式摘录。"""
    question = (question or "").strip()
    if not question:
        return {"answer": "请输入问题。", "mode": "rule", "citations": []}

    passages = build_passages(run_dir)
    index = _paper_index(run_dir)
    analyses = _load_analyses(run_dir)
    client = LLMClient(settings) if settings.llm_enabled else None
    try:
        extra = await _expand(client, question) if client else []
        ranked = retrieve(question, passages, extra_terms=extra)
        if client is not None and not ranked and analyses:
            # 检索没命中时给模型一份按精读顺序排列的论文片段，避免空资料
            order = [str(item["uid"]) for item in analyses]
            paper_level = [p for p in passages if p.kind != "主题"]
            paper_level.sort(
                key=lambda p: order.index(p.uids[0]) if p.uids and p.uids[0] in order else len(order)
            )
            ranked = [(0.0, passage) for passage in paper_level[:MAX_PASSAGES]]

        if not ranked and not analyses:
            return {
                "answer": "本次检索的论文与分析中没有找到与该问题相关的内容，可以换个问法，或扩大回溯窗口后重跑任务。",
                "mode": "rule",
                "citations": [],
            }

        if client is None:
            return _rule_answer(question, ranked, analyses, index)

        try:
            data = await asyncio.wait_for(
                client.json(_SYSTEM, _prompt(question, ranked)), timeout=LLM_TIMEOUT
            )
        except Exception as exc:  # 模型异常时退回抽取式，不影响问答可用性
            fallback = _rule_answer(question, ranked, analyses, index)
            fallback["error"] = f"{type(exc).__name__}: {exc}"[:160]
            return fallback

        if not isinstance(data, dict):
            data = {}
        text = str(data.get("answer") or "").strip()
        if not text:
            text = _extractive(question, ranked)
        raw_uids = data.get("citations") or []
        if isinstance(raw_uids, str):
            raw_uids = [raw_uids]
        uids = [str(uid) for uid in raw_uids if isinstance(uid, (str, int))]
        return {
            "answer": text[:MAX_ANSWER_CHARS],
            "mode": "llm",
            "citations": _collect_citations(uids, index, [p for _, p in ranked]),
            "insufficient": bool(data.get("insufficient")),
        }
    finally:
        if client is not None:
            await client.close()
