"""SVG 图表生成（纯函数，无第三方依赖）。

生成两类图：
1. 智力效率散点图：综合成本指数（x） × IQ 分数（y），对应站点「综合成本 × IQ」图
2. IQ 历史曲线图：72 小时 IQ 曲线（每模型一条线）

SVG 由 AstrBot 的 html_render（Playwright）渲染为 PNG；测试中可直接校验 XML。
"""

from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import urlsplit
from typing import Optional, Sequence

from .history_parser import HistorySeries
from .radar_parser import RadarPoint

WIDTH = 960
HEIGHT = 640

# 站点前端的模型配色（modelInfo）
MODEL_COLORS = {
    "gpt-5.6-sol": "#eab308",
    "gpt-5.6-terra": "#3b82f6",
    "gpt-5.6-luna": "#94a3b8",
    "gpt-5.5": "#00b8d9",
    "deepseek-v4-flash": "#8b5cf6",
}
FALLBACK_COLORS = [
    "#f0e442", "#cc79a7", "#56b4e9", "#00c98d", "#e69f00",
    "#a78bfa", "#ff7f50", "#fb7185", "#60a5fa", "#c4b5fd",
]

# 站点前端（deng.codexradar.com）的思考强度配色 effortColors
EFFORT_COLORS = {
    "ultra": "#f0e442",
    "max": "#cc79a7",
    "xhigh": "#56b4e9",
    "high": "#00c98d",
    "medium": "#a78bfa",
    "low": "#e69f00",
}


def effort_color(effort: Optional[str], seen: Optional[dict[str, int]] = None) -> str:
    """思考强度配色：站点 effortColors；未知强度回退到轮换色。"""
    if effort in EFFORT_COLORS:
        return EFFORT_COLORS[effort]
    if seen is not None:
        idx = seen.setdefault(effort or "?", len(seen))
        return FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]
    return FALLBACK_COLORS[abs(hash(effort or "?")) % len(FALLBACK_COLORS)]


def model_color(model: str, seen: Optional[dict[str, int]] = None) -> str:
    if model in MODEL_COLORS:
        return MODEL_COLORS[model]
    if seen is not None:
        idx = seen.setdefault(model, len(seen))
        return FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]
    return FALLBACK_COLORS[abs(hash(model)) % len(FALLBACK_COLORS)]


def short_model(model: str) -> str:
    for prefix in ("gpt-5.6-", "gpt-", "deepseek-"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model

# 站点前端 effortOrder：决定同模型内各强度的连线顺序
EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4, "ultra": 5}


def nice_max(value: float) -> float:
    """站点前端 niceMax：把最大值取整到 1/1.2/1.5/2/2.5/3/4/5/6/8/10×10^n。"""
    import math as _math
    raw = max(value if value else 0.0, 1.0)
    power = 10 ** _math.floor(_math.log10(raw))
    fraction = raw / power
    stops = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]
    stop = next((s for s in stops if fraction <= s), 10)
    return stop * power


def log_share(value: float, low: float, high: float) -> float:
    """站点前端 logShare：对数刻度归一化位置。"""
    import math as _math
    if high <= low:
        return 0.5
    return _math.log(value / low) / _math.log(high / low)


def cost_tick_text(value: float) -> str:
    """站点前端 metric.tick 的刻度文本格式。"""
    if value >= 10:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.1f}"
    if value >= 0.01:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _star_point(index: int) -> tuple[float, float]:
    """站点前端 starPoints：10 角星（外径 6 / 内径 2.8）。"""
    import math as _math
    radius = 2.8 if index % 2 else 6.0
    angle = -_math.pi / 2 + index * _math.pi / 5
    return _math.cos(angle) * radius, _math.sin(angle) * radius


def effort_shape_svg(effort: str, x: float, y: float, color: str) -> str:
    """站点前端 pointShape：不同思考强度用不同图形。"""
    if effort == "medium":
        return (f'<polygon points="{x:.1f},{y - 6:.1f} {x - 6:.1f},{y + 5:.1f} '
                f'{x + 6:.1f},{y + 5:.1f}" fill="{color}"/>')
    if effort == "high":
        return f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10" rx="1" fill="{color}"/>'
    if effort == "xhigh":
        return (f'<polygon points="{x:.1f},{y - 6:.1f} {x + 6:.1f},{y:.1f} '
                f'{x:.1f},{y + 6:.1f} {x - 6:.1f},{y:.1f}" fill="{color}"/>')
    if effort == "max":
        return (f'<polygon points="{x - 5.5:.1f},{y - 3.2:.1f} {x:.1f},{y - 6.2:.1f} '
                f'{x + 5.5:.1f},{y - 3.2:.1f} {x + 5.5:.1f},{y + 3.2:.1f} '
                f'{x:.1f},{y + 6.2:.1f} {x - 5.5:.1f},{y + 3.2:.1f}" fill="{color}"/>')
    if effort == "ultra":
        star = " ".join(
            f"{x + dx:.1f},{y + dy:.1f}" for dx, dy in (_star_point(i) for i in range(10))
        )
        return f'<polygon points="{star}" fill="{color}"/>'
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>'


def scatter_layout(points: Sequence[RadarPoint]):
    """站点同款 x 轴（对数 + 断轴）刻度映射。"""
    values = sorted(
        p.combined_cost_index for p in points if p.combined_cost_index is not None
    )
    x_min = values[0]
    x_max = values[-1]
    unique = sorted(set(values))
    second = unique[1] if len(unique) > 1 else None
    broken = second is not None and second / x_min >= 4
    share = 0.14  # 站点桌面端断轴占比

    def x_for(value: float, left: float, plot_w: float) -> float:
        if not broken:
            return left + log_share(value, x_min, x_max) * plot_w
        if value < second:
            return left
        return (
            left
            + plot_w * share
            + log_share(value, second, x_max) * plot_w * (1 - share)
        )

    return {
        "x_min": x_min,
        "x_max": x_max,
        "second": second,
        "broken": broken,
        "share": share,
        "x_for": x_for,
    }

def _esc(value: object) -> str:
    return html.escape(str(value))


def _fmt_time(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return ts


def efficiency_scatter_svg(
    points: Sequence[RadarPoint],
    *,
    updated_at: Optional[str] = None,
    source_url: str = "",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> str:
    """综合成本 × IQ 散点图（站点同款：对数刻度 + 断轴 + 每模型折线）。

    移植自 codexradar.com 前端 chartHtml：
    - x 轴为对数刻度 logShare(v, min, max)；当第二小值 >= 4× 最小值时启用断轴
    - 同一模型的各思考强度点按 effortOrder 连线，点形状区分强度
    """
    margin = {"left": 78, "right": 30, "top": 60, "bottom": 86}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    # 站点：filter(chart_x > 0 && isFinite(iq))
    pts = [p for p in points if p.iq is not None and p.combined_cost_index is not None]
    layout = scatter_layout(pts)
    x_for = layout["x_for"]
    broken = layout["broken"]
    second = layout["second"]

    # 站点：yMax = min(150, max(20, niceMax(max(iq))))
    y_max = min(150.0, max(20.0, nice_max(max(p.iq for p in pts))))
    y_ticks = 6

    def y_of(iq: float) -> float:
        return margin["top"] + (1 - iq / y_max) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,-apple-system,Segoe UI,'
        'Microsoft YaHei,sans-serif">'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    parts.append(
        f'<text x="{width / 2}" y="34" text-anchor="middle" font-size="24" font-weight="700" '
        'fill="#111827">综合成本 × IQ（智力效率）</text>'
    )
    # 站点同款提示：越靠左上越高效
    parts.append(
        f'<text x="{width - margin["right"] - 10}" y="34" text-anchor="end" font-size="14" '
        'fill="#6b7280">↖ 越靠左上越高效</text>'
    )

    # y 轴网格与刻度（站点：6 步，取整）
    for i in range(y_ticks + 1):
        iq = y_max * i / y_ticks
        y = y_of(iq)
        parts.append(
            f'<line x1="{margin["left"]}" y1="{y:.1f}" x2="{width - margin["right"]}" '
            f'y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin["left"] - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#6b7280">{round(iq)}</text>'
        )

    # x 轴：对数刻度 tick（站点：tickMin * (xMax/tickMin)^share，过滤相邻 <1.08）
    tick_min = second if broken else layout["x_min"]
    x_max = layout["x_max"]
    shares = [0, 0.2, 0.4, 0.6, 0.8, 1]
    raw_ticks = ([layout["x_min"]] if broken else []) + [
        tick_min * (x_max / tick_min) ** sh for sh in shares
    ]
    ticks: list[float] = []
    for value in raw_ticks:
        if not ticks or value / ticks[-1] > 1.08:
            ticks.append(value)
    for value in ticks:
        x = x_for(value, margin["left"], plot_w)
        parts.append(
            f'<line x1="{x:.1f}" y1="{margin["top"]}" x2="{x:.1f}" '
            f'y2="{height - margin["bottom"]}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{height - margin["bottom"] + 18}" text-anchor="middle" '
            f'font-size="12" fill="#6b7280">{_esc(cost_tick_text(value))}</text>'
        )

    # 断轴标记（站点：轴上两条斜线）
    if broken:
        x = margin["left"] + plot_w * layout["share"] / 2
        y = height - margin["bottom"]
        parts.append(
            f'<path d="M{x - 6:.1f} {y + 4:.1f}l5 -8 M{x:.1f} {y + 4:.1f}l5 -8" '
            'stroke="#9ca3af" stroke-width="2" fill="none"/>'
        )

    # 坐标轴标签
    parts.append(
        f'<text x="{width / 2}" y="{height - margin["bottom"] + 58}" text-anchor="middle" font-size="14" '
        'fill="#374151">相对综合成本指数（对数刻度，最高归一为 100）</text>'
    )
    parts.append(
        f'<text x="22" y="{height / 2}" text-anchor="middle" font-size="14" fill="#374151" '
        f'transform="rotate(-90 22 {height / 2})">IQ 分数（0-150）</text>'
    )

    # 系列：每模型折线（effort 按 effortOrder 排序）+ 强度形状点 + 标签
    seen_models: dict[str, int] = {}
    models_in_order: list[str] = []
    by_model: dict[str, list[RadarPoint]] = {}
    for point in pts:
        if point.model not in by_model:
            by_model[point.model] = []
            models_in_order.append(point.model)
        by_model[point.model].append(point)

    for model in models_in_order:
        color = model_color(model, seen_models)
        model_points = sorted(
            by_model[model],
            key=lambda p: EFFORT_ORDER.get(p.effort, -1),
        )
        coords = [
            (
                x_for(p.combined_cost_index, margin["left"], plot_w),
                y_of(p.iq),
            )
            for p in model_points
        ]
        if len(coords) > 1:
            path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in coords)
            parts.append(
                f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
                'stroke-linejoin="round" stroke-linecap="round" opacity="0.75"/>'
            )
        for index, (point, (x, y)) in enumerate(zip(model_points, coords)):
            parts.append(effort_shape_svg(point.effort, x, y, color))
            order = models_in_order.index(model)
            dy = 17 if (order + index) % 2 else -9
            parts.append(
                f'<text x="{x:.1f}" y="{y + dy:.1f}" text-anchor="middle" font-size="11" '
                f'fill="#374151">{_esc(point.effort)}</text>'
            )

    # 图例（模型颜色 + 站点风格短名，单行横排，位置保持原布局；完整名放入 <title> 悬浮提示）
    legend_y = height - margin["bottom"] + 34
    legend_x = margin["left"] + 8
    for model in models_in_order:
        color = model_color(model, seen_models)
        label = short_model(model)
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="12" height="12" rx="2" fill="{color}">'
            f'<title>{_esc(model)}</title></rect>'
        )
        parts.append(
            f'<text x="{legend_x + 18}" y="{legend_y}" font-size="12" fill="#374151">'
            f'{_esc(label)}</text>'
        )
        legend_x += 30 + len(label) * 13 + 16

    # 页脚
    footer = f"更新：{_fmt_time(updated_at)}"
    if source_url:
        host = urlsplit(source_url).netloc or source_url
        footer += f"　来源：{host}"
    footer += "　公式：按 2.5×价格≈1.35×速度 折算，最高综合成本归一为 100"
    parts.append(
        f'<text x="{width / 2}" y="{height - 12}" text-anchor="middle" font-size="12" '
        f'fill="#9ca3af">{_esc(footer)}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


def iq_history_svg(
    series: Sequence[HistorySeries],
    *,
    hours: int = 72,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
    updated_at: Optional[str] = None,
    width: int = 1240,
    height: int = 460,
    effort_colors: bool = False,
) -> str:
    """72 小时 IQ 历史曲线（每模型一条线）。"""
    margin = {"left": 62, "right": 300, "top": 56, "bottom": 78}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    all_scores = [
        p.score for s in series for p in s.points if p.score is not None
    ]
    # 动态 y 轴：按数据实际范围 + 15% padding 缩放（最小跨度 10），
    # 避免固定 0-150 区间把曲线波动压平；无数据时回退 0-150
    if all_scores:
        y_lo, y_hi = min(all_scores), max(all_scores)
        span = y_hi - y_lo
        if span < 10.0:
            y_hi = y_lo + 10.0
            span = 10.0
        pad = span * 0.15
        y_lo = max(0.0, y_lo - pad)
        y_hi = y_hi + pad
    else:
        y_lo, y_hi = 0.0, 150.0

    def parse_dt(ts: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    t0 = parse_dt(window_start or "")
    t1 = parse_dt(window_end or "")
    if t0 is None or t1 is None or t1 <= t0:
        now = datetime.now().astimezone()
        t1 = now
        t0 = now.replace(minute=0, second=0, microsecond=0)

    span = (t1 - t0).total_seconds()

    def x_of(ts: str) -> float:
        t = parse_dt(ts)
        if t is None:
            return margin["left"]
        ratio = (t - t0).total_seconds() / span if span > 0 else 0.0
        return margin["left"] + max(0.0, min(1.0, ratio)) * plot_w

    def y_of(score: float) -> float:
        return margin["top"] + (1 - (score - y_lo) / (y_hi - y_lo)) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,-apple-system,Segoe UI,'
        'Microsoft YaHei,sans-serif">'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    parts.append(
        f'<text x="{width / 2}" y="34" text-anchor="middle" font-size="24" font-weight="700" '
        f'fill="#111827">IQ 历史曲线（{hours}h）</text>'
    )

    # 网格与坐标轴
    y_ticks = 6
    for i in range(y_ticks + 1):
        score = y_lo + (y_hi - y_lo) * i / y_ticks
        y = y_of(score)
        parts.append(
            f'<line x1="{margin["left"]}" y1="{y:.1f}" x2="{width - margin["right"]}" '
            f'y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin["left"] - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#6b7280">{score:.0f}</text>'
        )
    x_ticks = 6
    for i in range(x_ticks + 1):
        t = t0 + (t1 - t0) * i / x_ticks
        x = margin["left"] + plot_w * i / x_ticks
        parts.append(
            f'<line x1="{x:.1f}" y1="{margin["top"]}" x2="{x:.1f}" '
            f'y2="{height - margin["bottom"]}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{height - margin["bottom"] + 18}" text-anchor="middle" '
            f'font-size="12" fill="#6b7280">{t.strftime("%m-%d %H:%M")}</text>'
        )

    parts.append(
        f'<text x="{width / 2}" y="{height - 44}" text-anchor="middle" font-size="14" '
        'fill="#374151">IQ 分数（动态区间，波动放大显示）</text>'
    )

    # 曲线：空值断线
    seen_models: dict[str, int] = {}
    for s in series:
        # 筛选单模型时：模型总览用模型色，各思考强度用站点 effortColors
        color = (
            effort_color(s.effort)
            if effort_colors and s.effort is not None
            else model_color(s.model, seen_models)
        )
        # 单模型视图：模型总览线加粗，避免与强度色（如 sol 黄 vs low 橙黄）混淆
        overview = effort_colors and s.effort is None
        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for p in s.points:
            if p.score is None:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append((x_of(p.ts), y_of(p.score)))
        if current:
            segments.append(current)
        for seg in segments:
            if len(seg) < 2:
                continue
            d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in seg)
            parts.append(
                f'<path d="{d}" fill="none" stroke="{color}" '
                f'stroke-width="{3.5 if overview else 2.5}" '
                'stroke-linejoin="round" stroke-linecap="round"/>'
            )
        latest = s.latest()
        if latest is not None and latest.score is not None and segments:
            x, y = segments[-1][-1]
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')

    # 图例（右侧独立栏，位于绘图区之外，不与曲线重叠）
    legend_x = width - margin["right"] + 22
    legend_y = margin["top"] + 6
    legend_max_x = width - 8
    for s in series:
        color = (
            effort_color(s.effort)
            if effort_colors and s.effort is not None
            else model_color(s.model, seen_models)
        )
        if s.effort is None:
            label = s.model
        elif effort_colors:
            label = s.effort  # 单模型视图：图例直接标思考强度
        else:
            label = f"{s.model}@{s.effort}"
        latest = s.latest()
        latest_txt = "" if latest is None or latest.score is None else f"  {latest.score:.1f}"
        item_w = 18 + (len(label) + len(latest_txt)) * 11 + 8
        if legend_x + item_w > legend_max_x:
            legend_x = width - margin["right"] + 22
            legend_y += 17
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="12" height="12" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 18}" y="{legend_y}" font-size="11" fill="#374151">'
            f'{_esc(label + latest_txt)}</text>'
        )
        legend_x += item_w

    # 页脚
    footer = f"时间范围：{_fmt_time(window_start)} ~ {_fmt_time(window_end)}"
    if updated_at:
        footer += f"　更新：{_fmt_time(updated_at)}"
    parts.append(
        f'<text x="{width / 2}" y="{height - 12}" text-anchor="middle" font-size="12" '
        f'fill="#9ca3af">{_esc(footer)}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)
