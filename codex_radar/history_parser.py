"""解析 IQ 历史曲线数据（api.codexradar.com/api/v1/iq-history）。

接口返回一个对象：key 为系列名，value 为按时间升序的每小时观察点列表：
    {
      "gpt-5.6-sol": [{"ts": "...", "score": 92.5, "n": 2016}, ...],
      "gpt-5.6-sol@max": [...],
      "latest:gpt-5.6-sol": [...],   # 「实时监控」模式使用的系列
      ...
    }

- score 为 0-150 的 IQ 分数（100% 通过率 -> 150）；无数据时为 null
- 「latest:」前缀系列用于站点「实时监控」模式，优先用于绘图
- 72h 窗口按所有系列中最新观察时间回退 hours 小时截取
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from .errors import RadarParseError

LATEST_PREFIX = "latest:"


# 模型别名表：支持「雷达历史 sol / d4flash」等短名
MODEL_ALIASES: dict[str, list[str]] = {
    "gpt-5.6-sol": ["sol", "5.6sol", "gpt5.6sol", "gpt56sol"],
    "gpt-5.6-terra": ["terra", "5.6terra", "gpt5.6terra", "gpt56terra"],
    "gpt-5.6-luna": ["luna", "5.6luna", "gpt5.6luna", "gpt56luna"],
    "gpt-5.5": ["5.5", "gpt5.5", "gpt55"],
    "deepseek-v4-flash": [
        "d4flash", "deepseek", "deepseekv4flash", "deepseekv4", "d4", "ds", "v4flash",
    ],
}


def resolve_model_alias(query: str, available: Sequence[str]) -> str:
    """把用户输入（完整名 / 别名 / 唯一子串）解析为数据中存在的模型名。"""
    q = query.strip().lower().replace(" ", "")
    if not q:
        raise ValueError("请指定模型名，例如：雷达历史 sol / 雷达历史 d4flash")
    available = list(available)
    if not available:
        raise ValueError("当前数据中没有可用模型系列")

    # 1) 完整模型名精确匹配
    for model in available:
        if model.lower() == q:
            return model
    # 2) 别名精确匹配（别名指向的规范模型必须存在于数据中）
    for model, aliases in MODEL_ALIASES.items():
        if model in available and (model.lower() == q or q in {a.lower() for a in aliases}):
            return model
    # 3) 唯一子串匹配
    matches = [model for model in available if q in model.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"「{query}」匹配到多个模型：{'、'.join(matches)}，请使用更精确的名称（如 sol / terra / luna / 5.5 / d4flash）"
        )
    raise ValueError(
        f"未找到模型「{query}」。可用模型：{'、'.join(available)}；"
        "别名示例：sol / terra / luna / 5.5 / d4flash"
    )




def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class HistoryPoint:
    ts: str
    score: Optional[float]
    n: int = 0


@dataclass
class HistorySeries:
    """一个 IQ 系列（模型，或 模型@思考强度）。"""

    key: str
    model: str
    effort: Optional[str]
    points: list[HistoryPoint] = field(default_factory=list)

    @property
    def is_latest(self) -> bool:
        return self.key.startswith(LATEST_PREFIX)

    @property
    def base_key(self) -> str:
        return self.key[len(LATEST_PREFIX):] if self.is_latest else self.key

    def latest(self) -> Optional[HistoryPoint]:
        return self.points[-1] if self.points else None

    def first(self) -> Optional[HistoryPoint]:
        return self.points[0] if self.points else None

    def scores(self) -> list[Optional[float]]:
        return [p.score for p in self.points]

    def has_data(self) -> bool:
        return any(p.score is not None for p in self.points)


@dataclass
class HistorySnapshot:
    hours: int
    series: list[HistorySeries] = field(default_factory=list)
    window_end: Optional[str] = None
    window_start: Optional[str] = None
    updated_at: Optional[str] = None
    source_url: str = ""

    def preferred_series(self) -> list[HistorySeries]:
        """优先「latest:」系列（实时监控），缺失时回退普通系列。

        保持接口给出的 key 顺序；同 base_key 的 latest 系列覆盖普通系列。
        """
        chosen: dict[str, HistorySeries] = {}
        order: list[str] = []
        for series in self.series:
            base = series.base_key
            if base not in chosen:
                chosen[base] = series
                order.append(base)
            elif series.is_latest:
                chosen[base] = series
        return [chosen[key] for key in order]

    def model_series(self) -> list[HistorySeries]:
        """模型级系列（无 @effort），用于总览曲线。"""
        return [s for s in self.preferred_series() if s.effort is None]


    def models(self) -> list[str]:
        """数据中出现的模型名（保持系列出现顺序、去重）。"""
        seen: list[str] = []
        for series in self.series:
            if series.model not in seen:
                seen.append(series.model)
        return seen

    def series_for_model(self, model: str) -> list[HistorySeries]:
        """某模型的全部系列（模型总览 + 各思考强度），latest: 优先。"""
        return [s for s in self.preferred_series() if s.model == model]

def split_key(key: str) -> tuple[str, Optional[str]]:
    """将系列名拆成 (model, effort)。effort 可为 None。"""
    if key.startswith(LATEST_PREFIX):
        key = key[len(LATEST_PREFIX):]
    if "@" in key:
        model, effort = key.rsplit("@", 1)
        return model, effort or None
    return key, None


def parse_iq_history(
    payload: Any,
    hours: int = 72,
    url: str = "",
) -> HistorySnapshot:
    """校验并解析 iq-history 响应。

    :raises RadarParseError: 结构缺失 / 字段异常。
    """
    source = url or "https://api.codexradar.com/api/v1/iq-history"
    if hours <= 0:
        hours = 72

    if not isinstance(payload, dict):
        raise RadarParseError(
            source, f"顶层结构应为 JSON 对象，实际为 {type(payload).__name__}"
        )
    if not payload:
        raise RadarParseError(source, "返回为空（没有任何模型系列）")

    series: list[HistorySeries] = []
    all_ts: list[datetime] = []

    for key, raw_points in payload.items():
        if not isinstance(raw_points, list):
            raise RadarParseError(source, f"系列 {key!r} 的值应为列表，实际为 {type(raw_points).__name__}")

        points: list[HistoryPoint] = []
        for item in raw_points:
            if not isinstance(item, dict) or "ts" not in item:
                raise RadarParseError(source, f"系列 {key!r} 中存在缺少 ts 字段的数据点")
            ts_text = str(item["ts"])
            parsed = _parse_ts(ts_text)
            if parsed is None:
                raise RadarParseError(source, f"系列 {key!r} 中存在无法解析的时间戳 {ts_text!r}")
            all_ts.append(parsed)
            n = item.get("n")
            n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
            points.append(
                HistoryPoint(
                    ts=ts_text,
                    score=_finite_number(item.get("score")),
                    n=max(0, n),
                )
            )
        points.sort(key=lambda p: p.ts)
        model, effort = split_key(str(key))
        series.append(
            HistorySeries(key=str(key), model=model, effort=effort, points=points)
        )

    if not all_ts:
        raise RadarParseError(source, "所有系列均为空（没有任何数据点）")

    window_end = max(all_ts)
    cutoff = window_end - timedelta(hours=hours)

    for s in series:
        s.points = [p for p in s.points if _parse_ts(p.ts) and _parse_ts(p.ts) >= cutoff]

    return HistorySnapshot(
        hours=hours,
        series=series,
        window_end=window_end.isoformat(),
        window_start=cutoff.isoformat(),
        updated_at=window_end.isoformat(),
        source_url=source,
    )
