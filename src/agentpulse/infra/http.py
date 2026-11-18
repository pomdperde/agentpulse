from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

USER_AGENT = "AgentPulse/0.1 (paper hotspot digest; +https://github.com/)"

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url


class HttpClient:
    """带重试的异步 HTTP 客户端。

    只对瞬时故障重试（429/5xx/超时/连接错误），4xx 直接失败——
    参数写错再重试只会把一次小错放大成请求风暴。
    """

    def __init__(self, timeout: float = 30.0, retries: int = 3, max_connections: int = 20):
        self.retries = max(1, retries)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(
                max_connections=max(4, max_connections),
                max_keepalive_connections=max(4, max_connections // 2),
            ),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = await self._client.request(method, url, **kwargs)
                if resp.status_code in RETRY_STATUS:
                    raise HttpError(resp.status_code, url, resp.text)
                if resp.status_code >= 400:
                    raise HttpError(resp.status_code, url, resp.text)
                return resp
            except (httpx.TimeoutException, httpx.TransportError, HttpError) as exc:
                last_exc = exc
                if isinstance(exc, HttpError) and exc.status not in RETRY_STATUS:
                    raise
                if attempt == self.retries - 1:
                    break
                # 指数退避 + 抖动，避免同一时刻的重试风暴
                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    async def get_text(self, url: str, **kwargs: Any) -> str:
        resp = await self._request("GET", url, **kwargs)
        return resp.text

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self._request("GET", url, **kwargs)
        return resp.json()

    async def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        resp = await self._request("POST", url, json=payload, **kwargs)
        return resp.json()
