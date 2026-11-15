from __future__ import annotations

import json
from pathlib import Path

from agentpulse.models import Paper


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "fixtures.json"


def load_fixtures() -> list[Paper]:
    """离线演示语料：真实论文标题与摘要，日期为真实发表时间。

    离线模式不按时间窗口过滤，报告里会标注“离线演示数据”。
    """
    raw = json.loads(_fixture_path().read_text(encoding="utf-8"))
    return [Paper.model_validate(item) for item in raw]


async def search_offline() -> list[Paper]:
    return load_fixtures()
