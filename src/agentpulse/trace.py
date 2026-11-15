from __future__ import annotations

from typing import Callable

import json
import time
from pathlib import Path


class Trace:
    """事件流落盘：每行一个 JSON，实时 flush，崩溃不丢已发生的事件。

    listener 用于把同一批事件推给 Web 面板（SSE），不改变落盘行为。
    """

    def __init__(self, path: Path, listener: Callable[[dict], None] | None = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0 = time.perf_counter()
        self._fh = self.path.open("w", encoding="utf-8")
        self._listener = listener

    def event(self, name: str, **fields: object) -> None:
        record = {
            "t": round(time.perf_counter() - self._t0, 3),
            "event": name,
            **fields,
        }
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        if self._listener is not None:
            try:
                self._listener(record)
            except Exception:
                # 推送失败不能影响主流程
                pass

    def close(self) -> None:
        self._fh.close()
