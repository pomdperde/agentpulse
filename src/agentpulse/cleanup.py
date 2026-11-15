from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

# 一轮任务至少会有这些文件之一；用来区分报告目录和用户随手放的其它目录
RUN_MARKERS = ("report.md", "report.html", "candidates.json")


@dataclass
class CleanResult:
    removed: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    dry_run: bool = False


def find_runs(root: Path) -> list[Path]:
    """列出 root 下的报告目录，按修改时间从新到旧。"""
    if not root.is_dir():
        return []
    runs = [
        path
        for path in root.iterdir()
        if path.is_dir() and any((path / marker).exists() for marker in RUN_MARKERS)
    ]
    runs.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return runs


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def plan(
    root: Path,
    keep: int = 20,
    days: int | None = None,
    now: float | None = None,
    exclude: set[Path] | None = None,
) -> tuple[list[Path], list[Path]]:
    """返回 (待删除, 保留)。保留最近 keep 个；给了 days 时再删掉超过该天数的。"""
    runs = find_runs(root)
    blocked = {path.resolve() for path in exclude or set()}
    cutoff = (now if now is not None else time.time()) - days * 86400 if days else None
    remove: list[Path] = []
    kept: list[Path] = []
    for index, path in enumerate(runs):
        expired = cutoff is not None and path.stat().st_mtime < cutoff
        if path.resolve() in blocked:
            kept.append(path)
        elif index >= keep or expired:
            remove.append(path)
        else:
            kept.append(path)
    return remove, kept


def clean(
    root: Path,
    keep: int = 20,
    days: int | None = None,
    dry_run: bool = False,
    exclude: set[Path] | None = None,
) -> CleanResult:
    remove, kept = plan(root, keep=keep, days=days, exclude=exclude)
    result = CleanResult(kept=kept, dry_run=dry_run)
    for path in remove:
        result.freed_bytes += dir_size(path)
        if not dry_run:
            shutil.rmtree(path)
        result.removed.append(path)
    return result
