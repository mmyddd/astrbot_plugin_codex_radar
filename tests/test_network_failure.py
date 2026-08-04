"""网络请求失败与缓存行为测试。

包含：
- client.fetch_json 对真实本地 HTTP 服务的超时 / 5xx 重试 / 404 行为
- RadarService 对抓取失败的诊断传播
- 主接口失败时回退静态快照接口
- TTL 缓存生效
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from codex_radar.cache import TTLCache
from codex_radar.client import fetch_json
from codex_radar.errors import RadarFetchError
from codex_radar.radar_parser import parse_intelligence_efficiency
from codex_radar.errors import RadarParseError
from codex_radar.service import RadarConfig, RadarService

PAYLOAD = {"ok": True, "value": 42}


class _Handler(BaseHTTPRequestHandler):
    state = {"fail_count": 0}

    def log_message(self, *args):  # 静默日志
        pass

    def do_GET(self):  # noqa: N802
        path = self.path
        if path == "/ok":
            body = json.dumps(PAYLOAD).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/flaky":
            if _Handler.state["fail_count"] < 2:
                _Handler.state["fail_count"] += 1
                self.send_response(503)
                self.end_headers()
            else:
                body = json.dumps(PAYLOAD).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif path == "/missing":
            self.send_response(404)
            self.end_headers()
        elif path == "/slow":
            time.sleep(2.0)
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def server():
    _Handler.state["fail_count"] = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_fetch_ok(server):
    assert asyncio.run(fetch_json(f"{server}/ok", timeout_seconds=5, retries=0)) == PAYLOAD


def test_fetch_retries_then_succeeds(server):
    _Handler.state["fail_count"] = 0
    assert asyncio.run(fetch_json(f"{server}/flaky", timeout_seconds=5, retries=2)) == PAYLOAD


def test_fetch_404_raises_diagnostic(server):
    with pytest.raises(RadarFetchError) as exc:
        asyncio.run(fetch_json(f"{server}/missing", timeout_seconds=5, retries=1))
    message = str(exc.value)
    assert "404" in message
    assert "未返回虚构数据" in message
    assert f"{server}/missing" in message


def test_fetch_timeout_raises_diagnostic(server):
    with pytest.raises(RadarFetchError) as exc:
        asyncio.run(fetch_json(f"{server}/slow", timeout_seconds=0.4, retries=1))
    message = str(exc.value)
    assert "超时" in message
    assert "重试" in message


def test_fetch_connect_error_diagnostic():
    with pytest.raises(RadarFetchError) as exc:
        asyncio.run(
            fetch_json(
                "http://127.0.0.1:1/nope",  # 端口 1 不可达
                timeout_seconds=1.0,
                retries=1,
            )
        )
    message = str(exc.value)
    assert "无法连接" in message


async def test_service_fetch_error_propagates():
    service = RadarService(RadarConfig(request_retries=0))

    async def broken(*args, **kwargs):
        raise httpx.ConnectError("refused")

    service._fetcher = broken
    with pytest.raises(RadarFetchError) as exc:
        await service.get_radar_snapshot()
    assert "codexradar.com" in str(exc.value)


async def test_service_fallback_url_used(efficiency_payload):
    """主接口失败时回退到静态快照接口。"""
    calls: list[str] = []
    service = RadarService(RadarConfig(request_retries=0))

    async def fetcher(url, **kwargs):
        calls.append(url)
        if "api/intelligence-efficiency" in url:
            raise httpx.ConnectError("refused")
        return efficiency_payload

    service._fetcher = fetcher
    snapshot = await service.get_radar_snapshot()
    assert snapshot.source_url.endswith("intelligence-efficiency.json")
    assert len(calls) == 2


async def test_service_prefers_parse_error_over_fetch_error(efficiency_payload):
    """主接口返回坏结构、备用接口可用：应使用备用接口。"""
    service = RadarService(RadarConfig(request_retries=0))

    async def fetcher(url, **kwargs):
        if "api/intelligence-efficiency" in url:
            return {"schema": 99, "combos": []}  # 结构变化
        return efficiency_payload

    service._fetcher = fetcher
    snapshot = await service.get_radar_snapshot()
    assert snapshot.schema == 1


async def test_cache_hit_avoids_refetch():
    calls = {"n": 0}
    service = RadarService(RadarConfig(radar_cache_seconds=3600))

    async def fetcher(*args, **kwargs):
        calls["n"] += 1
        return {"schema": 1, "combos": [{"model": "m1", "effort": "high"}],
                "tasks": [{"id": "t1"}],
                "cells": {"t1|m1|high": {"ran_by": [{"passed": True, "duration_sec": 60, "actual_cost_usd": 0.1}]}},
                "baseline_generated_at": "2026-08-04T00:00:00+00:00"}

    service._fetcher = fetcher
    first = await service.get_radar_snapshot()
    second = await service.get_radar_snapshot()
    assert first is second  # 同一缓存对象
    assert calls["n"] == 1


async def test_cache_expiry():
    cache = TTLCache()
    await cache.set("k", "v", ttl_seconds=0.05)
    assert await cache.get("k") == "v"
    await asyncio.sleep(0.1)
    assert await cache.get("k") is None


def test_parse_error_diagnostic_contains_fields(efficiency_payload):
    payload = dict(efficiency_payload)
    del payload["cells"]
    with pytest.raises(RadarParseError) as exc:
        parse_intelligence_efficiency(payload)
    assert "cells" in str(exc.value)


# ---------------------------------------------------------- history_hours 档位


def test_history_hours_options_match_deng_site():
    """档位与 deng 站 IQ_TREND_HOUR_OPTIONS=[24,48,72] 及 _conf_schema.json 一致。"""
    from codex_radar.service import HISTORY_HOURS_OPTIONS

    assert HISTORY_HOURS_OPTIONS == (24, 48, 72)
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_conf_schema.json")
    schema = json.load(open(schema_path, encoding="utf-8"))
    assert schema["history_hours"]["options"] == list(HISTORY_HOURS_OPTIONS)


def test_history_hours_config_valid_options():
    from codex_radar.service import RadarConfig

    for hours in (24, 48, 72):
        assert RadarConfig.from_dict({"history_hours": hours}).history_hours == hours


def test_history_hours_config_invalid_falls_back():
    """非法档位（站点没有的取值）回退默认 72。"""
    from codex_radar.service import RadarConfig

    for bad in (0, -5, 100, 7, 168):
        assert RadarConfig.from_dict({"history_hours": bad}).history_hours == 72
