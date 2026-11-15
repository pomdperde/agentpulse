from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import httpx

from agentpulse.config import Settings

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.S)


class LLMError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


def parse_json(text: str) -> Any:
    """从模型输出里抽出 JSON：兼容裸 JSON 和 ```json 围栏两种形态。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"无法从模型输出中解析 JSON: {text[:300]}")


class LLMClient:
    """OpenAI 兼容的聊天客户端：重试、成本记账、JSON 输出。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.tokens_used = 0
        self.calls = 0
        self._client = httpx.AsyncClient(
            timeout=settings.llm_timeout,
            headers={"Authorization": f"Bearer {settings.chat_api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled and self.tokens_used < self.settings.token_budget

    def _check_budget(self, estimated: int) -> None:
        if self.tokens_used + estimated > self.settings.token_budget:
            raise BudgetExceeded(
                f"token 预算触顶：已用 {self.tokens_used}，预算 {self.settings.token_budget}"
            )

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        if not self.settings.llm_enabled:
            raise LLMError("未配置 CHAT_API_KEY，无法调用模型")

        payload = {
            "model": self.settings.chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        url = self.settings.chat_base_url.rstrip("/") + "/chat/completions"

        last_exc: Exception | None = None
        for attempt in range(self.settings.http_retries):
            self._check_budget(2048)
            try:
                resp = await self._client.post(url, json=payload)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise LLMError(f"上游 {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise LLMError(f"上游拒绝请求 {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                self.calls += 1
                self.tokens_used += int(data.get("usage", {}).get("total_tokens", 0) or 0)
                return data["choices"][0]["message"]["content"] or ""
            except (httpx.TimeoutException, httpx.TransportError, LLMError) as exc:
                last_exc = exc
                if isinstance(exc, LLMError) and "上游拒绝请求" in str(exc):
                    raise
                if attempt == self.settings.http_retries - 1:
                    break
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))
        raise last_exc or LLMError("模型调用失败")

    async def json(self, system: str, user: str, *, temperature: float = 0.2) -> Any:
        return parse_json(await self.chat(system, user, temperature=temperature))
