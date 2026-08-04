"""Codex 雷达插件核心逻辑包。

包含数据抓取（client）、缓存（cache）、数据解析（radar_parser / history_parser）、
图表生成（chart_svg / chart_pil）与聊天文本格式化（format）。
"""

from .errors import RadarError, RadarFetchError, RadarParseError, RadarRenderError
from .service import RadarConfig, RadarService

__all__ = [
    "RadarError",
    "RadarFetchError",
    "RadarParseError",
    "RadarRenderError",
    "RadarConfig",
    "RadarService",
]

__version__ = "1.0.0"
