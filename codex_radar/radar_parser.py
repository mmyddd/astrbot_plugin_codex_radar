"""解析 codexradar.com 的智力效率数据（/api/intelligence-efficiency）。

聚合算法与站点前端代码保持一致（端口自 codexradar.com 页面的 aggregateTable）：
- IQ（分数）= 通过题数 / 有效题数 * 150（满分 150，100% 通过 -> 150）
- 耗时 = 每题最新一次有效运行 duration_sec 的均值（分钟）
- 花费 = 每题最新一次有效运行 actual_cost_usd 的均值（USD）；
  ultra 档仅统计 cost_complete=true 的样本
- 综合成本指数 = price * (minutes / 10) ^ ln(2.5)/ln(1.35) * 100，
  再按全图最高值归一为 100（对应站点「综合成本 × IQ」图的公式）
- 数据更新时间 = baseline_generated_at（数据生成时间）与最新判分时间
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import RadarParseError

# 站点前端：combinedCostWeight = Math.log(2.5) / Math.log(1.35)
COMBINED_COST_WEIGHT = math.log(2.5) / math.log(1.35)
# 站点前端：IQ = pass rate * 150（100% -> 150, 0% -> 0）
IQ_SCALE = 150.0


def finite_number(value: Any) -> Optional[float]:
    """与站点前端 finiteNumber 一致的数值解析（bool/None/'' -> None）。"""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _running_mean(current: Optional[float], count: int, value: float) -> float:
    """增量计算均值：new = (old * count + value) / (count + 1)。"""
    return ((current or 0.0) * count + value) / (count + 1)


@dataclass
class RadarPoint:
    """单个「模型 × 思考强度」组合的聚合结果。"""

    model: str
    effort: str
    passed: int = 0
    valid_tasks: int = 0
    iq: Optional[float] = None
    average_price_usd: Optional[float] = None
    price_samples: int = 0
    average_minutes: Optional[float] = None
    duration_samples: int = 0
    incomplete_cost_samples: int = 0
    total_runs: int = 0
    latest_graded_at: Optional[str] = None
    average_agent_steps: Optional[float] = None
    average_total_tokens: Optional[float] = None
    cache_hit_rate: Optional[float] = None
    raw_combined_cost: Optional[float] = None
    combined_cost_index: Optional[float] = None

    @property
    def key(self) -> str:
        return f"{self.model}@{self.effort}"

    @property
    def pass_rate(self) -> Optional[float]:
        if self.valid_tasks <= 0:
            return None
        return self.passed / self.valid_tasks * 100.0


@dataclass
class RadarSnapshot:
    """一次智力效率快照。"""

    schema: int = 0
    combos_count: int = 0
    tasks_count: int = 0
    points: list[RadarPoint] = field(default_factory=list)
    baseline_generated_at: Optional[str] = None
    discrimination_generated_at: Optional[str] = None
    source_updated_at: Optional[str] = None
    token_pricing: Optional[dict] = None
    tier_windows_usd: Optional[dict] = None
    source_url: str = ""

    @property
    def updated_at(self) -> Optional[str]:
        """对外展示的数据更新时间：优先 baseline_generated_at。"""
        return self.baseline_generated_at or self.source_updated_at

    def points_by_model(self) -> list[tuple[str, list[RadarPoint]]]:
        """按模型分组（保持接口给出的顺序），返回 [(model, [points])]。"""
        grouped: list[tuple[str, list[RadarPoint]]] = []
        index: dict[str, int] = {}
        for point in self.points:
            if point.model not in index:
                index[point.model] = len(grouped)
                grouped.append((point.model, []))
            grouped[index[point.model]][1].append(point)
        return grouped


def _parse_iso(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def parse_intelligence_efficiency(payload: Any, url: str = "") -> RadarSnapshot:
    """校验并解析 /api/intelligence-efficiency 响应。

    :raises RadarParseError: 结构缺失 / 字段异常 / schema 不匹配。
    """
    source = url or "https://codexradar.com/api/intelligence-efficiency"

    if not isinstance(payload, dict):
        raise RadarParseError(
            source, f"顶层结构应为 JSON 对象，实际为 {type(payload).__name__}"
        )

    schema = payload.get("schema")
    if schema != 1:
        raise RadarParseError(source, f"不支持的 schema={schema!r}（预期 1，站点可能已改版）")

    combos = payload.get("combos")
    tasks = payload.get("tasks")
    cells = payload.get("cells")

    if not isinstance(combos, list) or not combos:
        raise RadarParseError(source, "缺少非空 combos 列表（模型×思考强度组合）")
    if not isinstance(tasks, list) or not tasks:
        raise RadarParseError(source, "缺少非空 tasks 列表（评测题目集）")
    if not isinstance(cells, dict) or not cells:
        raise RadarParseError(source, "缺少非空 cells 映射（题目×模型×强度的运行明细）")

    points: list[RadarPoint] = []
    source_updated_at: Optional[str] = None

    for combo in combos:
        if not isinstance(combo, dict):
            raise RadarParseError(source, f"combos 中存在非对象元素：{combo!r}")
        model = str(combo.get("model") or "").strip()
        effort = str(combo.get("effort") or "").strip()
        if not model or not effort:
            raise RadarParseError(source, f"combos 中存在缺少 model/effort 的元素：{combo!r}")

        point = RadarPoint(model=model, effort=effort)
        for task in tasks:
            if not isinstance(task, dict) or task.get("id") is None:
                continue
            task_id = task["id"]
            cell = cells.get(f"{task_id}|{model}|{effort}")
            runners = cell.get("ran_by") if isinstance(cell, dict) else None
            if not isinstance(runners, list):
                continue

            point.total_runs += sum(
                1 for item in runners if isinstance(item, dict)
            )
            runner = runners[0] if runners and isinstance(runners[0], dict) else None
            if not runner:
                continue

            # 分数：最新一次有效结果
            if isinstance(runner.get("passed"), bool):
                point.valid_tasks += 1
                if runner["passed"]:
                    point.passed += 1

            # 耗时（分钟）
            duration = finite_number(runner.get("duration_sec"))
            if duration is not None and duration > 0:
                point.average_minutes = (
                    (point.average_minutes or 0) * point.duration_samples + duration / 60.0
                ) / (point.duration_samples + 1)
                point.duration_samples += 1

            # 花费（USD）；ultra 档仅统计 cost_complete=true
            price = finite_number(runner.get("actual_cost_usd"))
            if price is not None and price >= 0:
                if effort != "ultra" or runner.get("cost_complete") is True:
                    point.average_price_usd = (
                        (point.average_price_usd or 0) * point.price_samples + price
                    ) / (point.price_samples + 1)
                    point.price_samples += 1
                else:
                    point.incomplete_cost_samples += 1

            # 最新判分时间
            graded_at = _parse_iso(runner.get("graded_at"))
            if graded_at and (
                point.latest_graded_at is None or graded_at > point.latest_graded_at
            ):
                point.latest_graded_at = graded_at
            if graded_at and (source_updated_at is None or graded_at > source_updated_at):
                source_updated_at = graded_at

            # 详细指标（agent steps / tokens / cache 命中率）
            agent_steps = finite_number(runner.get("n_agent_steps"))
            if agent_steps is not None and agent_steps >= 0:
                point.average_agent_steps = _running_mean(
                    point.average_agent_steps, 1, agent_steps
                )
            input_tokens = finite_number(runner.get("n_input_tokens"))
            output_tokens = finite_number(runner.get("n_output_tokens"))
            if input_tokens is not None or output_tokens is not None:
                total = max(0.0, input_tokens or 0.0) + max(0.0, output_tokens or 0.0)
                point.average_total_tokens = _running_mean(
                    point.average_total_tokens, 1, total
                )
            cache_tokens = finite_number(runner.get("n_cache_tokens"))
            if (
                input_tokens is not None
                and input_tokens > 0
                and cache_tokens is not None
                and cache_tokens >= 0
            ):
                point.cache_hit_rate = cache_tokens / input_tokens

        if point.valid_tasks == 0:
            continue
        point.iq = point.passed / point.valid_tasks * IQ_SCALE
        points.append(point)

    if not points:
        raise RadarParseError(source, "没有任何模型×思考强度组合拥有有效判分数据")

    # 综合成本指数（与站点前端公式一致）
    for point in points:
        if (
            point.average_price_usd is not None
            and point.average_price_usd > 0
            and point.average_minutes is not None
            and point.average_minutes > 0
        ):
            point.raw_combined_cost = (
                point.average_price_usd
                * math.pow(point.average_minutes / 10.0, COMBINED_COST_WEIGHT)
                * 100.0
            )
    max_raw = max((p.raw_combined_cost or 0.0) for p in points)
    for point in points:
        if point.raw_combined_cost is not None and max_raw > 0:
            point.combined_cost_index = point.raw_combined_cost / max_raw * 100.0

    return RadarSnapshot(
        schema=schema,
        combos_count=len(combos),
        tasks_count=len(tasks),
        points=points,
        baseline_generated_at=_parse_iso(payload.get("baseline_generated_at")),
        discrimination_generated_at=_parse_iso(payload.get("discrimination_generated_at")),
        source_updated_at=source_updated_at,
        token_pricing=payload.get("token_pricing"),
        tier_windows_usd=payload.get("tier_windows_usd"),
        source_url=source,
    )
