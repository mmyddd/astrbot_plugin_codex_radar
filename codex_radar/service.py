"""业务编排：抓取 → 解析 → 缓存，供指令处理器与测试共用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .cache import TTLCache
from .client import fetch_json
import httpx
from .client import _describe_transport_error
from .errors import RadarError, RadarFetchError, RadarParseError
from .history_parser import HistorySnapshot, parse_iq_history
from .radar_parser import RadarSnapshot, parse_intelligence_efficiency

RADAR_URLS = [
    "https://codexradar.com/api/intelligence-efficiency",
    # 站点内置的静态快照（前端 fallback，新分布式 points 格式）
    "https://codexradar.com/data/intelligence-efficiency.json",
    "https://api.codexradar.com/api/v1/intelligence-efficiency",
    "https://api.codexradar.com/api/v1/table",
]

# 站点前端（deng.codexradar.com）实际使用的历史接口：
# API = "https://api." + apex（apex = codexradar.com），
# 路径 /api/v1/iq-history，cache-buster v=20260719-language-1
HISTORY_URL = "https://api.codexradar.com/api/v1/iq-history?v=20260719-language-1"

FetchFn = Callable[..., Awaitable[Any]]


# deng.codexradar.com 前端的 IQ 历史档位（IQ_TREND_HOUR_OPTIONS = [24, 48, 72]）
HISTORY_HOURS_OPTIONS: tuple[int, ...] = (24, 48, 72)
DEFAULT_HISTORY_HOURS = 72

@dataclass
class RadarConfig:
    """插件配置（与 _conf_schema.json 对应）。"""

    radar_cache_seconds: int = 300
    history_cache_seconds: int = 600
    request_timeout_seconds: float = 12.0
    request_retries: int = 2
    history_hours: int = DEFAULT_HISTORY_HOURS
    send_chart_image: bool = True

    @classmethod
    def from_dict(cls, config: Optional[dict]) -> "RadarConfig":
        if not config:
            return cls()
        kwargs = {}
        for field_name in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if field_name in config:
                try:
                    value = config[field_name]
                    if value is not None:
                        kwargs[field_name] = type(getattr(cls(), field_name))(value)
                except (TypeError, ValueError):
                    continue
        # history_hours 仅允许 deng 站三档（24/48/72），非法值回退默认
        if kwargs.get("history_hours") not in HISTORY_HOURS_OPTIONS:
            kwargs.pop("history_hours", None)
        return cls(**kwargs)


class RadarService:
    """抓取 + 缓存 + 解析 的聚合服务。"""

    def __init__(
        self,
        config: Optional[RadarConfig] = None,
        *,
        fetcher: Optional[FetchFn] = None,
        cache: Optional[TTLCache] = None,
    ) -> None:
        self.config = config or RadarConfig()
        self._fetcher = fetcher or fetch_json
        self._cache = cache or TTLCache()

    async def get_radar_snapshot(self) -> RadarSnapshot:
        """获取智力效率快照（带缓存）。失败抛 RadarError。"""
        cached = await self._cache.get("radar_snapshot")
        if cached is not None:
            return cached

        first_error: Optional[RadarError] = None
        parse_errors: list[RadarParseError] = []
        for url in RADAR_URLS:
            try:
                payload = await self._fetcher(
                    url,
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.request_retries,
                )
                snapshot = parse_intelligence_efficiency(payload, url=url)
                await self._cache.set(
                    "radar_snapshot",
                    snapshot,
                    ttl_seconds=self.config.radar_cache_seconds,
                )
                return snapshot
            except RadarParseError as exc:
                parse_errors.append(exc)
                first_error = first_error or exc
                continue
            except RadarFetchError as exc:
                first_error = first_error or exc
                continue
            except httpx.TransportError as exc:
                # 防御：自定义 fetcher 抛出的底层传输错误统一包装
                first_error = first_error or RadarFetchError(
                    url, reason=_describe_transport_error(exc)
                )
                continue

        # 结构变化优先提示（比网络错误更有诊断价值）
        if parse_errors:
            raise parse_errors[0]
        raise first_error or RadarFetchError(RADAR_URLS[0], reason="未知错误")

    async def get_history_snapshot(self) -> HistorySnapshot:
        """获取 IQ 历史快照（带缓存）。失败抛 RadarError。"""
        cached = await self._cache.get("history_snapshot")
        if cached is not None:
            return cached

        payload = await self._fetcher(
            HISTORY_URL,
            timeout_seconds=self.config.request_timeout_seconds,
            retries=self.config.request_retries,
        )
        snapshot = parse_iq_history(
            payload,
            hours=self.config.history_hours,
            url=HISTORY_URL,
        )
        await self._cache.set(
            "history_snapshot",
            snapshot,
            ttl_seconds=self.config.history_cache_seconds,
        )
        return snapshot

    async def clear_cache(self) -> None:
        await self._cache.clear()
