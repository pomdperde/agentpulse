"""任务历史持久化：SQLite 单文件，无外部服务依赖。

设计要点：
- 单进程内存态之外的第二存储，重启后可恢复任务列表与事件流
- 全部 IO 走 asyncio.to_thread，不阻塞事件循环（SSE 推送不卡顿）
- 单连接 + 线程锁 + WAL 模式，保证并发写安全
- 报告文件仍落盘在 reports/，DB 只存元数据与事件，不存大字段
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

# 全局连接与锁：单进程内复用，避免每次操作都打开文件
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_db_path: Path | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    salt            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER,
    topic        TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    error        TEXT,
    run_dir      TEXT,
    fetched      INTEGER DEFAULT 0,
    kept         INTEGER DEFAULT 0,
    llm_used     INTEGER DEFAULT 0,
    sources      TEXT,
    days         INTEGER,
    field        TEXT,
    params       TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_user_id ON runs(user_id);
"""

# 兼容旧库：若 runs 表无 user_id 列则补上（幂等）
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN user_id INTEGER",
]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path) -> None:
    """初始化数据库连接与表结构，幂等。"""
    global _conn, _db_path
    with _lock:
        if _conn is None or _db_path != path:
            if _conn is not None:
                _conn.close()
            _conn = _connect(path)
            _conn.executescript(SCHEMA)
            # 兼容旧库：尝试执行迁移（列已存在会报错，忽略即可）
            for stmt in _MIGRATIONS:
                try:
                    _conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            _conn.commit()
            _db_path = path


def close_db() -> None:
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = None


def _get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db()")
    return _conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 同步原语（在线程池中执行） ----------
# 所有操作共享同一连接，必须加锁串行化，否则多线程并发写会触发
# sqlite3.InterfaceError / OperationalError。

def _insert_run(
    run_id: str,
    topic: str,
    params: dict[str, Any],
    sources: list[str] | None = None,
    days: int | None = None,
    field: str | None = None,
    user_id: int | None = None,
) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO runs
               (id, user_id, topic, status, created_at, sources, days, field, params)
               VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (
                run_id,
                user_id,
                topic,
                _now(),
                json.dumps(sources or [], ensure_ascii=False),
                days,
                field or "",
                json.dumps(params, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()


def _update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    with _lock:
        conn = _get_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(run_id)
        conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", values)
        conn.commit()


def _append_event(run_id: str, seq: int, payload: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO events (run_id, seq, payload, created_at) VALUES (?, ?, ?, ?)",
            (run_id, seq, json.dumps(payload, ensure_ascii=False, default=str), _now()),
        )
        conn.commit()


def _list_runs(limit: int, user_id: int | None = None) -> list[sqlite3.Row]:
    with _lock:
        conn = _get_conn()
        if user_id is not None:
            cur = conn.execute(
                "SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return cur.fetchall()


def _get_run(run_id: str) -> sqlite3.Row | None:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        return cur.fetchone()


def _get_events(run_id: str) -> list[sqlite3.Row]:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
        )
        return cur.fetchall()


def _delete_run(run_id: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()


def _delete_old_runs(keep: int) -> int:
    """只保留最近 keep 条，删除更早的 run 及其事件。返回删除条数。"""
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "SELECT id FROM runs ORDER BY created_at DESC LIMIT -1 OFFSET ?", (keep,)
        )
        old_ids = [row["id"] for row in cur.fetchall()]
        if not old_ids:
            return 0
        conn.executemany("DELETE FROM runs WHERE id = ?", [(rid,) for rid in old_ids])
        conn.commit()
        return len(old_ids)


# ---------- 异步接口 ----------

async def insert_run(
    run_id: str,
    topic: str,
    params: dict[str, Any],
    sources: list[str] | None = None,
    days: int | None = None,
    field: str | None = None,
    user_id: int | None = None,
) -> None:
    await asyncio.to_thread(
        _insert_run, run_id, topic, params, sources, days, field, user_id
    )


async def update_run_status(
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    run_dir: str | None = None,
    finished: bool = False,
    fetched: int | None = None,
    kept: int | None = None,
    llm_used: bool | None = None,
) -> None:
    fields: dict[str, Any] = {"status": status}
    if error is not None:
        fields["error"] = error
    if run_dir is not None:
        fields["run_dir"] = run_dir
    if finished:
        fields["finished_at"] = _now()
    if fetched is not None:
        fields["fetched"] = fetched
    if kept is not None:
        fields["kept"] = kept
    if llm_used is not None:
        fields["llm_used"] = 1 if llm_used else 0
    await asyncio.to_thread(_update_run, run_id, **fields)


async def append_event(run_id: str, seq: int, payload: dict[str, Any]) -> None:
    await asyncio.to_thread(_append_event, run_id, seq, payload)


async def list_runs(limit: int = 100, user_id: int | None = None) -> list[dict[str, Any]]:
    rows = await asyncio.to_thread(_list_runs, limit, user_id)
    return [_row_to_run_dict(row) for row in rows]


async def get_run(run_id: str) -> dict[str, Any] | None:
    row = await asyncio.to_thread(_get_run, run_id)
    return _row_to_run_dict(row) if row else None


async def get_events(run_id: str) -> list[dict[str, Any]]:
    rows = await asyncio.to_thread(_get_events, run_id)
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            events.append(json.loads(row["payload"]))
        except (ValueError, TypeError):
            events.append({"event": "raw", "payload": row["payload"]})
    return events


async def delete_run(run_id: str) -> None:
    await asyncio.to_thread(_delete_run, run_id)


async def delete_old_runs(keep: int) -> int:
    return await asyncio.to_thread(_delete_old_runs, keep)


def _row_to_run_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "topic": row["topic"],
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
        "run_dir": row["run_dir"],
        "fetched": row["fetched"] or 0,
        "kept": row["kept"] or 0,
        "llm_used": bool(row["llm_used"]),
        "sources": json.loads(row["sources"]) if row["sources"] else [],
        "days": row["days"],
        "field": row["field"] or "",
        "params": json.loads(row["params"]) if row["params"] else {},
    }


# ---------- 用户管理 ----------

def _create_user(username: str, password_hash: str, salt: str, role: str) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, salt, role, _now()),
        )
        conn.commit()
        return cur.lastrowid


def _get_user(user_id: int) -> sqlite3.Row | None:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()


def _get_user_by_username(username: str) -> sqlite3.Row | None:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
        return cur.fetchone()


def _list_users() -> list[sqlite3.Row]:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("SELECT id, username, role, created_at, last_login_at FROM users ORDER BY id ASC")
        return cur.fetchall()


def _update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    with _lock:
        conn = _get_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(user_id)
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", values)
        conn.commit()


def _delete_user(user_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def _user_count() -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        return int(cur.fetchone()["c"])


def _row_to_user_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


async def create_user(username: str, password_hash: str, salt: str, role: str = "user") -> int:
    return await asyncio.to_thread(_create_user, username, password_hash, salt, role)


async def get_user(user_id: int) -> dict[str, Any] | None:
    row = await asyncio.to_thread(_get_user, user_id)
    return _row_to_user_dict(row) if row else None


async def get_user_with_secret(user_id: int) -> dict[str, Any] | None:
    """含密码哈希，仅供登录校验使用。"""
    row = await asyncio.to_thread(_get_user, user_id)
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "password_hash": row["password_hash"],
        "salt": row["salt"],
        "role": row["role"],
    }


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    row = await asyncio.to_thread(_get_user_by_username, username)
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "password_hash": row["password_hash"],
        "salt": row["salt"],
        "role": row["role"],
    }


async def list_users() -> list[dict[str, Any]]:
    rows = await asyncio.to_thread(_list_users)
    return [_row_to_user_dict(row) for row in rows]


async def update_user(user_id: int, **fields: Any) -> None:
    await asyncio.to_thread(_update_user, user_id, **fields)


async def delete_user(user_id: int) -> None:
    await asyncio.to_thread(_delete_user, user_id)


async def user_count() -> int:
    return await asyncio.to_thread(_user_count)


async def ensure_default_admin() -> None:
    """首次启动时若无用户，创建默认管理员 admin/admin123。"""
    from agentpulse.infra.auth import hash_password

    if await user_count() > 0:
        return
    pw_hash, salt = hash_password("admin123")
    await create_user("admin", pw_hash, salt, role="admin")
