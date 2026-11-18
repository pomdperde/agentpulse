"""期刊/会议别名：让 CVPR 这类缩写也能匹配到全称。

数据源返回的 venue 写法并不统一，例如 Crossref 常带 “(CVPR)” 后缀，
OpenAlex 则只有 “Proceedings of the IEEE/CVF Conference on Computer Vision
and Pattern Recognition”。这里用别名表把缩写的检索意图扩展成多个匹配串。
"""

from __future__ import annotations

import re

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

VENUE_ALIASES: dict[str, tuple[str, ...]] = {
    "cvpr": ("cvpr", "computer vision and pattern recognition"),
    "iccv": ("iccv", "international conference on computer vision"),
    "eccv": ("eccv", "european conference on computer vision"),
    "wacv": ("wacv", "winter conference on applications of computer vision"),
    "neurips": ("neurips", "nips", "neural information processing systems"),
    "icml": ("icml", "international conference on machine learning"),
    "iclr": ("iclr", "international conference on learning representations"),
    "aaai": ("aaai", "association for the advancement of artificial intelligence"),
    "ijcai": ("ijcai", "international joint conference on artificial intelligence"),
    "acl": ("acl", "association for computational linguistics"),
    "emnlp": ("emnlp", "empirical methods in natural language processing"),
    "naacl": ("naacl", "north american chapter of the association for computational linguistics"),
    "coling": ("coling", "international conference on computational linguistics"),
    "sigir": ("sigir", "research and development in information retrieval"),
    "kdd": ("kdd", "knowledge discovery and data mining"),
    "www": ("the web conference", "world wide web conference"),
    "cikm": ("cikm", "conference on information and knowledge management"),
    "icra": ("icra", "international conference on robotics and automation"),
    "iros": ("iros", "intelligent robots and systems"),
    "miccai": ("miccai", "medical image computing and computer assisted intervention"),
    "tpami": ("tpami", "pattern analysis and machine intelligence"),
    "jmlr": ("jmlr", "journal of machine learning research"),
    "tacl": ("tacl", "transactions of the association for computational linguistics"),
    "nature": ("nature",),
    "science": ("science",),
    "cell": ("cell",),
    "lancet": ("lancet",),
    "nejm": ("new england journal of medicine", "nejm"),
    "jama": ("jama",),
    "bmj": ("bmj",),
}

# 用于从 arXiv comment 里识别会议/期刊名的全部匹配串
_ALL_PATTERNS: tuple[str, ...] = tuple(
    dict.fromkeys(pattern for patterns in VENUE_ALIASES.values() for pattern in patterns)
)

# 别名 → 所属分组，让 NIPS 这类旧称也能映射到 NeurIPS 分组
_ALIAS_INDEX: dict[str, str] = {
    pattern: key for key, patterns in VENUE_ALIASES.items() for pattern in patterns
}


def _normalize(query: str) -> str:
    text = " ".join(query.strip().lower().split())
    text = YEAR_RE.sub(" ", text)  # 去掉 “CVPR 2025” 里的年份
    return " ".join(text.replace("(", " ").replace(")", " ").split())


def venue_patterns(query: str) -> tuple[str, ...]:
    """把用户输入的期刊/会议名扩展成用于匹配的字符串集合。"""
    key = _normalize(query)
    if not key:
        return ()
    if key in VENUE_ALIASES:
        return VENUE_ALIASES[key]
    if key in _ALIAS_INDEX:
        return VENUE_ALIASES[_ALIAS_INDEX[key]]
    return (key,)


def venue_matches(venue: str, query: str) -> bool:
    """venue 字段是否命中用户指定的期刊/会议（支持缩写与全称互认）。"""
    text = (venue or "").lower()
    if not text:
        return False
    return any(pattern in text for pattern in venue_patterns(query))


def venue_from_comment(comment: str) -> str:
    """arXiv 的 comment 常写 “Accepted to CVPR 2025”，命中别名时返回该注释。"""
    text = (comment or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(pattern in lowered for pattern in _ALL_PATTERNS):
        return text[:120]
    return ""


def pick_source(results: list[dict], query: str) -> dict | None:
    """从 OpenAlex sources 搜索结果里挑出最匹配的会议/期刊。

    排除 workshop 分会场，并优先名称与匹配串占比更高、论文量更大的来源。
    """
    patterns = venue_patterns(query)
    if not patterns:
        return None
    lowered = query.lower()
    best: dict | None = None
    best_score = 0.0
    candidates = [
        item for item in (results or []) if int(item.get("works_count") or 0) > 0
    ] or list(results or [])
    for item in candidates:
        name = str(item.get("display_name") or "").strip()
        if not name:
            continue
        text = name.lower()
        matched = [pattern for pattern in patterns if pattern in text]
        if not matched:
            continue
        score = max(len(pattern) for pattern in matched) / max(len(text), 1)
        kind = str(item.get("type") or "")
        if kind == "conference":
            score += 0.35
        elif kind == "book series":
            score -= 0.3
        if "workshop" in text and "workshop" not in lowered:
            score -= 0.4
        score += min(int(item.get("works_count") or 0), 200_000) / 2_000_000
        if score > best_score:
            best_score, best = score, item
    return best
