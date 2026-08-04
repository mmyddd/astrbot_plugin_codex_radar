"""pytest 共享 fixtures：合成数据（严格镜像真实接口结构）、假事件。"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class FakeEvent:
    """模拟 AstrMessageEvent 的最小对象，捕获处理器 yield 的结果。"""

    def __init__(self, message_str: str = "") -> None:
        self.message_str = message_str
        self.results: list[tuple[str, object]] = []

    def plain_result(self, text: str):
        self.results.append(("plain", text))
        return self.results[-1]

    def chain_result(self, chain: list):
        self.results.append(("chain", chain))
        return self.results[-1]

    def image_result(self, url_or_path: str):
        self.results.append(("image", url_or_path))
        return self.results[-1]


@pytest.fixture
def fake_event() -> FakeEvent:
    return FakeEvent("降智雷达")


@pytest.fixture
def efficiency_payload() -> dict:
    """合成智力效率数据，结构与 codexradar.com/api/intelligence-efficiency 一致。

    数值经过设计，便于断言站点口径的聚合公式：
    - gpt-5.6-sol low/high：pass=2/3，耗时均值 5.6667 分钟，花费均值 $0.5833
    - gpt-5.6-terra max：pass=1/2（task-2 的 runner 缺 passed 字段，应被跳过）
    - gpt-5.6-luna ultra：task-1 cost_complete=false（花费应被排除）
    - future-model-x：未知模型也应完整列出
    """
    with open(os.path.join(FIXTURES_DIR, "efficiency_sample.json"), encoding="utf-8") as f:
        return json.load(f)


def _hourly_series(base: float, jitter: float = 0.0, null_every: int = 0) -> list[dict]:
    start = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(96):  # 4 天每小时一点
        ts = start + timedelta(hours=i)
        if null_every and i % null_every == 0:
            score = None
        else:
            score = round(base + jitter * (i % 5) + (i % 7) * 0.1, 1)
        out.append({"ts": ts.isoformat().replace("+00:00", "Z"), "score": score, "n": 336})
    return out


@pytest.fixture
def history_payload() -> dict:
    """合成 iq-history 数据，结构与 api.codexradar.com/api/v1/iq-history 一致。

    - 96 个每小时观察点（07-31 00:00 ~ 08-03 23:00 UTC）
    - 72h 窗口应截取 07-31 23:00 之后的 73 个点
    - latest: 前缀系列用于「实时监控」，解析时应优先
    - deepseek 系列全为 null（站点当前无样本）
    """
    start = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
    deepseek = [
        {
            "ts": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "score": None,
            "n": 0,
        }
        for i in range(96)
    ]
    return {
        "gpt-5.6-sol": _hourly_series(90.0, 2.0),
        "gpt-5.6-sol@max": _hourly_series(100.0, 1.0),
        "latest:gpt-5.6-sol": _hourly_series(91.0, 1.5),
        "gpt-5.5": _hourly_series(85.0, 1.0, null_every=37),
        "deepseek-v4-flash": deepseek,
    }


@pytest.fixture
def history_payload_empty_series() -> dict:
    """系列存在但完全没有数据点（异常但结构合法）。"""
    return {"gpt-5.6-sol": []}
