"""数据字段缺失 / 页面结构变化测试：必须报可诊断错误，不得静默返回。"""

from __future__ import annotations

import pytest

from codex_radar.errors import RadarParseError
from codex_radar.history_parser import parse_iq_history
from codex_radar.radar_parser import parse_intelligence_efficiency

RADAR_URL = "https://codexradar.com/api/intelligence-efficiency"
HISTORY_URL = "https://api.codexradar.com/api/v1/iq-history"


def test_radar_top_level_not_object():
    with pytest.raises(RadarParseError) as exc:
        parse_intelligence_efficiency("not json", url=RADAR_URL)
    msg = str(exc.value)
    assert "顶层" in msg and RADAR_URL in msg


def test_radar_missing_combos(efficiency_payload):
    payload = dict(efficiency_payload)
    payload["combos"] = []
    with pytest.raises(RadarParseError, match="combos"):
        parse_intelligence_efficiency(payload, url=RADAR_URL)


def test_radar_missing_tasks(efficiency_payload):
    payload = dict(efficiency_payload)
    del payload["tasks"]
    with pytest.raises(RadarParseError, match="tasks"):
        parse_intelligence_efficiency(payload, url=RADAR_URL)


def test_radar_missing_cells(efficiency_payload):
    payload = dict(efficiency_payload)
    del payload["cells"]
    with pytest.raises(RadarParseError, match="cells"):
        parse_intelligence_efficiency(payload, url=RADAR_URL)


def test_radar_combo_without_model_or_effort(efficiency_payload):
    payload = dict(efficiency_payload)
    payload["combos"] = [{"model": "", "effort": "high"}]
    with pytest.raises(RadarParseError, match="model/effort"):
        parse_intelligence_efficiency(payload, url=RADAR_URL)


def test_radar_cell_with_bad_shape(efficiency_payload):
    """cells 值不是对象时按无数据跳过，不应崩溃。"""
    payload = dict(efficiency_payload)
    payload["cells"]["task-1|gpt-5.6-sol|low"] = "broken"
    snapshot = parse_intelligence_efficiency(payload, url=RADAR_URL)
    assert snapshot.points  # 其余组合仍正常


def test_radar_runner_not_object(efficiency_payload):
    payload = dict(efficiency_payload)
    payload["cells"]["task-1|gpt-5.6-sol|low"] = {"ran_by": ["not-a-dict"]}
    snapshot = parse_intelligence_efficiency(payload, url=RADAR_URL)
    sol_low = next(p for p in snapshot.points if p.effort == "low")
    # task-1 无有效 runner，不计入有效题数
    assert sol_low.valid_tasks == 2


def test_history_series_value_not_list(history_payload):
    payload = dict(history_payload)
    payload["gpt-5.6-sol"] = {"ts": "2026-08-01T00:00:00Z"}
    with pytest.raises(RadarParseError, match="列表"):
        parse_iq_history(payload, url=HISTORY_URL)


def test_history_point_missing_ts(history_payload):
    payload = dict(history_payload)
    payload["gpt-5.6-sol"] = [{"score": 90.0, "n": 10}]
    with pytest.raises(RadarParseError, match="ts"):
        parse_iq_history(payload, url=HISTORY_URL)


def test_history_null_scores_only_are_ok(history_payload):
    """全 null 分数（如 deepseek 无样本）结构合法，应保留而非报错。"""
    payload = {
        "gpt-5.6-sol": [
            {"ts": "2026-08-03T00:00:00Z", "score": None, "n": 0},
            {"ts": "2026-08-03T01:00:00Z", "score": None, "n": 0},
        ]
    }
    snapshot = parse_iq_history(payload, hours=72, url=HISTORY_URL)
    assert len(snapshot.series) == 1
    assert not snapshot.series[0].has_data()
