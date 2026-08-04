"""IQ 历史数据解析测试（72h 窗口、latest: 系列、空系列）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from codex_radar.errors import RadarParseError
from codex_radar.history_parser import parse_iq_history

URL = "https://api.codexradar.com/api/v1/iq-history"


def test_parse_basic_structure(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    assert snapshot.hours == 72
    assert snapshot.source_url == URL
    assert len(snapshot.series) == 5
    assert snapshot.updated_at == "2026-08-03T23:00:00+00:00"


def test_72h_window_filter(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    start = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
    cutoff = start + timedelta(hours=23)  # 08-03 23:00 - 72h = 07-31 23:00
    end = start + timedelta(hours=95)  # 08-03 23:00

    sol = next(s for s in snapshot.series if s.key == "gpt-5.6-sol")
    # 72 小时窗口（含端点）=> 73 个每小时观察点
    assert len(sol.points) == 73
    first_ts = datetime.fromisoformat(sol.points[0].ts.replace("Z", "+00:00"))
    last_ts = datetime.fromisoformat(sol.points[-1].ts.replace("Z", "+00:00"))
    assert first_ts == cutoff
    assert last_ts == end
    # 窗口外的点被丢弃
    assert all(datetime.fromisoformat(p.ts.replace("Z", "+00:00")) >= cutoff for p in sol.points)


def test_latest_series_preferred(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    preferred = snapshot.preferred_series()
    keys = [s.key for s in preferred]
    # latest:gpt-5.6-sol 覆盖 gpt-5.6-sol
    assert "latest:gpt-5.6-sol" in keys
    assert "gpt-5.6-sol" not in keys
    # 无 latest 变体的系列原样保留
    assert "gpt-5.6-sol@max" in keys
    assert "gpt-5.5" in keys
    assert "deepseek-v4-flash" in keys


def test_model_series_only(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    models = snapshot.model_series()
    assert [s.model for s in models] == ["gpt-5.6-sol", "gpt-5.5", "deepseek-v4-flash"]


def test_null_scores_kept_as_no_data(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    deepseek = next(s for s in snapshot.series if s.model == "deepseek-v4-flash")
    assert not deepseek.has_data()
    assert deepseek.latest().score is None
    # 有 null 间隙的系列仍整体有效
    gpt55 = next(s for s in snapshot.series if s.model == "gpt-5.5")
    assert gpt55.has_data()
    assert any(p.score is None for p in gpt55.points)


def test_points_sorted_by_time(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    for s in snapshot.series:
        stamps = [p.ts for p in s.points]
        assert stamps == sorted(stamps)


def test_invalid_payloads_raise(history_payload):
    with pytest.raises(RadarParseError, match="顶层"):
        parse_iq_history(["not", "a", "dict"], url=URL)
    with pytest.raises(RadarParseError, match="为空"):
        parse_iq_history({}, url=URL)
    bad = dict(history_payload)
    bad["gpt-5.6-sol"] = "oops"
    with pytest.raises(RadarParseError, match="列表"):
        parse_iq_history(bad, url=URL)
    bad2 = dict(history_payload)
    bad2["gpt-5.6-sol"] = [{"score": 1.0}]  # 缺 ts
    with pytest.raises(RadarParseError, match="ts"):
        parse_iq_history(bad2, url=URL)
    bad3 = dict(history_payload)
    bad3["gpt-5.6-sol"] = [{"ts": "not-a-date", "score": 1.0}]
    with pytest.raises(RadarParseError, match="时间戳"):
        parse_iq_history(bad3, url=URL)


def test_all_empty_series_raise(history_payload_empty_series):
    with pytest.raises(RadarParseError, match="均为空"):
        parse_iq_history(history_payload_empty_series, url=URL)


def test_hours_clamped(history_payload):
    snapshot = parse_iq_history(history_payload, hours=-5, url=URL)
    assert snapshot.hours == 72


# ---------------------------------------------------------------- 模型别名


def test_models_and_series_for_model(history_payload):
    snapshot = parse_iq_history(history_payload, hours=72, url=URL)
    assert snapshot.models() == [
        "gpt-5.6-sol",
        "gpt-5.5",
        "deepseek-v4-flash",
    ]
    sol_series = snapshot.series_for_model("gpt-5.6-sol")
    # latest:gpt-5.6-sol 覆盖普通系列；含模型级与 effort 级
    assert [s.key for s in sol_series] == ["latest:gpt-5.6-sol", "gpt-5.6-sol@max"]


def test_resolve_model_alias(history_payload):
    from codex_radar.history_parser import resolve_model_alias

    available = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "deepseek-v4-flash"]
    assert resolve_model_alias("sol", available) == "gpt-5.6-sol"
    assert resolve_model_alias("SOL", available) == "gpt-5.6-sol"
    assert resolve_model_alias(" terra ", available) == "gpt-5.6-terra"
    assert resolve_model_alias("d4flash", available) == "deepseek-v4-flash"
    assert resolve_model_alias("deepseek", available) == "deepseek-v4-flash"
    assert resolve_model_alias("5.5", available) == "gpt-5.5"
    assert resolve_model_alias("gpt-5.6-sol", available) == "gpt-5.6-sol"
    # 唯一子串："luna" 亦为别名，"5.6-luna" 子串唯一
    assert resolve_model_alias("5.6-luna", available) == "gpt-5.6-luna"


def test_resolve_model_alias_errors(history_payload):
    from codex_radar.history_parser import resolve_model_alias

    available = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "deepseek-v4-flash"]
    # 歧义子串："5.6" 匹配三个模型
    with pytest.raises(ValueError, match="多个模型"):
        resolve_model_alias("5.6", available)
    # 不存在
    with pytest.raises(ValueError, match="未找到模型"):
        resolve_model_alias("gpt-7", available)
    # 空输入
    with pytest.raises(ValueError, match="请指定模型名"):
        resolve_model_alias("  ", available)
    # 无可用模型
    with pytest.raises(ValueError, match="没有可用模型"):
        resolve_model_alias("sol", [])
