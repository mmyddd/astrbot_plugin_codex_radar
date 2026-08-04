"""HTTP 客户端：合理的超时、重试与可诊断错误。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from .errors import RadarFetchError

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AstrBot-CodexRadar/1.0)",
    "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _describe_transport_error(exc: BaseException) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "无法连接服务器（网络不可达或连接超时）"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "请求超时"
    if isinstance(exc, httpx.ReadError):
        return "连接中断（读取响应失败）"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "服务器协议错误"
    if isinstance(exc, httpx.TransportError):
        return f"传输层错误：{type(exc).__name__}"
    return f"{type(exc).__name__}: {exc}"


async def fetch_json(
    url: str,
    *,
    timeout_seconds: float = 12.0,
    retries: int = 2,
    headers: Optional[dict] = None,
) -> Any:
    """带超时与重试的 JSON 抓取。

    失败时抛出 :class:`RadarFetchError`，其中包含地址、状态码、
    原因、耗时与重试次数，便于用户诊断。
    """
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout_seconds,
                    connect=timeout_seconds,
                    read=timeout_seconds,
                ),
                headers=merged_headers,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
            elapsed = time.monotonic() - started

            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise RadarFetchError(
                        url,
                        status=response.status_code,
                        reason=f"响应内容不是有效 JSON：{exc}",
                        attempts=attempt,
                        elapsed=elapsed,
                    ) from exc

            if response.status_code in _RETRYABLE_STATUS and attempt <= retries:
                await asyncio.sleep(0.5 * attempt)
                continue
            raise RadarFetchError(
                url,
                status=response.status_code,
                reason=f"HTTP {response.status_code}",
                attempts=attempt,
                elapsed=elapsed,
            )
        except httpx.TransportError as exc:
            if attempt <= retries:
                await asyncio.sleep(0.5 * attempt)
                continue
            elapsed = time.monotonic() - started
            raise RadarFetchError(
                url,
                reason=_describe_transport_error(exc),
                attempts=attempt,
                elapsed=elapsed,
            ) from exc
