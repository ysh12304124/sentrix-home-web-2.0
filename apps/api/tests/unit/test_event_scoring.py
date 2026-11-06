"""事件候选打分的八分量。数值逐条对回 1.0 ``backend/db.py:2000-2080``。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.memories.events import (
    CANDIDATE_THRESHOLD,
    CANDIDATE_WINDOW_HOURS,
    SPLIT_GUARD_SEMANTIC_VISUAL_CONFLICT,
    WEIGHTS,
    EventAnchor,
    EventAnchorSet,
    ObservationAnchor,
    VisualSignal,
    normalise_tokens,
    rank_candidates,
    score_event_candidate,
    within_candidate_window,
)

NOW = datetime(2017, 10, 5, 12, 0, 0)
XIAOSHAN = GpsPoint(30.1839, 120.2642)


def anchor(**overrides) -> ObservationAnchor:
    fields: dict[str, object] = {
        "captured_at": NOW,
        "visual_place": "公园",
        "visual_event_type": "户外活动",
        "activity": "拍照",
        "objects": frozenset({"相机"}),
    }
    fields.update(overrides)
    return ObservationAnchor(**fields)  # type: ignore[arg-type]


def event(**overrides) -> EventAnchorSet:
    fields: dict[str, object] = {
        "event_id": "evt_1",
        "time_start": NOW,
        "activity": "拍照",
        "anchors": (
            EventAnchor(
                captured_at=NOW,
                visual_place="公园",
                visual_event_type="户外活动",
                gps=XIAOSHAN,
            ),
        ),
        "people": frozenset(),
        "object_sets": (frozenset({"相机"}),),
    }
    fields.update(overrides)
    return EventAnchorSet(**fields)  # type: ignore[arg-type]


def test_weights_are_the_1_0_eight_component_table():
    """八项权重之和是 **1.20**，不是 1.0——时空加成是这八项之外再叠加的。

    这不是笔误：1.0 的 ``total`` 就是这么算的，最后靠 ``max(0.0, min(1.0, total))``
    收口。照「权重必须归一」去改会把饱和区的形状改掉。
    """
    assert pytest.approx(sum(WEIGHTS.values()), abs=1e-9) == 1.20
    assert set(WEIGHTS) == {
        "time",
        "location",
        "visual_place",
        "event_type",
        "activity",
        "objects",
        "person",
        "visual_boost",
    }


def test_perfect_match_saturates():
    score = score_event_candidate(
        anchor=anchor(gps=XIAOSHAN, location="30.183900,120.264200"),
        event=event(),
        visual=VisualSignal(similarity=1.0, available=True),
    )
    assert score.time == pytest.approx(1.0)
    assert score.location == pytest.approx(1.0)
    assert score.visual_place == pytest.approx(1.0)
    assert score.activity == pytest.approx(0.9)
    assert score.objects == pytest.approx(0.8)
    assert score.visual_boost == pytest.approx(1.0)
    assert score.spatio_temporal_bonus == pytest.approx(0.20)
    assert score.total == pytest.approx(1.0)


def test_missing_time_gives_the_neutral_quarter_point():
    """1.0 ``db.py:2006`` 的 ``else: time_score = 0.25``——不是 0。"""
    score = score_event_candidate(anchor=anchor(captured_at=None), event=event(), visual=VisualSignal())
    assert score.time == pytest.approx(0.25)


def test_gps_decays_linearly_to_zero_at_two_km():
    """``max(0, 1 - distance / 2.0)``：1 公里给 0.5，2 公里给 0。"""
    one_km = score_event_candidate(
        anchor=anchor(gps=GpsPoint(30.1929, 120.2642)),
        event=event(anchors=(EventAnchor(gps=XIAOSHAN),)),
        visual=VisualSignal(),
    )
    assert one_km.location == pytest.approx(0.5, abs=0.02)
    far = score_event_candidate(
        anchor=anchor(gps=GpsPoint(30.2019, 120.2642)),
        event=event(anchors=(EventAnchor(gps=XIAOSHAN),)),
        visual=VisualSignal(),
    )
    assert far.location == pytest.approx(0.0)


def test_event_type_match_is_worth_eight_tenths():
    score = score_event_candidate(anchor=anchor(), event=event(), visual=VisualSignal())
    assert score.event_type == pytest.approx(0.8)


def test_event_type_mismatch_is_zero():
    score = score_event_candidate(
        anchor=anchor(visual_event_type="室内聚餐"), event=event(), visual=VisualSignal()
    )
    assert score.event_type == pytest.approx(0.0)


def test_activity_substring_counts_as_match():
    """1.0 注释：同一事件的措辞不同是常态，不惩罚。"""
    score = score_event_candidate(
        anchor=anchor(activity="摆花"), event=event(activity="在客厅摆花"), visual=VisualSignal()
    )
    assert score.activity == pytest.approx(0.9)


def test_visual_boost_floor_and_ceiling():
    """0.70 以下不给分，1.00 给满，中间线性。"""

    def boost(similarity: float) -> float:
        return score_event_candidate(
            anchor=anchor(), event=event(), visual=VisualSignal(similarity=similarity, available=True)
        ).visual_boost

    assert boost(0.70) == pytest.approx(0.0)
    assert boost(0.85) == pytest.approx(0.5)
    assert boost(1.00) == pytest.approx(1.0)
    assert boost(0.50) == pytest.approx(0.0)


def test_unavailable_visual_signal_gives_zero_boost_not_false_conflict():
    """``available=False`` 与「相似度 0」必须分得开——合成一个 0 会让 split_guard 误触发。"""
    score = score_event_candidate(
        anchor=anchor(), event=event(), visual=VisualSignal(similarity=0.0, available=False)
    )
    assert score.visual_boost == pytest.approx(0.0)
    assert score.visual_available is False


def test_split_guard_fires_on_conflict_without_corroboration():
    score = score_event_candidate(
        anchor=anchor(activity="室内聚餐", visual_event_type="室内聚餐", objects=frozenset(), gps=XIAOSHAN),
        event=event(
            activity="户外徒步",
            anchors=(EventAnchor(gps=XIAOSHAN, visual_event_type="户外活动"),),
            object_sets=(),
        ),
        visual=VisualSignal(similarity=0.20, available=True),
    )
    assert score.semantic_conflict is True
    assert score.split_guard == SPLIT_GUARD_SEMANTIC_VISUAL_CONFLICT
    assert score.total == pytest.approx(0.0)


def test_split_guard_does_not_fire_when_objects_bridge():
    """有共同物件做桥时语义冲突不拆——那是同一场活动的不同片段。"""
    score = score_event_candidate(
        anchor=anchor(activity="室内聚餐", visual_event_type="室内聚餐", objects=frozenset({"相机"})),
        event=event(activity="户外徒步", anchors=(EventAnchor(visual_event_type="户外活动"),)),
        visual=VisualSignal(similarity=0.20, available=True),
    )
    assert score.semantic_conflict is True
    assert score.split_guard is None
    assert score.total > 0.0


def test_spatio_temporal_bonus_requires_both_ninety_percent():
    score = score_event_candidate(
        anchor=anchor(captured_at=NOW + timedelta(minutes=30), gps=GpsPoint(30.1929, 120.2642)),
        event=event(anchors=(EventAnchor(gps=XIAOSHAN),)),
        visual=VisualSignal(),
    )
    assert score.time >= 0.90
    assert score.location < 0.90
    assert score.spatio_temporal_bonus == pytest.approx(0.0)


def test_total_is_clamped_to_one():
    score = score_event_candidate(
        anchor=anchor(gps=XIAOSHAN, people=frozenset({"ent_1"})),
        event=event(people=frozenset({"ent_1"})),
        visual=VisualSignal(similarity=1.0, available=True),
    )
    assert 0.0 <= score.total <= 1.0


def test_threshold_constant_is_half():
    assert CANDIDATE_THRESHOLD == 0.50


@pytest.mark.parametrize(
    ("delta_hours", "expected"),
    [(0.0, True), (5.9, True), (6.0, True), (6.1, False)],
)
def test_candidate_window_is_six_hours(delta_hours: float, expected: bool):
    assert within_candidate_window(NOW, NOW + timedelta(hours=delta_hours)) is expected
    assert CANDIDATE_WINDOW_HOURS == 6.0


def test_window_with_missing_time_is_open():
    assert within_candidate_window(None, NOW) is True
    assert within_candidate_window(NOW, None) is True


def test_rank_candidates_orders_by_score_then_time_then_id():
    strong = event(event_id="evt_a", time_start=NOW)
    weak = event(event_id="evt_b", time_start=NOW - timedelta(hours=1))
    ranked = rank_candidates(
        [
            (weak, score_event_candidate(anchor=anchor(), event=weak, visual=VisualSignal())),
            (strong, score_event_candidate(anchor=anchor(), event=strong, visual=VisualSignal())),
        ]
    )
    assert [item[0].event_id for item in ranked] == ["evt_a", "evt_b"]


def test_rank_candidates_breaks_ties_by_id_ascending():
    first = event(event_id="evt_a")
    second = event(event_id="evt_b")
    score = score_event_candidate(anchor=anchor(), event=first, visual=VisualSignal())
    ranked = rank_candidates([(second, score), (first, score)])
    assert [item[0].event_id for item in ranked] == ["evt_a", "evt_b"]


def test_normalise_tokens_splits_chinese_punctuation():
    assert normalise_tokens("花、草") == frozenset({"花", "草"})
    assert normalise_tokens(["A", " b "]) == frozenset({"a", "b"})
    assert normalise_tokens(None) == frozenset()


def test_breakdown_keeps_1_0_field_names():
    """落库字段名与 1.0 的 ``aggregation_breakdown_json`` 对齐，读旧数据的人不用换字典键。"""
    score = score_event_candidate(anchor=anchor(), event=event(), visual=VisualSignal())
    breakdown = score.breakdown()
    assert breakdown["confirmed_people"] == score.person
    assert "visual_similarity" in breakdown
    assert "split_guard" in breakdown
    assert breakdown["spatio_temporal_bonus"] == score.spatio_temporal_bonus


def test_mixed_timezone_events_do_not_claim_a_time_match():
    aware = NOW.replace(tzinfo=timezone(timedelta(hours=8)))
    assert not within_candidate_window(NOW, aware)
    assert not within_candidate_window(aware, NOW)
    score = score_event_candidate(anchor=anchor(captured_at=aware), event=event(), visual=VisualSignal())
    assert score.time == 0.25
    assert within_candidate_window(aware, aware.astimezone(UTC))
