"""对真实目标站点做端到端验证（不依赖 AstrBot 运行时）。

用法：
    python scripts/validate_live.py [--out out]

行为：
1. 抓取 https://codexradar.com/api/intelligence-efficiency（失败时回退静态快照）
2. 抓取 https://api.codexradar.com/api/v1/iq-history（deng.codexradar.com 前端所用接口）
3. 按插件解析逻辑解析并打印摘要
4. 生成 SVG + Pillow PNG 图表到 out/ 目录
5. 全部成功退出码 0；任何失败打印可诊断错误并退出码 1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar.chart_pil import efficiency_scatter_png, iq_history_png  # noqa: E402
from radar.chart_svg import efficiency_scatter_svg, iq_history_svg  # noqa: E402
from radar.client import fetch_json  # noqa: E402
from radar.errors import RadarError  # noqa: E402
from radar.format import format_history_text, format_radar_text  # noqa: E402
from radar.history_parser import parse_iq_history, resolve_model_alias  # noqa: E402
from radar.radar_parser import parse_intelligence_efficiency  # noqa: E402
from radar.service import HISTORY_URL, RADAR_URLS  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(os.getcwd(), "out"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--model",
        default=None,
        help="只输出指定模型（如 sol / d4flash），默认全部模型",
    )
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    failures: list[str] = []

    # ---- 智力效率 ----
    print("== 降智雷达数据 ==")
    radar_snapshot = None
    for url in RADAR_URLS:
        try:
            payload = await fetch_json(url, timeout_seconds=args.timeout, retries=2)
            snapshot = parse_intelligence_efficiency(payload, url=url)
            radar_snapshot = snapshot
            print(f"[OK] {url}")
            break
        except RadarError as exc:
            failures.append(f"radar {url}: {exc}")
            print(f"[FAIL] {url}: {exc}")
    if radar_snapshot is None:
        print("降智雷达数据不可用。")
    else:
        print(f"模型×强度组合：{radar_snapshot.combos_count}，评测题：{radar_snapshot.tasks_count}")
        print(f"更新时间：{radar_snapshot.updated_at}（最新判分 {radar_snapshot.source_updated_at}）")
        for model, points in radar_snapshot.points_by_model():
            row = ", ".join(
                f"{p.effort}: IQ={p.iq:.1f} 耗时={p.average_minutes:.1f}分 "
                f"花费=${p.average_price_usd:.3f} 综合成本={p.combined_cost_index:.0f}"
                for p in points
            )
            print(f"  {model}: {row}")
        with open(os.path.join(args.out, "radar_text.txt"), "w", encoding="utf-8") as f:
            f.write(format_radar_text(radar_snapshot))
        svg = efficiency_scatter_svg(
            radar_snapshot.points,
            updated_at=radar_snapshot.updated_at,
            source_url=radar_snapshot.source_url,
        )
        with open(os.path.join(args.out, "radar_scatter.svg"), "w", encoding="utf-8") as f:
            f.write(svg)
        efficiency_scatter_png(
            radar_snapshot.points,
            os.path.join(args.out, "radar_scatter.png"),
            updated_at=radar_snapshot.updated_at,
            source_url=radar_snapshot.source_url,
        )
        print(f"[OK] 图表: {os.path.join(args.out, 'radar_scatter.svg')} / radar_scatter.png")

    # ---- IQ 历史 ----
    print("\n== 雷达历史数据 ==")
    try:
        payload = await fetch_json(HISTORY_URL, timeout_seconds=args.timeout, retries=2)
        history = parse_iq_history(payload, hours=72, url=HISTORY_URL)
        print(f"[OK] {HISTORY_URL}")
        print(f"系列数：{len(history.series)}（含 latest: 实时系列）")
        print(f"时间范围：{history.window_start} ~ {history.window_end}")
        print(f"更新时间：{history.updated_at}")
        for s in history.model_series():
            latest = s.latest()
            if latest is None or latest.score is None:
                print(f"  {s.model}: 暂无数据（{len(s.points)} 个观察点）")
            else:
                print(f"  {s.model}: 最新 IQ={latest.score:.1f}（{len(s.points)} 个观察点，n={latest.n}）")
        for s in history.preferred_series():
            if s.effort is None:
                continue
            latest = s.latest()
            value = "—" if latest is None or latest.score is None else f"{latest.score:.1f}"
            print(f"    {s.model}@{s.effort}: {value}")

        selected = None
        if args.model:
            selected = resolve_model_alias(args.model, history.models())
            print(f"[OK] 模型筛选: {args.model} -> {selected}（系列 {len(history.series_for_model(selected))} 条）")
        chart_series = (
            # 指定模型：只画各思考等级曲线，不画模型平均（总览）线
            [s for s in history.series_for_model(selected) if s.effort is not None]
            if selected
            else history.model_series()
        )
        with open(os.path.join(args.out, "history_text.txt"), "w", encoding="utf-8") as f:
            f.write(format_history_text(history, model=selected))
        suffix = f"_{selected}" if selected else ""
        svg = iq_history_svg(
            chart_series,
            hours=72,
            window_start=history.window_start,
            window_end=history.window_end,
            updated_at=history.updated_at,
            effort_colors=selected is not None,
        )
        with open(os.path.join(args.out, f"iq_history{suffix}.svg"), "w", encoding="utf-8") as f:
            f.write(svg)
        iq_history_png(
            chart_series,
            os.path.join(args.out, f"iq_history{suffix}.png"),
            hours=72,
            window_start=history.window_start,
            window_end=history.window_end,
            updated_at=history.updated_at,
            effort_colors=selected is not None,
        )
        print(f"[OK] 图表: {os.path.join(args.out, f'iq_history{suffix}.svg')} / iq_history{suffix}.png")
    except RadarError as exc:
        failures.append(f"history: {exc}")
        print(f"[FAIL] history: {exc}")

    print("\n" + ("验证失败：" if failures else "验证通过。"))
    for item in failures:
        print(" -", item)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
