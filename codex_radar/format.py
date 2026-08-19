"""聊天平台友好的文本格式化（含 ASCII 迷你走势图）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from .history_parser import HistorySnapshot, HistorySeries
from .radar_parser import RadarSnapshot

EFFORT_ZH = {
    "off": "关",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "很高",
    "max": "最大",
    "ultra": "极限",
}

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def fmt_time(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return str(ts)


def _f(value: Optional[float], digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def sparkline(values: Sequence[Optional[float]], width: int = 20) -> str:
    """Unicode 迷你走势图；空值跳过，全空返回占位符。"""
    nums = [v for v in values if v is not None]
    if not nums:
        return "（无数据）"
    lo, hi = min(nums), max(nums)
    span = hi - lo
    if span <= 0:
        span = 1.0
    step = max(1, len(nums) // width)
    sampled = nums[::step][:width]
    return "".join(
        SPARK_CHARS[min(len(SPARK_CHARS) - 1, int((v - lo) / span * (len(SPARK_CHARS) - 1)))]
        for v in sampled
    )


def format_radar_text(snapshot: RadarSnapshot) -> str:
    """降智雷达的完整文本：每个模型 × 每种思考强度。"""
    lines: list[str] = []
    lines.append("📡 Codex 智力效率雷达")
    lines.append(f"更新时间：{fmt_time(snapshot.updated_at)}")
    if snapshot.source_updated_at and snapshot.source_updated_at != snapshot.updated_at:
        lines.append(f"最新判分：{fmt_time(snapshot.source_updated_at)}")
    lines.append(f"来源：{snapshot.source_url or 'codexradar.com'}")

    for model, points in snapshot.points_by_model():
        lines.append(f"\n▍{model}")
        for p in points:
            effort = EFFORT_ZH.get(p.effort, p.effort)
            iq = _f(p.iq, 1)
            minutes = _f(p.average_minutes, 1)
            price = _f(p.average_price_usd, 3)
            # 综合成本指数：>=10 显示整数，否则保留一位小数避免丢精度
            cost = (
                _f(p.combined_cost_index, 1)
                if p.combined_cost_index is not None and p.combined_cost_index < 10
                else _f(p.combined_cost_index, 0)
            )
            lines.append(
                f"  {effort:<4} IQ {iq:<6} 耗时 {minutes:>7}分  花费 ${price:>7}  综合成本 {cost:>5}"
            )

    lines.append(
        "\n综合成本公式（站点口径）：2.5×价格≈1.35×速度，图中最高综合成本归一为 100。"
    )
    return "\n".join(lines)


def format_history_text(snapshot: HistorySnapshot, model: Optional[str] = None) -> str:
    """雷达历史的文本：模型级走势 + 各强度明细。

    model 为 None 时列出全部模型；指定模型时只输出该模型
    （模型总览 + 每个思考强度的走势图）。
    """
    lines: list[str] = []
    title = f"📈 Codex IQ 历史（{snapshot.hours}h）"
    if model:
        title += f" · {model}"
    lines.append(title)
    lines.append(
        f"时间范围：{fmt_time(snapshot.window_start)} ~ {fmt_time(snapshot.window_end)}"
    )
    lines.append(f"更新时间：{fmt_time(snapshot.updated_at)}")
    lines.append(f"来源：{snapshot.source_url or 'api.codexradar.com/api/v1/iq-history'}")

    preferred = snapshot.preferred_series()

    if model:
        model_series = [s for s in preferred if s.effort is None and s.model == model]
        effort_series = [s for s in preferred if s.effort is not None and s.model == model]
    else:
        model_series = [s for s in preferred if s.effort is None]
        effort_series = [s for s in preferred if s.effort is not None]

    def _overview_line(s: HistorySeries) -> str:
        latest = s.latest()
        if latest is None or latest.score is None:
            return f"  {s.model:<16} 暂无数据"
        delta = ""
        first = s.first()
        if first is not None and first.score is not None:
            diff = latest.score - first.score
            delta = f"（{diff:+.1f}）"
        scores = [p.score for p in s.points]
        return f"  {s.model:<16} {latest.score:6.1f}{delta:<10} {sparkline(scores)}"

    if model_series and not model:
        # 指定模型时只输出各思考等级历史，不输出模型平均（总览）
        lines.append("\n【模型总览】")
        for s in model_series:
            lines.append(_overview_line(s))

    if effort_series:
        if model:
            lines.append("\n【各思考强度】")
            for s in effort_series:
                latest = s.latest()
                if latest is None or latest.score is None:
                    lines.append(f"  {s.effort:<8} 暂无数据")
                    continue
                delta = ""
                first = s.first()
                if first is not None and first.score is not None:
                    diff = latest.score - first.score
                    delta = f"（{diff:+.1f}）"
                scores = [p.score for p in s.points]
                lines.append(
                    f"  {s.effort:<8} {latest.score:6.1f}{delta:<10} {sparkline(scores)}"
                )
        else:
            lines.append("\n【各思考强度最新 IQ】")
            by_model: dict[str, list[HistorySeries]] = {}
            order: list[str] = []
            for s in effort_series:
                if s.model not in by_model:
                    by_model[s.model] = []
                    order.append(s.model)
                by_model[s.model].append(s)
            for m in order:
                bits: list[str] = []
                for s in by_model[m]:
                    latest = s.latest()
                    if latest is None or latest.score is None:
                        bits.append(f"{s.effort}=—")
                    else:
                        bits.append(f"{s.effort}={latest.score:.1f}")
                lines.append(f"  {m}: " + "  ".join(bits))

    lines.append("\n曲线图为 72h 每小时 IQ 分数（0-150），空值表示该时段无有效样本。")
    return "\n".join(lines)
