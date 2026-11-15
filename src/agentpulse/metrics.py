"""期刊/会议影响力评分。

影响因子与分区本身是商业数据（JCR / 中科院分区），不能直接抓取。这里采用两条合法路径：

1. OpenAlex `sources.summary_stats.2yr_mean_citedness`：公开、可商用的「两年平均被引」
   指标，作为影响因子代理；分区按该值的分档阈值或同批次分位推算。
2. 本地覆盖文件 `data/journal_metrics.json`：用户可填入自己有权使用的 IF 与分区，
   优先级高于 OpenAlex，格式见 README 注释或下方示例。
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path

from agentpulse.http import HttpClient
from agentpulse.venues import pick_source

ROOT = Path(__file__).resolve().parents[2]
OVERRIDE_PATH = ROOT / "data" / "journal_metrics.json"
SOURCE_API = "https://api.openalex.org/sources"

# 代理值的绝对分档阈值：≥10 Q1，≥5 Q2，≥1.5 Q3，其余 Q4
_IF_THRESHOLDS = ((10.0, "Q1"), (5.0, "Q2"), (1.5, "Q3"))
_QUARTILE_SCORE = {"Q1": 1.0, "Q2": 0.75, "Q3": 0.5, "Q4": 0.25}

# data/journal_metrics.json 示例：
# {
#   "Nature": {"impact_factor": 64.8, "quartile": "Q1"},
#   "管理世界": {"impact_factor": 8.2, "quartile": "Q1"}
# }


@dataclass
class VenueMetric:
    name: str
    impact_factor: float = 0.0
    quartile: str = ""
    works: int = 0
    origin: str = "openalex"
    quality: float | None = None


def load_overrides(path: Path | None = None) -> dict[str, dict]:
    """读取本地覆盖的 IF / 分区，键名不区分大小写。"""
    target = path or OVERRIDE_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(name).strip().lower(): item
        for name, item in raw.items()
        if isinstance(item, dict)
    }


def _match_override(venue: str, overrides: dict[str, dict]) -> dict | None:
    text = (venue or "").lower()
    best: tuple[int, dict] | None = None
    for name, item in overrides.items():
        if name and name in text:
            if best is None or len(name) > best[0]:
                best = (len(name), item)
    return best[1] if best else None


def _quartile_from_if(value: float) -> str:
    if value <= 0:
        return ""
    for threshold, quartile in _IF_THRESHOLDS:
        if value >= threshold:
            return quartile
    return "Q4"


def apply_quartiles(metrics: list[VenueMetric]) -> None:
    """同批次 >=6 个来源时按分位划分，否则用绝对阈值。"""
    ranked = sorted(metrics, key=lambda m: m.impact_factor, reverse=True)
    known = [m for m in ranked if m.impact_factor > 0 and not m.quartile]
    if len(known) >= 6:
        size = len(known)
        for index, metric in enumerate(known):
            percentile = index / size
            metric.quartile = (
                "Q1" if percentile < 0.25 else
                "Q2" if percentile < 0.5 else
                "Q3" if percentile < 0.75 else "Q4"
            )
    for metric in known:
        if not metric.quartile:
            metric.quartile = _quartile_from_if(metric.impact_factor)


def _quality(metric: VenueMetric) -> float | None:
    """没有影响因子也没有分区时返回 None，该论文不参与影响力打分。"""
    if metric.impact_factor <= 0 and not metric.quartile:
        return None
    normalized = min(1.0, math.log1p(metric.impact_factor) / math.log1p(40))
    quartile = _QUARTILE_SCORE.get(metric.quartile, 0.0)
    return round(0.6 * normalized + 0.4 * quartile, 4)


async def fetch_venue_metrics(
    http: HttpClient,
    venues: list[str],
    mailto: str = "",
    overrides: dict[str, dict] | None = None,
) -> dict[str, VenueMetric]:
    """并发查询每个来源的两年平均被引，返回以压低后的来源名为键的指标表。"""
    overrides = overrides or {}
    unique = list(dict.fromkeys(v.strip() for v in venues if v and v.strip()))
    unique = unique[:20]
    metrics: dict[str, VenueMetric] = {}

    for venue in unique:
        override = _match_override(venue, overrides)
        if override:
            metric = VenueMetric(
                name=venue,
                impact_factor=float(override.get("impact_factor") or 0.0),
                quartile=str(override.get("quartile") or ""),
                origin="local",
            )
            metrics[venue.lower()] = metric

    async def _lookup(venue: str) -> None:
        params: dict[str, str | int] = {
            "search": venue,
            "per-page": 3,
            "select": "id,display_name,type,works_count,summary_stats",
        }
        if mailto:
            params["mailto"] = mailto
        try:
            data = await http.get_json(SOURCE_API, params=params)
        except Exception:
            return
        picked = pick_source(data.get("results") or [], venue)
        if not picked:
            return
        stats = picked.get("summary_stats") or {}
        metrics[venue.lower()] = VenueMetric(
            name=str(picked.get("display_name") or venue),
            impact_factor=float(stats.get("2yr_mean_citedness") or 0.0),
            works=int(picked.get("works_count") or 0),
            origin="openalex",
        )

    pending = [v for v in unique if v.lower() not in metrics]
    if pending:
        await asyncio.gather(*(_lookup(venue) for venue in pending))

    values = list(metrics.values())
    apply_quartiles(values)
    for metric in values:
        metric.quality = _quality(metric)
    return metrics


def quality_for(venue: str, metrics: dict[str, VenueMetric]) -> float | None:
    """按来源名做包含匹配，返回该论文所在期刊/会议的质量分；未知时返回 None。"""
    text = (venue or "").lower()
    if not text or not metrics:
        return None
    best: tuple[int, VenueMetric] | None = None
    for key, metric in metrics.items():
        if key and (key in text or text in key):
            if best is None or len(key) > best[0]:
                best = (len(key), metric)
    return best[1].quality if best else None
