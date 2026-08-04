"""智力效率数据解析测试（含站点口径公式验证）。"""

from __future__ import annotations

import math

import pytest

from codex_radar.errors import RadarParseError
from codex_radar.radar_parser import (
    COMBINED_COST_WEIGHT,
    parse_intelligence_efficiency,
)

URL = "https://codexradar.com/api/intelligence-efficiency"


def _point(snapshot, model: str, effort: str):
    for p in snapshot.points:
        if p.model == model and p.effort == effort:
            return p
    raise AssertionError(f"missing point {model}@{effort}")


def test_parse_basic_structure(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    assert snapshot.schema == 1
    assert snapshot.combos_count == 5
    assert snapshot.tasks_count == 3
    assert len(snapshot.points) == 5
    assert snapshot.source_url == URL


def test_parse_metadata_times(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    assert snapshot.updated_at == "2026-08-04T03:15:07.728451+00:00"
    assert snapshot.source_updated_at == "2026-08-03T12:00:00+08:00"
    assert snapshot.token_pricing["version"] == "test-pricing-2026-08-01"


def test_parse_score_and_averages(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)

    sol_low = _point(snapshot, "gpt-5.6-sol", "low")
    # passed=2/3 -> IQ = 2/3*150 = 100
    assert sol_low.passed == 2
    assert sol_low.valid_tasks == 3
    assert sol_low.iq == pytest.approx(100.0)
    # 耗时均值 = (600+300+120)/60/3 = 5.6667 分钟
    assert sol_low.average_minutes == pytest.approx(17 / 3, abs=1e-9)
    assert sol_low.duration_samples == 3
    # 花费均值 = (1.0+0.5+0.25)/3
    assert sol_low.average_price_usd == pytest.approx(1.75 / 3, abs=1e-9)
    assert sol_low.price_samples == 3
    assert sol_low.total_runs == 3


def test_parse_skips_runner_without_passed(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    terra = _point(snapshot, "gpt-5.6-terra", "max")
    # task-2 的 runner 缺 passed 字段：不计入有效题数，但耗时/花费仍计入
    assert terra.valid_tasks == 2
    assert terra.passed == 1
    assert terra.iq == pytest.approx(75.0)
    # 耗时样本：task-1(15min) + task-2(5min) + task-3(7.5min) => (15+5+7.5)/3
    assert terra.average_minutes == pytest.approx(27.5 / 3)
    # 花费样本：task-2 无 actual_cost_usd 不计入 => (2+1)/2
    assert terra.average_price_usd == pytest.approx(1.5)
    assert terra.duration_samples == 3
    assert terra.price_samples == 2


def test_parse_ultra_excludes_incomplete_cost(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    ultra = _point(snapshot, "gpt-5.6-luna", "ultra")
    # task-1 cost_complete=false：花费被排除
    assert ultra.price_samples == 1
    assert ultra.incomplete_cost_samples == 1
    assert ultra.average_price_usd == pytest.approx(8.0)
    # 耗时不受影响
    assert ultra.average_minutes == pytest.approx(27.5)
    assert ultra.iq == pytest.approx(150.0)


def test_unknown_model_is_listed(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    future = _point(snapshot, "future-model-x", "high")
    assert future.iq == pytest.approx(150.0)
    assert future.average_minutes == pytest.approx(1.0)
    assert future.average_price_usd == pytest.approx(0.1)


def test_combined_cost_math(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    sol_low = _point(snapshot, "gpt-5.6-sol", "low")
    terra = _point(snapshot, "gpt-5.6-terra", "max")
    ultra = _point(snapshot, "gpt-5.6-luna", "ultra")

    def raw(price, minutes):
        return price * math.pow(minutes / 10.0, COMBINED_COST_WEIGHT) * 100.0

    assert sol_low.raw_combined_cost == pytest.approx(raw(1.75 / 3, 17 / 3))
    assert terra.raw_combined_cost == pytest.approx(raw(1.5, 27.5 / 3))
    assert ultra.raw_combined_cost == pytest.approx(raw(8.0, 27.5))

    # 归一化：最高综合成本 = 100，其余按比例
    max_raw = max(p.raw_combined_cost for p in snapshot.points if p.raw_combined_cost)
    for point in snapshot.points:
        if point.raw_combined_cost is None:
            continue
        assert point.combined_cost_index == pytest.approx(
            point.raw_combined_cost / max_raw * 100.0
        )
    assert ultra.combined_cost_index == pytest.approx(100.0)


def test_points_grouped_by_model_in_order(efficiency_payload):
    snapshot = parse_intelligence_efficiency(efficiency_payload, url=URL)
    grouped = snapshot.points_by_model()
    assert [model for model, _ in grouped] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "future-model-x",
    ]
    sol_points = grouped[0][1]
    assert [p.effort for p in sol_points] == ["low", "high"]


def test_no_valid_points_raises(efficiency_payload):
    payload = dict(efficiency_payload)
    # cells 存在但没有任何有效 runner：所有组合都无有效判分
    payload["cells"] = {key: {"ran_by": []} for key in payload["cells"]}
    with pytest.raises(RadarParseError) as exc:
        parse_intelligence_efficiency(payload, url=URL)
    assert "没有任何模型" in str(exc.value)


def test_schema_mismatch_raises(efficiency_payload):
    payload = dict(efficiency_payload)
    payload["schema"] = 2
    with pytest.raises(RadarParseError) as exc:
        parse_intelligence_efficiency(payload, url=URL)
    assert "schema" in str(exc.value)
