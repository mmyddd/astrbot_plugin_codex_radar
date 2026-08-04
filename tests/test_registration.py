"""指令注册与路由测试：两个指令必须注册成功且处理器可被调用。"""

from __future__ import annotations

import inspect

import pytest

import main
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.register.star_handler import star_handlers_registry
from codex_radar.errors import RadarFetchError
from codex_radar.format import format_history_text, format_radar_text
from tests.conftest import FakeEvent


def _plugin_handlers():
    return star_handlers_registry.get_handlers_by_module_name("main")


def _command_names(handlers) -> set[str]:
    names: set[str] = set()
    for handler in handlers:
        for event_filter in handler.event_filters:
            if isinstance(event_filter, CommandFilter):
                names.add(event_filter.command_name)
    return names


@pytest.fixture(scope="module")
def plugin():
    return main.CodexRadarPlugin(context=object())


def test_commands_registered(plugin):
    handlers = _plugin_handlers()
    assert len(handlers) >= 2
    assert _command_names(handlers) == {"降智雷达", "雷达历史"}


def test_handlers_are_async_generators(plugin):
    handlers = _plugin_handlers()
    by_name = {h.handler_name: h for h in handlers}
    assert inspect.isasyncgenfunction(by_name["radar"].handler)
    assert inspect.isasyncgenfunction(by_name["history"].handler)


def test_history_command_accepts_optional_model_param(plugin):
    """雷达历史 sol：AstrBot 会把 sol 作为可选参数 model 传入。"""
    handlers = _plugin_handlers()
    history_md = next(h for h in handlers if h.handler_name == "history")
    cmd_filter = next(
        f for f in history_md.event_filters if isinstance(f, CommandFilter)
    )
    assert cmd_filter.handler_params.get("model") == ""  # 可选参数，默认空串


async def test_history_command_with_model_filter(plugin, history_payload):
    """雷达历史 sol：只输出 gpt-5.6-sol 的模型总览与全思考强度。"""
    plugin._config.send_chart_image = False
    await plugin.service._cache.clear()

    async def ok_fetcher(*a, **k):
        return history_payload

    plugin.service._fetcher = ok_fetcher
    event = FakeEvent("雷达历史 sol")
    results = [r async for r in plugin.history(event, model="sol")]
    assert len(results) == 1
    kind, payload = results[0]
    assert kind == "plain"
    assert "gpt-5.6-sol" in payload
    assert "【各思考强度】" in payload
    assert "【模型总览】" not in payload  # 指定模型时无平均/总览
    # 不包含其他模型（gpt-5.5 / deepseek）
    assert "gpt-5.5" not in payload
    assert "deepseek" not in payload


async def test_history_command_invalid_model(plugin, history_payload):
    """雷达历史 xxx：返回可诊断错误，不返回数据。"""
    plugin._config.send_chart_image = False
    await plugin.service._cache.clear()

    async def ok_fetcher(*a, **k):
        return history_payload

    plugin.service._fetcher = ok_fetcher
    event = FakeEvent("雷达历史 xxx")
    results = [r async for r in plugin.history(event, model="xxx")]
    assert len(results) == 1
    kind, payload = results[0]
    assert kind == "plain"
    assert "未找到模型" in payload
    assert "可用模型" in payload


def test_handler_descriptions_present(plugin):
    handlers = _plugin_handlers()
    for handler in handlers:
        assert handler.desc, "处理器缺少描述（会用于指令帮助）"


async def test_radar_command_returns_text_when_chart_disabled(
    plugin, efficiency_payload
):
    plugin._config.send_chart_image = False
    await plugin.service._cache.clear()

    async def ok_fetcher(*a, **k):
        return efficiency_payload

    plugin.service._fetcher = ok_fetcher

    event = FakeEvent("降智雷达")
    results = [r async for r in plugin.radar(event)]
    assert len(results) == 1
    kind, payload = results[0]
    assert kind == "plain"
    assert "IQ" in payload
    assert "gpt-5.6-sol" in payload
    assert "更新时间" in payload


async def test_radar_command_error_path(plugin):
    plugin._config.send_chart_image = False
    await plugin.service._cache.clear()

    async def broken_fetcher(*args, **kwargs):
        raise RadarFetchError(
            "https://codexradar.com/api/intelligence-efficiency",
            reason="连接被拒绝",
        )

    plugin.service._fetcher = broken_fetcher
    event = FakeEvent("降智雷达")
    results = [r async for r in plugin.radar(event)]
    assert len(results) == 1
    kind, payload = results[0]
    assert kind == "plain"
    assert "数据抓取失败" in payload
    assert "未返回虚构数据" in payload


async def test_history_command_returns_chart_and_text(plugin, history_payload, tmp_path):
    plugin._config.send_chart_image = True
    await plugin.service._cache.clear()

    async def ok_fetcher(*a, **k):
        return history_payload

    plugin.service._fetcher = ok_fetcher

    png = tmp_path / "history.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    async def fake_render(snapshot, model=None):
        return str(png)

    plugin._render_history = fake_render  # type: ignore[method-assign]
    event = FakeEvent("雷达历史")
    results = [r async for r in plugin.history(event)]
    assert len(results) == 1
    kind, chain = results[0]
    assert kind == "chain"
    kinds = [type(c).__name__ for c in chain]
    # 消息顺序：先图片后文字
    assert kinds == ["Image", "Plain"]
    text = next(c for c in chain if type(c).__name__ == "Plain").text
    assert "72" in text
    assert "gpt-5.6-sol" in text


async def test_history_command_error_path(plugin):
    plugin._config.send_chart_image = False
    await plugin.service._cache.clear()

    async def broken_fetcher(*args, **kwargs):
        raise RadarFetchError(
            "https://api.codexradar.com/api/v1/iq-history",
            reason="请求超时",
        )

    plugin.service._fetcher = broken_fetcher
    event = FakeEvent("雷达历史")
    results = [r async for r in plugin.history(event)]
    assert len(results) == 1
    kind, payload = results[0]
    assert kind == "plain"
    assert "数据抓取失败" in payload
    assert "未返回虚构数据" in payload


def test_text_formatting_smoke(efficiency_payload, history_payload):
    """格式化函数不抛异常且包含关键信息。"""
    from codex_radar.history_parser import parse_iq_history
    from codex_radar.radar_parser import parse_intelligence_efficiency

    snapshot = parse_intelligence_efficiency(efficiency_payload)
    text = format_radar_text(snapshot)
    assert "综合成本" in text

    history = parse_iq_history(history_payload, hours=72)
    htext = format_history_text(history)
    assert "IQ 历史" in htext


def test_html_shell_fills_viewport_width():
    """回归：SVG 必须撑满渲染视口宽度，避免右侧大片空白。"""
    shell = main._HTML_SHELL
    assert "width: 100vw" in shell
    assert "height: auto" in shell
