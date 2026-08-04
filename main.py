"""AstrBot 插件：Codex 智力效率雷达。

指令：
- `降智雷达`：抓取 https://codexradar.com/ 的智力效率数据
  （耗时 / 分数 / 花费 / 综合成本曲线 / 更新时间），按模型 × 思考强度完整列出，
  并发送「综合成本 × IQ」曲线图片。
- `雷达历史`：抓取 https://deng.codexradar.com/ 的 72h IQ 历史曲线
  （数据接口 api.codexradar.com/api/v1/iq-history），发送 72h 曲线图片并列出各模型数据。
- `雷达历史 <模型>`：只输出指定模型的近 72h 各思考等级历史（支持 sol / d4flash 等别名）。

图表渲染链路：AstrBot html_render（Playwright）→ Pillow PNG → 文本 + ASCII 走势图。
失败时返回可诊断错误，绝不返回虚构数据。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Optional

# AstrBot 的插件加载器不一定把插件目录加入 sys.path：
# 这里显式加入，确保子包 codex_radar 可被 import（避免 No module named 'codex_radar'）。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

from codex_radar.chart_pil import efficiency_scatter_png, iq_history_png
from codex_radar.chart_svg import efficiency_scatter_svg, iq_history_svg
from codex_radar.errors import RadarError
from codex_radar.format import format_history_text, format_radar_text
from codex_radar.history_parser import HistorySnapshot, resolve_model_alias
from codex_radar.radar_parser import RadarSnapshot
from codex_radar.service import RadarConfig, RadarService

_HTML_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><style>
 html, body { margin: 0; padding: 0; background: #ffffff; }
 /* 渲染服务使用固定 viewport 宽度：让 SVG 撑满视口，避免右侧大片空白 */
 svg { display: block; width: 100vw !important; height: auto !important; }
</style></head>
<body>{{ svg | safe }}</body></html>"""


def _out_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "astrbot_codex_radar")
    os.makedirs(path, exist_ok=True)
    return path


class CodexRadarPlugin(Star):
    """Codex 智力效率雷达插件。

    使用方式：
    - 发送「降智雷达」获取 codexradar.com 的智力效率数据与综合成本 × IQ 曲线。
    - 发送「雷达历史」获取 72 小时 IQ 历史曲线与各模型明细。
    - 发送「雷达历史 sol / d4flash ...」获取指定模型的各思考等级历史。
    """

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self._config = RadarConfig.from_dict(config)
        self.service = RadarService(self._config)

    # ------------------------------------------------------------------ 指令

    @filter.command("降智雷达")
    async def radar(self, event: AstrMessageEvent):
        """获取 Codex 智力效率数据（耗时 / 分数 / 花费 / 综合成本曲线）。"""
        try:
            snapshot = await self.service.get_radar_snapshot()
        except RadarError as exc:
            yield event.plain_result(f"【降智雷达】\n{exc.user_message()}")
            return

        text = format_radar_text(snapshot)
        if self._config.send_chart_image:
            image_path = await self._render_scatter(snapshot)
            if image_path:
                yield event.chain_result([Image.fromFileSystem(image_path), Plain(text)])
                return
        yield event.plain_result(text)

    @filter.command("雷达历史")
    async def history(self, event: AstrMessageEvent, model: str = ""):
        """获取 72 小时 IQ 历史曲线（deng.codexradar.com 数据）。
        可选参数：雷达历史 sol / 雷达历史 d4flash ...（不填则输出全部模型）"""
        try:
            snapshot = await self.service.get_history_snapshot()
        except RadarError as exc:
            yield event.plain_result(f"【雷达历史】\n{exc.user_message()}")
            return

        # 可选参数：雷达历史 sol / 雷达历史 d4flash ...（不填则输出全部模型）
        selected: Optional[str] = None
        if model and model.strip():
            try:
                selected = resolve_model_alias(model, snapshot.models())
            except ValueError as exc:
                yield event.plain_result(f"【雷达历史】{exc}")
                return

        text = format_history_text(snapshot, model=selected)
        if self._config.send_chart_image:
            image_path = await self._render_history(snapshot, model=selected)
            if image_path:
                yield event.chain_result([Image.fromFileSystem(image_path), Plain(text)])
                return
        yield event.plain_result(text)

    # ------------------------------------------------------------ 图表渲染

    async def _render_scatter(self, snapshot: RadarSnapshot) -> Optional[str]:
        """渲染「综合成本 × IQ」曲线图：html_render → Pillow 降级。"""
        svg = efficiency_scatter_svg(
            snapshot.points,
            updated_at=snapshot.updated_at,
            source_url=snapshot.source_url,
        )
        path = await self._html_render_png(svg)
        if path:
            return path
        try:
            return efficiency_scatter_png(
                snapshot.points,
                os.path.join(_out_dir(), f"radar_{int(time.time())}.png"),
                updated_at=snapshot.updated_at,
                source_url=snapshot.source_url,
            )
        except Exception as exc:  # pragma: no cover - 降级失败仅告警
            logger.warning(f"[codex_radar] Pillow 渲染失败，降级为文本：{exc}")
            return None

    async def _render_history(
        self, snapshot: HistorySnapshot, model: Optional[str] = None
    ) -> Optional[str]:
        """渲染 72h IQ 历史曲线：html_render → Pillow 降级。

        model 为 None 时画全部模型的模型级曲线；指定模型时只画该模型
        各思考等级的曲线（不画模型平均线）。
        """
        if model:
            # 指定模型：只输出各思考等级的历史，不输出模型平均（总览）线
            series = [s for s in snapshot.series_for_model(model) if s.effort is not None]
        else:
            series = snapshot.model_series()
        # 单模型视图：不同思考强度用不同颜色（站点 effortColors）
        if not series:
            return None
        svg = iq_history_svg(
            series,
            hours=snapshot.hours,
            window_start=snapshot.window_start,
            window_end=snapshot.window_end,
            updated_at=snapshot.updated_at,
            effort_colors=model is not None,
        )
        path = await self._html_render_png(svg)
        if path:
            return path
        try:
            return iq_history_png(
                series,
                os.path.join(_out_dir(), f"history_{int(time.time())}.png"),
                hours=snapshot.hours,
                window_start=snapshot.window_start,
                window_end=snapshot.window_end,
                updated_at=snapshot.updated_at,
                effort_colors=model is not None,
            )
        except Exception as exc:  # pragma: no cover - 降级失败仅告警
            logger.warning(f"[codex_radar] Pillow 渲染失败，降级为文本：{exc}")
            return None

    async def _html_render_png(self, svg: str) -> Optional[str]:
        """使用 AstrBot 内置 html_render（Playwright）渲染 SVG 为 PNG。"""
        try:
            return await self.html_render(
                _HTML_SHELL,
                {"svg": svg},
                return_url=False,
                options={"type": "png", "full_page": True, "omit_background": True},
            )
        except Exception as exc:  # Playwright 不可用 / 渲染失败
            logger.warning(f"[codex_radar] html_render 不可用，尝试 Pillow 降级：{exc}")
            return None

    async def terminate(self):
        """插件卸载：清空缓存。"""
        try:
            await self.service.clear_cache()
        except Exception:  # pragma: no cover
            pass
