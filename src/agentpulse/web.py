from __future__ import annotations

import asyncio
import gc
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentpulse import qa, settings_store
from agentpulse.http import HttpClient, HttpError
from agentpulse.llm import LLMClient
from agentpulse.orchestrator import ALL_SOURCES, Pipeline
from agentpulse.sources import NCBI_ESEARCH
from agentpulse.taxonomy import (
    FIELDS,
    FIELD_SOURCES,
    TOPIC_PRESETS,
    children_of,
    find_node,
    find_subfield,
    node_hotspots,
    subfield_hotspots,
    subfields_of,
)

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
REPORT_ROOT = ROOT / "reports"


class RunRequest(BaseModel):
    topic: str = Field(default="", max_length=80)
    topics: list[str] | None = Field(default=None, max_length=5)
    days: int | None = Field(default=None, ge=1, le=3650)
    max_papers: int | None = Field(default=None, ge=1, le=200)
    sources: list[str] | None = None
    field: str | None = None
    subfield: str | None = None
    subfield3: str | None = None
    venue: str | None = Field(default=None, max_length=80)
    ieee_only: bool = False
    offline: bool = False
    no_llm: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)


class SettingsUpdate(BaseModel):
    ncbi_api_key: str | None = None
    chat_api_key: str | None = None
    chat_base_url: str | None = None
    chat_model: str | None = None
    contact_email: str | None = None


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_API_KEY_RE = re.compile(r"^\S{8,200}$")
_BASE_URL_RE = re.compile(r"^https?://[^\s]{4,200}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:\-/]{2,80}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_test_hits: dict[str, list[float]] = {}


@dataclass
class RunState:
    id: str
    topic: str
    status: str = "queued"
    events: list[dict] = field(default_factory=list)
    signal: asyncio.Event = field(default_factory=asyncio.Event)
    run_dir: Path | None = None
    error: str | None = None
    task: asyncio.Task | None = None

    def push(self, record: dict) -> None:
        self.events.append(record)
        self.signal.set()


runs: dict[str, RunState] = {}
MAX_RUNS = 12
app = FastAPI(title="AgentPulse", version="0.2.0")


def _trim_runs() -> None:
    """只保留最近若干个已完成任务，避免事件与结果长期驻留内存。"""
    if len(runs) <= MAX_RUNS:
        return
    finished = sorted(
        (state for state in runs.values() if state.status in {"done", "failed"}),
        key=lambda state: state.id,
    )
    excess = len(runs) - MAX_RUNS
    for state in finished:
        if excess <= 0:
            break
        runs.pop(state.id, None)
        excess -= 1


async def _execute(state: RunState, request: RunRequest) -> None:
    state.status = "running"
    try:
        settings = settings_store.effective_settings()
        days = request.days or settings.default_days
        max_papers = request.max_papers or settings.max_papers
        sources = [
            s for s in (request.sources or list(ALL_SOURCES)) if s in ALL_SOURCES
        ] or list(ALL_SOURCES)

        pipeline = Pipeline(
            settings,
            REPORT_ROOT,
            no_llm=request.no_llm,
            offline=request.offline,
            listener=state.push,
        )
        state.run_dir = await pipeline.run(
            topics=request.topics or [request.topic.strip()],
            days=days,
            sources=sources,
            max_papers=max_papers,
            field=request.field or None,
            subfield=request.subfield or None,
            subfield3=request.subfield3 or None,
            venue=(request.venue or "").strip() or None,
            ieee_only=request.ieee_only,
        )
        state.status = "done"
        state.push({"event": "run.complete", "status": "done", "run_dir": str(state.run_dir)})
    except Exception as exc:
        state.status = "failed"
        state.error = f"{type(exc).__name__}: {exc}"
        state.push({"event": "run.failed", "status": "failed", "error": state.error})
    finally:
        state.push({"event": "stream.end"})
        state.signal.set()
        state.task = None
        _trim_runs()
        gc.collect()


@app.post("/api/runs")
async def create_run(request: RunRequest) -> JSONResponse:
    topics = [t.strip() for t in (request.topics or [request.topic]) if t and t.strip()]
    topics = list(dict.fromkeys(topics))[:5]
    if not topics:
        raise HTTPException(status_code=400, detail="至少需要一个研究主题")
    if any(len(t) > 80 for t in topics):
        raise HTTPException(status_code=400, detail="单个主题不能超过 80 个字符")
    if request.field and request.field not in FIELDS:
        raise HTTPException(status_code=400, detail=f"未知学科门类：{request.field}")
    if request.subfield:
        if not request.field:
            raise HTTPException(status_code=400, detail="选择二级学科前需先选择一级学科门类")
        if find_subfield(request.field, request.subfield) is None:
            raise HTTPException(status_code=400, detail=f"未知二级学科：{request.subfield}")
    if request.subfield3:
        if not request.field or not request.subfield:
            raise HTTPException(status_code=400, detail="选择三级学科前需先选择二级学科")
        if find_node(request.field, (request.subfield, request.subfield3)) is None:
            raise HTTPException(status_code=400, detail=f"未知三级学科：{request.subfield3}")
    run_id = uuid.uuid4().hex[:12]
    state = RunState(id=run_id, topic=" / ".join(topics))
    runs[run_id] = state
    state.task = asyncio.create_task(_execute(state, request))
    return JSONResponse({"id": run_id, "status": state.status}, status_code=202)


@app.get("/api/runs")
async def list_runs() -> JSONResponse:
    items = [
        {
            "id": r.id,
            "topic": r.topic,
            "status": r.status,
            "events": len(r.events),
            "error": r.error,
        }
        for r in sorted(runs.values(), key=lambda x: x.id, reverse=True)
    ]
    return JSONResponse({"items": items})


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(
        {
            "id": state.id,
            "topic": state.topic,
            "status": state.status,
            "error": state.error,
            "report_url": f"/api/runs/{state.id}/report" if state.run_dir else None,
            "events": len(state.events),
        }
    )


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def generate():
        sent = 0
        while True:
            while sent < len(state.events):
                record = state.events[sent]
                sent += 1
                yield f"data: {json.dumps(record, ensure_ascii=False)}\n\n"
                if record.get("event") == "stream.end":
                    return
            if state.status in {"done", "failed"} and sent >= len(state.events):
                return
            state.signal.clear()
            try:
                await asyncio.wait_for(state.signal.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/report")
async def get_report(run_id: str) -> FileResponse:
    state = runs.get(run_id)
    if state is None or state.run_dir is None:
        raise HTTPException(status_code=404, detail="report not ready")
    path = state.run_dir / "report.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(path, media_type="text/html")


@app.post("/api/runs/{run_id}/ask")
async def ask_run(run_id: str, request: AskRequest) -> JSONResponse:
    state = runs.get(run_id)
    if state is None or state.run_dir is None:
        raise HTTPException(status_code=404, detail="任务结果尚未就绪")
    result = await qa.answer(
        request.question, state.run_dir, settings_store.effective_settings()
    )
    return JSONResponse(result)


@app.get("/api/subfields")
async def list_subfields(field: str = "", parent: str = "") -> JSONResponse:
    if field and field not in FIELDS:
        raise HTTPException(status_code=400, detail="未知学科门类")
    if parent:
        if not field:
            raise HTTPException(status_code=400, detail="查询三级学科前需选择学科门类")
        if find_subfield(field, parent) is None:
            raise HTTPException(status_code=400, detail="未知二级学科")
        nodes = children_of(field, parent)
    else:
        nodes = subfields_of(field)
    return JSONResponse(
        {
            "field": field,
            "parent": parent,
            "subfields": [
                {"name": node.name, "hotspots": [label for label, _ in node.hotspots]}
                for node in nodes
            ],
        }
    )


@app.get("/api/topics")
async def topics(field: str = "", subfield: str = "", subfield3: str = "") -> JSONResponse:
    if field and field not in FIELDS:
        raise HTTPException(status_code=400, detail="未知学科门类")
    if subfield3:
        if not field or not subfield:
            raise HTTPException(status_code=400, detail="选择三级学科前需先选择二级学科")
        hotspots = node_hotspots(field, (subfield, subfield3))
        if not hotspots:
            raise HTTPException(status_code=400, detail="未知三级学科")
        return JSONResponse(
            {"field": field, "subfield": subfield, "subfield3": subfield3, "topics": hotspots}
        )
    if subfield:
        if not field:
            raise HTTPException(status_code=400, detail="选择二级学科前需先选择学科门类")
        hotspots = subfield_hotspots(field, subfield)
        if not hotspots:
            raise HTTPException(status_code=400, detail="未知二级学科")
        return JSONResponse(
            {"field": field, "subfield": subfield, "subfield3": "", "topics": hotspots}
        )
    return JSONResponse(
        {
            "field": field,
            "subfield": "",
            "subfield3": "",
            "topics": list(TOPIC_PRESETS.get(field, TOPIC_PRESETS[""])),
        }
    )


@app.get("/api/sources")
async def list_sources(field: str = "") -> JSONResponse:
    if field and field not in FIELDS:
        raise HTTPException(status_code=400, detail="未知学科门类")
    return JSONResponse(
        {
            "field": field,
            "sources": list(FIELD_SOURCES.get(field, FIELD_SOURCES[""])),
            "all": list(ALL_SOURCES),
        }
    )


@app.get("/api/settings")
async def get_settings() -> JSONResponse:
    return JSONResponse(settings_store.describe())


@app.put("/api/settings")
async def put_settings(payload: SettingsUpdate) -> JSONResponse:
    values = {
        key: (value.strip() if isinstance(value, str) else value)
        for key, value in payload.model_dump().items()
    }
    if values.get("ncbi_api_key"):
        if not _KEY_RE.fullmatch(values["ncbi_api_key"]):
            raise HTTPException(status_code=400, detail="NCBI API Key 格式不正确")
    if values.get("chat_api_key"):
        if not _API_KEY_RE.fullmatch(values["chat_api_key"]):
            raise HTTPException(status_code=400, detail="模型 API Key 格式不正确")
    if values.get("chat_base_url"):
        if not _BASE_URL_RE.fullmatch(values["chat_base_url"]):
            raise HTTPException(status_code=400, detail="接口地址需以 http:// 或 https:// 开头")
    if values.get("chat_model"):
        if not _MODEL_RE.fullmatch(values["chat_model"]):
            raise HTTPException(status_code=400, detail="模型名格式不正确")
    if values.get("contact_email"):
        if not _EMAIL_RE.fullmatch(values["contact_email"]):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
    try:
        return JSONResponse(settings_store.update(values))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/settings/test-model")
async def test_model() -> JSONResponse:
    settings = settings_store.effective_settings()
    if not settings.llm_enabled:
        return JSONResponse({"ok": False, "message": "未配置模型 API Key，先保存后再测试"})
    client = LLMClient(settings)
    try:
        await asyncio.wait_for(
            client.chat("You are a health check service. Reply with OK only.", "ping"),
            timeout=30,
        )
        return JSONResponse({"ok": True, "model": settings.chat_model, "message": "模型可用"})
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "message": "模型响应超时（30s）"})
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:160]}
        )
    finally:
        await client.close()


@app.post("/api/settings/test")
async def test_settings(request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = [t for t in _test_hits.get(client_ip, []) if now - t < 60]
    if len(hits) >= 5:
        _test_hits[client_ip] = hits
        raise HTTPException(status_code=429, detail="测试过于频繁，请稍后再试")
    hits.append(now)
    _test_hits[client_ip] = hits

    settings = settings_store.effective_settings()
    params = {
        "db": "pubmed",
        "term": "agent memory",
        "retmode": "json",
        "retmax": 0,
        "tool": "agentpulse",
    }
    if settings.contact_email:
        params["email"] = settings.contact_email
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    try:
        async with HttpClient(timeout=15, retries=1) as http:
            data = await http.get_json(NCBI_ESEARCH, params=params)
    except HttpError as exc:
        friendly = {
            400: "密钥无效或参数被拒绝（HTTP 400）",
            401: "密钥未通过鉴权（HTTP 401）",
            403: "请求被拒绝（HTTP 403）",
            429: "上游限流（HTTP 429），请稍后再试",
        }.get(exc.status, f"上游返回 HTTP {exc.status}")
        return JSONResponse({"ok": False, "status": exc.status, "message": friendly})
    except Exception:
        return JSONResponse({"ok": False, "message": "网络请求失败，请检查网络或代理"})

    total = int(((data.get("esearchresult") or {}).get("count")) or 0)
    return JSONResponse(
        {
            "ok": True,
            "total": total,
            "rate_limit": "10/s" if settings.ncbi_api_key else "3/s",
        }
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    settings = settings_store.effective_settings()
    return JSONResponse({"ok": True, "llm": settings.llm_enabled, "version": app.version})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


def serve(port: int = 8123, host: str = "127.0.0.1") -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
