"""插件类型化错误。

所有失败都带「阶段 / 地址 / 原因 / 重试次数」等可诊断信息，
并且明确声明不会返回虚构数据。
"""

from __future__ import annotations

from typing import Optional


class RadarError(Exception):
    """插件基础错误。"""

    stage = "未知阶段"

    def user_message(self) -> str:
        """面向聊天用户的、可诊断的错误文案。"""
        return str(self)


class RadarFetchError(RadarError):
    """HTTP 抓取失败：超时、连接失败、非 2xx 状态码、响应不是 JSON。"""

    stage = "HTTP 请求"

    def __init__(
        self,
        url: str,
        *,
        status: Optional[int] = None,
        reason: str = "",
        attempts: int = 1,
        elapsed: Optional[float] = None,
    ) -> None:
        self.url = url
        self.status = status
        self.reason = reason
        self.attempts = attempts
        self.elapsed = elapsed
        super().__init__(self._compose())

    def _compose(self) -> str:
        lines = ["数据抓取失败（HTTP 请求）", f"- 地址：{self.url}"]
        if self.status is not None:
            lines.append(f"- 状态码：{self.status}")
        if self.reason:
            lines.append(f"- 原因：{self.reason}")
        if self.elapsed is not None:
            lines.append(f"- 耗时：{self.elapsed:.1f}s")
        if self.attempts > 1:
            lines.append(f"- 已重试：{self.attempts - 1} 次")
        lines.append("- 未返回虚构数据。请稍后重试；若持续失败，可能是目标站点接口变更或网络不可达。")
        return "\n".join(lines)


class RadarParseError(RadarError):
    """响应结构异常 / 缺少字段（站点可能改版）。"""

    stage = "数据解析"

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        self.detail = detail
        super().__init__(self._compose())

    def _compose(self) -> str:
        return (
            "数据解析失败（页面结构可能已变化）\n"
            f"- 地址：{self.url}\n"
            f"- 原因：{self.detail}\n"
            "- 未返回虚构数据。请等待站点恢复或检查接口是否变更。"
        )


class RadarRenderError(RadarError):
    """图表渲染失败（插件会自动降级到文本/ASCII 展示）。"""

    stage = "图表渲染"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"图表渲染失败：{detail}（已降级为文本展示）")
