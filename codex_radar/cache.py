"""TTL 缓存：进程内、线程安全的轻量缓存。"""

from __future__ import annotations

import asyncio
import time
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """带 TTL 的进程内缓存。

    - 线程安全（asyncio.Lock 保护）
    - get 时惰性过期
    - 适合缓存远端数据，避免每次指令都重复请求
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[T]:
        """返回未过期的缓存值；不存在或已过期返回 None。"""
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if time.monotonic() > expires_at:
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: T, ttl_seconds: float) -> None:
        """写入缓存。ttl_seconds <= 0 表示不缓存。"""
        if ttl_seconds <= 0:
            return
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl_seconds, value)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
