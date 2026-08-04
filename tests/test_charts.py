"""图表生成测试：SVG 结构合法、Pillow PNG 可生成且可读取。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from PIL import Image

from codex_radar.chart_pil import efficiency_scatter_png, iq_history_png
from codex_radar.chart_svg import efficiency_scatter_svg, iq_history_svg

import pytest
from codex_radar.history_parser import parse_iq_history
from codex_radar.radar_parser import parse_intelligence_efficiency


def test_scatter_svg_well_formed(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload)
    svg = efficiency_scatter_svg(
        snapshot.points,
        updated_at=snapshot.updated_at,
        source_url=snapshot.source_url,
    )
    assert svg.startswith("<svg")
    root = ET.fromstring(svg)  # 非良构 XML 会抛异常
    assert root.tag.endswith("svg")
    text = ET.tostring(root, encoding="unicode")
    assert "综合成本 × IQ" in text
    assert "gpt-5.6-sol" in text
    assert "更新时间" in text or "更新" in text


def test_scatter_uses_log_scale_and_broken_axis(efficiency_payload):
    """站点同款：x 轴对数刻度，第二小值 >= 4× 最小值时启用断轴。"""
    from codex_radar.chart_svg import cost_tick_text, log_share, nice_max, scatter_layout

    # 纯函数：对数刻度边界
    assert log_share(1, 1, 100) == 0
    assert log_share(100, 1, 100) == 1
    assert log_share(10, 1, 100) == pytest.approx(0.5)
    # niceMax（站点 y 轴上限取整）
    assert nice_max(93.8) == 100
    assert nice_max(7.0) == 8
    # 刻度文本格式（>=10 取整，>=1 一位，>=0.01 两位，否则四位）
    assert cost_tick_text(100.0) == "100"
    assert cost_tick_text(1.2) == "1.2"
    assert cost_tick_text(0.0582) == "0.06"
    assert cost_tick_text(0.00012) == "0.0001"

    snapshot = parse_intelligence_efficiency(efficiency_payload)
    layout = scatter_layout(snapshot.points)
    # fixture 数据：综合成本指数跨度极大（约 5e-5 ~ 100），必然触发断轴
    assert layout["broken"] is True
    assert layout["x_max"] > 50

    svg = efficiency_scatter_svg(snapshot.points, updated_at=snapshot.updated_at)
    # 断轴标记与对数刻度小数刻度存在
    assert "l5 -8" in svg
    assert "0.0001" in svg
    # 点按模型连线（每模型一条 path），形状区分强度
    assert "<path" in svg and "<polygon" in svg and "<rect" in svg


def test_scatter_png_points_spread_across_width(efficiency_payload, tmp_path):
    """回归：对数刻度下点不应全部聚集在最左侧。"""
    from PIL import Image
    from codex_radar.chart_pil import efficiency_scatter_png

    snapshot = parse_intelligence_efficiency(efficiency_payload)
    out = tmp_path / "scatter.png"
    efficiency_scatter_png(snapshot.points, str(out), updated_at=snapshot.updated_at)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    # 统计模型配色像素的 x 分布（sol 黄 #eab308 / terra 蓝 #3b82f6 / luna 灰 / 未来模型色）
    targets = {(234, 179, 8), (59, 130, 246), (148, 163, 184)}
    xs: list[int] = []
    for x in range(w):
        for y in range(0, h, 2):
            if img.getpixel((x, y)) in targets:
                xs.append(x)
    assert xs, "图中找不到模型配色的数据点像素"
    # 数据点必须覆盖超过 55% 的图宽（对数刻度展开），而不是挤在最左侧
    assert max(xs) > w * 0.55
    assert min(xs) < w * 0.25


def test_history_svg_well_formed(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72)
    series = snapshot.model_series()
    svg = iq_history_svg(
        series,
        hours=72,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        updated_at=snapshot.updated_at,
    )
    root = ET.fromstring(svg)
    text = ET.tostring(root, encoding="unicode")
    assert "IQ 历史曲线" in text
    assert "gpt-5.6-sol" in text
    assert "07-31" in text or "08-0" in text  # 时间轴刻度


def test_history_svg_dynamic_y_axis(history_payload):
    """y 轴为动态区间（数据范围放大），而非固定 0-150，曲线波动可见。"""
    snapshot = parse_iq_history(history_payload, hours=72)
    svg = iq_history_svg(snapshot.model_series(), hours=72, updated_at=snapshot.updated_at)
    # fixture 分数范围约 85~106：动态轴不应出现 0 / 150 刻度
    assert ">0<" not in svg
    assert ">150<" not in svg
    # 动态区间标注
    assert "动态区间" in svg


def test_history_png_dynamic_y_axis(history_payload, tmp_path):
    """Pillow 路径同样使用动态 y 轴（曲线占满大部分图高）。"""
    from codex_radar.chart_pil import iq_history_png

    snapshot = parse_iq_history(history_payload, hours=72)
    out = tmp_path / "history_dyn.png"
    iq_history_png(snapshot.model_series(), str(out), hours=72, updated_at=snapshot.updated_at)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    # 找模型色像素（sol 黄 #eab308）的 y 范围：曲线应覆盖图高中部大片区域
    ys = [
        y
        for x in range(0, w, 2)
        for y in range(0, h, 2)
        if img.getpixel((x, y)) == (234, 179, 8)
    ]
    assert ys, "曲线像素缺失"
    # 曲线纵向跨度 > 20% 图高（固定 0-150 时仅有约 10%）
    assert (max(ys) - min(ys)) > h * 0.2


def test_history_svg_filtered_model_includes_efforts(history_payload):
    """雷达历史 sol：图表只含该模型的思考强度系列（无模型平均/总览线）。"""
    snapshot = parse_iq_history(history_payload, hours=72)
    series = [s for s in snapshot.series_for_model("gpt-5.6-sol") if s.effort is not None]
    assert len(series) == 1  # 仅 @max
    svg = iq_history_svg(
        series,
        hours=72,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        updated_at=snapshot.updated_at,
        effort_colors=True,
    )
    root = ET.fromstring(svg)
    text = ET.tostring(root, encoding="unicode")
    assert ">max " in text  # 强度系列进入图例
    # 没有总览线：模型级图例标签（无 @effort）不应出现
    assert ">gpt-5.6-sol<" not in text
    assert "gpt-5.5" not in text  # 其他模型不出现


def test_history_svg_effort_colors_per_level(history_payload):
    """雷达历史 sol：各思考强度用不同颜色（站点 effortColors），无模型平均线。"""
    from codex_radar.chart_svg import EFFORT_COLORS, MODEL_COLORS

    snapshot = parse_iq_history(history_payload, hours=72)
    series = [s for s in snapshot.series_for_model("gpt-5.6-sol") if s.effort is not None]
    svg = iq_history_svg(
        series,
        hours=72,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        updated_at=snapshot.updated_at,
        effort_colors=True,
    )
    # @max 用 max 的 effort 色；模型色不应出现在曲线上
    assert f'stroke="{EFFORT_COLORS["max"]}"' in svg
    assert f'stroke="{MODEL_COLORS["gpt-5.6-sol"]}"' not in svg
    # 图例直接标思考强度名
    root = ET.fromstring(svg)
    text = ET.tostring(root, encoding="unicode")
    assert ">max " in text  # 图例条目以思考强度名开头
    assert "gpt-5.6-sol@max" not in text


def test_history_png_effort_colors(history_payload, tmp_path):
    """Pillow 降级路径同样按强度着色，且不画模型平均线。"""
    from codex_radar.chart_pil import iq_history_png

    snapshot = parse_iq_history(history_payload, hours=72)
    series = [s for s in snapshot.series_for_model("gpt-5.6-sol") if s.effort is not None]
    out = tmp_path / "history_effort.png"
    iq_history_png(
        series,
        str(out),
        hours=72,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        updated_at=snapshot.updated_at,
        effort_colors=True,
    )
    img = Image.open(out).convert("RGB")
    w, h = img.size
    found = set()
    for x in range(0, w, 3):
        for y in range(0, h, 3):
            found.add(img.getpixel((x, y)))
    # max 色 #cc79a7 = (204, 121, 167) 必须出现（唯一强度线）
    assert (204, 121, 167) in found
    # 模型总览色（sol 黄 #eab308 = (234, 179, 8)）不应出现（无总览线）
    assert (234, 179, 8) not in found


def test_scatter_png_generated(efficiency_payload, tmp_path):
    snapshot = parse_intelligence_efficiency(efficiency_payload)
    out = tmp_path / "scatter.png"
    efficiency_scatter_png(snapshot.points, str(out), updated_at=snapshot.updated_at)
    assert out.exists() and out.stat().st_size > 0
    with open(out, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    with Image.open(out) as img:
        img.verify()


def test_history_png_generated(history_payload, tmp_path):
    snapshot = parse_iq_history(history_payload, hours=72)
    out = tmp_path / "history.png"
    iq_history_png(
        snapshot.model_series(),
        str(out),
        hours=72,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        updated_at=snapshot.updated_at,
    )
    assert out.exists() and out.stat().st_size > 0
    with open(out, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
