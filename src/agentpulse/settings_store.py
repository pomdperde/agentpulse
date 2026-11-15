from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from agentpulse.config import Settings

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "data" / "settings.json"

# 只允许通过设置页维护这些字段，其余配置仍由环境变量 / .env 管理
FIELDS = (
    "ncbi_api_key",
    "chat_api_key",
    "chat_base_url",
    "chat_model",
    "contact_email",
)
MASKED_FIELDS = {"ncbi_api_key", "chat_api_key"}
# 这两个字段有内置默认值，未被显式配置时不应视为「被 .env 锁定」
SOFT_DEFAULT_FIELDS = {"chat_base_url", "chat_model"}

_LOCK = threading.Lock()


def _read_store() -> dict[str, str]:
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in FIELDS and isinstance(v, str)}


def _write_store(data: dict[str, str]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


def resolve(field: str) -> tuple[str, str, bool]:
    """返回 (生效值, 来源, 是否锁定)。

    优先级：进程环境变量 > data/settings.json > .env。
    环境变量与 .env 都属于部署层配置，设置页只读；只有本地文件可编辑。
    """
    env_value = os.getenv(field.upper(), "")
    if env_value:
        return env_value, "env", True

    stored = _read_store().get(field, "")
    if stored:
        return stored, "file", False

    value = getattr(Settings(), field, "") or ""
    if value and field in SOFT_DEFAULT_FIELDS:
        return "", "default", False
    if value:
        return value, "env_file", True
    return "", "", False


def effective_settings() -> Settings:
    """在 Settings 基础上叠加设置页写入的值，供流水线与健康检查使用。"""
    base = Settings()
    overrides: dict[str, str] = {}
    for field in FIELDS:
        value, source, _ = resolve(field)
        if value and source == "file":
            overrides[field] = value
    return base.model_copy(update=overrides) if overrides else base


def describe() -> dict:
    fields: dict[str, dict] = {}
    for field in FIELDS:
        value, source, locked = resolve(field)
        item: dict = {
            "configured": bool(value) or source == "default",
            "source": source,
            "locked": locked,
        }
        if source == "default":
            item["value"] = getattr(Settings(), field, "") or ""
        elif field in MASKED_FIELDS:
            item["hint"] = _mask(value)
        else:
            item["value"] = value
        fields[field] = item
    return {
        "fields": fields,
        "precedence": ["env", "file", "env_file"],
        "llm_ready": effective_settings().llm_enabled,
    }


def update(values: dict[str, str | None]) -> dict:
    """写入可编辑字段；锁定字段抛 PermissionError，由接口层转 409。"""
    with _LOCK:
        store = _read_store()
        for field in FIELDS:
            if field not in values or values[field] is None:
                continue
            _, source, locked = resolve(field)
            if locked:
                raise PermissionError(f"{field} 由 {source} 提供，设置页不可修改")
            value = str(values[field]).strip()
            if value:
                store[field] = value
            else:
                store.pop(field, None)
        _write_store(store)
    return describe()
