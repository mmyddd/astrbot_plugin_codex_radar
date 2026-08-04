"""Pillow 绘制的 PNG 图表（降级方案）。

当 AstrBot 的 html_render（Playwright）不可用时，用 Pillow 直接绘制
同构的两类图表。Pillow 是 AstrBot 运行时已有的依赖。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, Sequence

from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

from .chart_svg import MODEL_COLORS, model_color, short_model
from .chart_svg import (
    EFFORT_ORDER,
    _star_point,
    cost_tick_text,
    effort_color,
    nice_max,
    scatter_layout,
)
from .history_parser import HistorySeries
from .radar_parser import RadarPoint

_CANDIDATE_FONTS = [
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_font_cache: dict[tuple[str, int], ImageFont.ImageFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = ("bold" if bold else "regular", size)
    if key in _font_cache:
        return _font_cache[key]
    for path in _CANDIDATE_FONTS:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _fmt_time(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return ts


def _prepare_image(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), "white")
    return image, ImageDraw.Draw(image)


def efficiency_scatter_png(
    points: Sequence[RadarPoint],
    out_path: str,
    *,
    updated_at: Optional[str] = None,
    source_url: str = "",
    width: int = 960,
    height: int = 640,
) -> str:
    """综合成本 × IQ 散点图（PNG，站点同款：对数刻度 + 断轴 + 每模型折线）。"""

    image, draw = _prepare_image(width, height)
    margin = {"left": 78, "right": 30, "top": 60, "bottom": 86}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    pts = [p for p in points if p.iq is not None and p.combined_cost_index is not None]
    layout = scatter_layout(pts)
    x_for = layout["x_for"]
    broken = layout["broken"]
    second = layout["second"]

    y_max = min(150.0, max(20.0, nice_max(max(p.iq for p in pts))))
    y_ticks = 6

    def y_of(iq: float) -> float:
        return margin["top"] + (1 - iq / y_max) * plot_h

    draw.text((width / 2 - 150, 14), "综合成本 × IQ（智力效率）", font=_font(24, bold=True), fill="#111827")
    draw.text(
        (width - margin["right"] - 10, 34),
        "↖ 越靠左上越高效",
        font=_font(14),
        fill="#6b7280",
        anchor="ra",
    )

    for i in range(y_ticks + 1):
        iq = y_max * i / y_ticks
        y = y_of(iq)
        draw.line([(margin["left"], y), (width - margin["right"], y)], fill="#e5e7eb", width=1)
        draw.text((margin["left"] - 58, y - 7), f"{round(iq)}", font=_font(12), fill="#6b7280")

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
        draw.line([(x, margin["top"]), (x, height - margin["bottom"])], fill="#e5e7eb", width=1)
        draw.text((x - 24, height - margin["bottom"] + 8), cost_tick_text(value), font=_font(12), fill="#6b7280")

    if broken:
        x = margin["left"] + plot_w * layout["share"] / 2
        y = height - margin["bottom"]
        draw.line([(x - 6, y + 4), (x - 1, y - 4)], fill="#9ca3af", width=2)
        draw.line([(x, y + 4), (x + 5, y - 4)], fill="#9ca3af", width=2)

    draw.text((width / 2 - 170, height - margin["bottom"] + 48), "相对综合成本指数（对数刻度，最高归一为 100）", font=_font(14), fill="#374151")
    draw.text((20, 24), "IQ 分数（0-150）", font=_font(14), fill="#374151")

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
            draw.line(coords, fill=color, width=2, joint="curve")
        for index, (point, (x, y)) in enumerate(zip(model_points, coords)):
            _draw_effort_shape(draw, point.effort, x, y, color)
            order = models_in_order.index(model)
            dy = 17 if (order + index) % 2 else -9
            draw.text((x - 12, y + dy - 7), point.effort, font=_font(11), fill="#374151")

    legend_y = height - margin["bottom"] + 34
    legend_x = margin["left"] + 8
    for model in models_in_order:
        color = model_color(model, seen_models)
        label = short_model(model)
        draw.rectangle([legend_x, legend_y - 10, legend_x + 12, legend_y + 2], fill=color)
        draw.text((legend_x + 13, legend_y - 10), label, font=_font(11), fill="#374151")
        legend_x += 20 + len(label) * 11 + 6

    footer = f"更新：{_fmt_time(updated_at)}"
    if source_url:
        host = urlsplit(source_url).netloc or source_url
        footer += f"  来源：{host}"
    draw.text((width / 2, height - 12), footer, font=_font(12), fill="#9ca3af", anchor="mm")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    image.save(out_path, "PNG")
    return out_path


def _draw_effort_shape(
    draw: ImageDraw.ImageDraw,
    effort: str,
    x: float,
    y: float,
    color: str,
) -> None:
    """站点前端 pointShape 的 Pillow 实现。"""
    if effort == "medium":
        draw.polygon([(x, y - 6), (x - 6, y + 5), (x + 6, y + 5)], fill=color)
    elif effort == "high":
        draw.rectangle([x - 5, y - 5, x + 5, y + 5], fill=color)
    elif effort == "xhigh":
        draw.polygon([(x, y - 6), (x + 6, y), (x, y + 6), (x - 6, y)], fill=color)
    elif effort == "max":
        draw.polygon(
            [
                (x - 5.5, y - 3.2), (x, y - 6.2), (x + 5.5, y - 3.2),
                (x + 5.5, y + 3.2), (x, y + 6.2), (x - 5.5, y + 3.2),
            ],
            fill=color,
        )
    elif effort == "ultra":
        draw.polygon(
            [(x + dx, y + dy) for dx, dy in (_star_point(i) for i in range(10))],
            fill=color,
        )
    else:
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color)


def iq_history_png(
    series: Sequence[HistorySeries],
    out_path: str,
    *,
    hours: int = 72,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
    updated_at: Optional[str] = None,
    width: int = 960,
    height: int = 520,
    effort_colors: bool = False,
) -> str:
    """72 小时 IQ 历史曲线（PNG）。返回 out_path。"""
    image, draw = _prepare_image(width, height)
    margin = {"left": 62, "right": 26, "top": 56, "bottom": 86}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    all_scores = [p.score for s in series for p in s.points if p.score is not None]
    # 动态 y 轴：数据范围 + 15% padding（最小跨度 10），放大曲线波动
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

    draw.text((width / 2 - 120, 14), f"IQ 历史曲线（{hours}h）", font=_font(24, bold=True), fill="#111827")

    y_ticks = 6
    for i in range(y_ticks + 1):
        score = y_lo + (y_hi - y_lo) * i / y_ticks
        y = y_of(score)
        draw.line([(margin["left"], y), (width - margin["right"], y)], fill="#e5e7eb", width=1)
        draw.text((margin["left"] - 46, y - 7), f"{score:.0f}", font=_font(12), fill="#6b7280")
    x_ticks = 6
    for i in range(x_ticks + 1):
        t = t0 + (t1 - t0) * i / x_ticks
        x = margin["left"] + plot_w * i / x_ticks
        draw.line([(x, margin["top"]), (x, height - margin["bottom"])], fill="#e5e7eb", width=1)
        draw.text((x - 34, height - margin["bottom"] + 8), t.strftime("%m-%d %H:%M"), font=_font(12), fill="#6b7280")

    draw.text((width / 2 - 150, height - margin["bottom"] + 48), "IQ 分数（动态区间，波动放大显示）", font=_font(14), fill="#374151")

    seen_models: dict[str, int] = {}
    for s in series:
        # 筛选单模型时：模型总览用模型色，各思考强度用站点 effortColors
        color = (
            effort_color(s.effort)
            if effort_colors and s.effort is not None
            else model_color(s.model, seen_models)
        )
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
        overview = effort_colors and s.effort is None
        for seg in segments:
            if len(seg) < 2:
                continue
            # 单模型视图：模型总览线加粗，避免与强度色混淆
            draw.line(seg, fill=color, width=4 if overview else 3, joint="curve")
        latest = s.latest()
        if latest is not None and latest.score is not None and segments:
            x, y = segments[-1][-1]
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=color)

    legend_x = margin["left"] + 8
    legend_y = height - margin["bottom"] + 34
    legend_max_x = width - margin["right"] - 10
    for s in series:
        color = (
            effort_color(s.effort)
            if effort_colors and s.effort is not None
            else model_color(s.model, seen_models)
        )
        if s.effort is None:
            label = short_model(s.model)
        elif effort_colors:
            label = s.effort  # 单模型视图：图例直接标思考强度
        else:
            label = f"{short_model(s.model)}@{s.effort}"
        latest = s.latest()
        if latest is not None and latest.score is not None:
            label += f"  {latest.score:.1f}"
        item_w = 20 + len(label) * 11 + 6
        if legend_x + item_w > legend_max_x:
            legend_x = margin["left"] + 8
            legend_y += 18
        draw.rectangle([legend_x, legend_y - 10, legend_x + 12, legend_y + 2], fill=color)
        draw.text((legend_x + 13, legend_y - 10), label, font=_font(11), fill="#374151")
        legend_x += item_w

    footer = f"时间范围：{_fmt_time(window_start)} ~ {_fmt_time(window_end)}"
    if updated_at:
        footer += f"  更新：{_fmt_time(updated_at)}"
    draw.text((width / 2 - 220, height - 18), footer[:70], font=_font(12), fill="#9ca3af")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    image.save(out_path, "PNG")
    return out_path
