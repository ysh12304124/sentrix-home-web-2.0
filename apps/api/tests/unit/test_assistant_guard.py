"""答案门禁与兜底答复的单元测试。

门禁只做确定性判定，**不做语义等价**——「三个人」和「3」是不是同一个数，正则判不对，
只会误伤事实正确的回答（1.0 为此把原来的一批正则规则删掉了，见 1.0 ``final_guard.py:1-27``）。
语义评审是 L2（判分模型）的事，属于 S6。

本文件钉的是 **1.0 ``backend/agent_runtime/final_guard.py`` 的规则表**：每条规则的正则、
每条规则的文案编码、规则的顺序、以及三道「保守门槛」（``_HEDGE`` / ``_CONDITION_LEVEL_DENY``
/ ``_ANY_DENIAL``）。最后两道是 2.0 曾经删掉、这次退回来的——删掉它们会让正常回答被误拦，
所以各配了正反两个用例。

每条规则都配一个反向用例：构造函数让它通过，证明它不是恒真的。
"""

from __future__ import annotations

from sentrix.domain.assistant.emergency import clarify_answer, fallback_answer
from sentrix.domain.assistant.evidence_ledger import (
    EvidenceLedger,
    EvidenceRequirement,
    EvidenceType,
)
from sentrix.domain.assistant.final_guard import (
    HARD_GUARD_CODES,
    FinalGuard,
    GuardFacts,
    answered_with_gap,
)
from sentrix.domain.assistant.models import TerminationReason
from sentrix.domain.assistant.profile import DEFAULT_PROFILE
from sentrix.domain.assistant.task_state import TaskState, ToolCallRecord
from sentrix.domain.assistant.tools import FINAL_ANSWER_TOOL

DESCRIPTIONS = {
    EvidenceType.PHOTO_SET: "从相册里取到与问题相关的照片。",
    EvidenceType.DELIVERED_PHOTO: "把要展示的照片交付给用户，让它们真的能打开。",
    EvidenceType.PERSON_PROFILE: "读取这位已确认人物的家庭角色。",
}


def ledger(*evidence_types: EvidenceType) -> EvidenceLedger:
    return EvidenceLedger(
        scope_id="album1",
        requirements=[
            EvidenceRequirement(id=f"r{i}", evidence_type=t, description=DESCRIPTIONS[t])
            for i, t in enumerate(evidence_types)
        ],
    )


def make_state(records: tuple[ToolCallRecord, ...] = ()) -> TaskState:
    state = TaskState(
        turn_id="turn_1",
        conversation_id="conv_1",
        scope_id="album1",
        question="给我看看在海边的照片",
        profile=DEFAULT_PROFILE,
    )
    for record in records:
        state.record(record)
    return state


def search_record(total: int, *, has_more: bool = False, call_id: str = "call_search") -> ToolCallRecord:
    return ToolCallRecord(
        call_id=call_id,
        tool="search_memories",
        signature=call_id,
        status="ok",
        observation={"result_set_id": "rs_1", "total": total, "has_more": has_more},
    )


def deliver_record(delivered: int = 2, call_id: str = "call_deliver") -> ToolCallRecord:
    """一次带交付的终止动作。交付是 ``final_answer`` 上的字段，不是一次独立调用。"""
    return ToolCallRecord(
        call_id=call_id,
        tool=FINAL_ANSWER_TOOL,
        signature=call_id,
        status="ok",
        observation={"result_set_id": "rs_1", "delivered": delivered, "rejected": []},
    )


def check(
    answer: str,
    *,
    records: tuple[ToolCallRecord, ...] = (),
    result_mode: str | None = None,
) -> str:
    """返回命中的规则码，通过则返回空串。"""
    facts = GuardFacts.from_state(make_state(records))
    if result_mode is not None:
        facts = GuardFacts(
            tool_results=facts.tool_results,
            has_more=facts.has_more,
            result_mode=result_mode,
        )
    verdict = FinalGuard().check(answer=answer, facts=facts)
    return "" if verdict.passed else verdict.code


# ---- 安全硬拦：1.0 final_guard.py:162-178 的五条 -----------------------------


def test_an_internal_asset_id_in_the_answer_is_blocked():
    assert check("这张照片的编号是 asset_9f3a21b7c4，可以打开。") == "internal_id_leak"


def test_a_short_internal_prefix_is_not_enough_to_block():
    """1.0 的正则要求后面至少 6 位十六进制——``asset_1`` 这种不是真 id，不该误伤。"""
    assert check("这张照片叫 asset_1，我觉得很好看。") == ""


def test_a_database_table_name_in_the_answer_is_blocked():
    assert check("我从 agent_result_sets 里取到了这几张。") == "table_name_leak"


def test_an_internal_schema_field_in_the_answer_is_blocked():
    assert check("当前 scope_id: album_cbfbfce14789") == "table_name_leak"


def test_a_bare_schema_word_without_a_value_is_not_blocked():
    """``scope_id`` 后面必须跟 ``:`` 或 ``=`` 才算泄漏内部结构。光提到这个词不算。"""
    assert check("我按当前相册（scope_id）检索了一下。") == ""


def test_mentioning_another_album_is_blocked():
    assert check("我也可以帮你看看别的相册里的照片。") == "viewer_escape"


def test_mentioning_other_users_is_blocked():
    assert check("其他用户的照片我看不到。") == "viewer_escape"


def test_talking_about_people_in_a_family_photo_is_not_an_escape():
    """家人合影里的「与他人」不该触发 ``viewer_escape``。

    1.0 的正则把裸词 ``他人`` 单独成项（``final_guard.py:174``，2.0 原先逐字照搬）。
    2026-10-10 那轮实测有 5 道题被它**硬拦**——硬拦不重试、整轮终结走确定性兜底、
    均分 0，而问题原文是「我想找带强子第一次近距离看活海星和鲎的那次参观照片」
    这类。相册里全是家人，「与他人一起拍的」是日常说法。
    """
    assert check("这张是与他人一起拍的合影，一共三个人。") == ""
    assert check("画面里有他和他人合影的场景。") == ""


def test_claiming_access_to_other_peoples_albums_is_still_blocked():
    """收紧不等于放行：真正的越权表达照样命中——那才是这条规则存在的理由。"""
    assert check("我还能看到他人的相册。") == "viewer_escape"
    assert check("他人的隐私照片我也能看到。") == "viewer_escape"


def test_claiming_a_write_is_blocked():
    assert check("已经帮你删除了那两张照片。") == "write_not_allowed"


def test_an_imperative_about_deleting_is_not_a_write_claim():
    """用户说「删除」而模型复述，不是「已经写入了」——1.0 的正则要求
    ``(?:已经|帮你|可以)`` 前缀，就是为了避开这种复述。"""
    assert check("如果你想删除相册里的照片，可以到设置里操作。") == ""


def test_every_hard_code_is_in_the_1_0_hard_block_set():
    assert {
        "internal_id_leak",
        "table_name_leak",
        "viewer_escape",
        "write_not_allowed",
    } == HARD_GUARD_CODES


# ---- 模板占位符 -------------------------------------------------------------


def test_an_unfilled_placeholder_is_blocked():
    assert check("这是你在[地点名称]拍的照片。") == "placeholder_leak"


def test_a_bracketed_but_filled_answer_is_not_blocked():
    assert check("这是你在海边拍的照片。") == ""


# ---- 未检索却断言没有 -------------------------------------------------------


def test_denying_without_ever_searching_is_blocked():
    assert check("相册里没有找到海边的照片。") == "denial_without_search"


def test_denying_after_a_search_is_not_blocked():
    assert check("相册里没有找到海边的照片。", records=(search_record(0),)) == ""


# ---- 存在性矛盾（Truth Guard） ----------------------------------------------


def test_claiming_a_hit_when_the_search_was_empty_is_blocked():
    assert check("找到了两张海边的照片。", records=(search_record(0),)) == "fabrication_from_empty"


def test_a_hedged_answer_about_an_empty_search_is_not_blocked():
    """``_HEDGE``：诚实的不确定不是断言。"""
    assert check("这次没有找到，所以我无法确认你去过海边。", records=(search_record(0),)) == ""


def test_an_answer_with_any_negation_is_not_treated_as_a_found_claim():
    """``_ANY_DENIAL``——2.0 曾经删掉这道门槛，理由是它把「没找到 A，但找到了 B」也一起
    豁免了。1.0 的注释写的是这道门槛**刻意**保守。删掉它会让下面这句话被误判成编造，
    而它是对检索结果的如实描述。"""
    assert check("没有找到爬山的，但找到了这两张海边的。", records=(search_record(0),)) == ""


def test_denying_while_the_search_had_hits_is_blocked():
    assert check("相册里没有找到海边的照片。", records=(search_record(3),)) == "omission_conflict"


def test_a_conditional_denial_is_not_an_omission():
    """``_CONDITION_LEVEL_DENY``：「没找到能确认爬山的记录」≠「没有找到照片」。"""
    assert check("没有找到能确认爬山这个说法的记录。", records=(search_record(3),)) == ""


# ---- 交付结构（1.0 final_guard.py:106-112） ---------------------------------


def test_delivering_nothing_is_not_blocked_any_more():
    """**``delivery_contradiction`` 已删除**（2026-10-09）。

    它要求 ``delivered_count == 0``（**整数** 0），而那条记录在 2.0 的形状下取不到：
    ``deliver()`` 成功时 ``delivered`` 恒 ``>= 1``，失败时走 ``_failure``（观察里没有
    这一格）、``_finish_with_final_answer`` 又把它记成 ``status="error"``，而
    ``from_state`` 只读 ``ok``。

    更要紧的是**前提变了**：规则设计于「省略的时代」（1.0 的 final 有 49% 不带
    ``selected_image_handles``）；W6 把那个字段做成必填之后，零交付是一个明确写下的
    ``[]``，不是遗漏。留在那里会在约 300 道「检索取完但没交图」的题上各花一个模型步，
    而且打回的是模型的**显式选择**。
    """
    records = (
        search_record(2, call_id="s1"),
        deliver_record(0, call_id="d0"),
        search_record(2, call_id="s2"),
    )

    assert check("这是你要的海边照片。", records=records) == ""


def test_asking_for_everything_while_more_remains_is_blocked():
    assert (
        check(
            "这是你要的全部照片。",
            records=(search_record(20, has_more=True), deliver_record(2)),
            result_mode="all",
        )
        == "all_requested_but_has_more"
    )


# ---- 顺序与配置 -------------------------------------------------------------


def test_safety_rules_run_before_the_existence_rules():
    """一条回答同时踩安全和存在性时，返回的是安全那条——1.0 ``check()`` 的顺序。"""
    assert check("asset_9f3a21b7c4 里没有找到照片。", records=(search_record(3),)) == "internal_id_leak"


def test_a_clean_answer_passes_every_rule():
    assert check("这是你要的两张海边照片。", records=(search_record(2), deliver_record(2))) == ""


def test_there_is_no_evidence_gate_rule():
    """S3 自造的那条「证据门」已经删掉：需求还开着、预算也还有，**不再**拦最终答案。

    这里用一句正常的收尾话，证明门禁不会因为「账本还开着」就拦下来。
    """
    state = make_state((search_record(0),))
    verdict = FinalGuard().check(
        answer="这次没有找到海边的照片，我先说清楚。", facts=GuardFacts.from_state(state)
    )

    assert verdict.passed


def test_from_state_reads_the_retrieval_bookkeeping_like_1_0():
    """``GuardFacts.from_state`` 复刻 1.0 ``result_set.py:378/402`` 的检索簿记。

    **只剩检索那一半。** 交付侧的簿记（``delivery_state`` / ``delivered_count``）随
    ``delivery_contradiction`` 一起删除——那条规则是它们唯一的读者。留一格没人读的
    状态比删掉更贵：它看起来还在守什么。
    """
    empty = GuardFacts.from_state(make_state())
    assert (empty.has_more, empty.tool_results) == (False, ())

    searched = GuardFacts.from_state(make_state((search_record(5, has_more=True),)))
    assert searched.has_more is True
    assert searched.tool_results == ({"tool": "search_memories", "total": 5},)

    # 交付记录不再进这份投影：它不是检索的产物。
    delivered = GuardFacts.from_state(make_state((search_record(5), deliver_record(3))))
    assert delivered.tool_results == ({"tool": "search_memories", "total": 5},)
    assert delivered.has_more is False


# ---- 兜底答复 ---------------------------------------------------------------


def test_the_fallback_says_what_was_found_and_what_was_not():
    text = fallback_answer(
        reason=TerminationReason.TOOL_BUDGET_EXHAUSTED,
        ledger=ledger(EvidenceType.PHOTO_SET),
        photo_count=3,
        delivered_count=0,
    )

    assert "3 张" in text
    assert "工具调用预算用尽" in text


def test_the_fallback_is_honest_when_nothing_was_found():
    text = fallback_answer(
        reason=TerminationReason.MODEL_ERROR,
        ledger=ledger(EvidenceType.PHOTO_SET),
        photo_count=0,
        delivered_count=0,
    )

    assert "没有检索到照片" in text
    assert "模型服务不可用" in text


def test_the_fallback_carries_the_service_side_reason_when_there_is_one():
    """服务这一侧的失败要带上端点与服务端给的原因（用户 2026-09-25 裁决二）。

    「模型服务不可用」单独一句不可执行：8100 缺 tool-call flag 时，服务端那句 400 已经把
    根因写得清清楚楚，它不能只落在日志里。
    """
    text = fallback_answer(
        reason=TerminationReason.MODEL_ERROR,
        ledger=ledger(EvidenceType.PHOTO_SET),
        photo_count=0,
        delivered_count=0,
        detail='模型端点 http://127.0.0.1:8100/v1 上的 gemma4-12b-it 返回 400：{"error":…}',
    )

    assert "模型服务不可用" in text
    assert "http://127.0.0.1:8100/v1" in text
    assert "400" in text


def test_the_fallback_invents_no_endpoint_when_there_is_no_detail():
    """反向门禁：没有服务侧说明时不许凭空补一个端点出来。"""
    text = fallback_answer(
        reason=TerminationReason.MODEL_ERROR,
        ledger=ledger(EvidenceType.PHOTO_SET),
        photo_count=0,
        delivered_count=0,
    )

    assert "模型服务不可用" in text
    assert "http" not in text


def test_clarify_answer_is_used_before_a_ledger_exists():
    assert "超时" in clarify_answer(TerminationReason.TIMEOUT)


def test_answered_with_gap_tracks_the_ledger():
    assert answered_with_gap(ledger(EvidenceType.PHOTO_SET))
    assert not answered_with_gap(ledger())


def test_a_blocked_answer_carries_the_offending_text():
    """**打回的那段原文要留下来。**（2026-10-10 补）

    在此之前 `GuardVerdict` 只有规则码——而 `viewer_escape` 的正则里 ``他人`` 是个日常
    词（家人合影里「与他人」很常见），是真命中还是误伤，**只有看到原句才判得了**。
    实测 20261010 那一轮 10 道题被它硬拦（整轮终结、走确定性兜底），原文一个字都没留下，
    于是那 10 道完全不可诊断。

    **2026-10-15 补：光有 evidence 不够，正则本身也收紧了**（见
    ``test_talking_about_people_in_a_family_photo_is_not_an_escape``）。这条用例原先
    用的样例正是那类误伤句（「那天我和他人一起在桥上拍了合影」），现在换成一句真正
    越权的回答——否则它钉的就不是 ``viewer_escape`` 了。
    """
    verdict = FinalGuard().check(
        answer="我还能翻看他人的相册。",
        facts=GuardFacts(),
    )

    assert verdict.code == "viewer_escape"
    assert "他人" in verdict.evidence
    # 留的是命中处前后一小段，不是整段回答。
    assert len(verdict.evidence) <= 80


def test_a_passing_answer_carries_no_evidence():
    """反向：通过的裁决不该带一段原文——那会让「有没有命中」这件事变得含糊。"""
    assert FinalGuard().check(answer="海边有两张照片。", facts=GuardFacts()).evidence == ""


def test_text_candidates_do_not_prove_a_requested_field_exists():
    record = ToolCallRecord(
        call_id="t",
        tool="search_text_memories",
        signature="q",
        status="ok",
        observation={"total": 6, "preview": [{"media_kind": "text"}]},
    )
    assert check("没有找到活动的具体费用，已有通知没有列价格。", records=(record,)) == ""
    # The original photo existence check still applies.
    assert check("没有找到相关照片。", records=(search_record(6),)) == "omission_conflict"


def test_ordinary_english_observations_are_not_a_database_disclosure_in_text_answers():
    record = ToolCallRecord(
        call_id="t", tool="search_text_memories", signature="q", status="ok", observation={"total": 1}
    )
    assert (
        check("The provided text observations do not contain the requested address.", records=(record,)) == ""
    )
    assert check("SELECT * FROM observations", records=(record,)) == "table_name_leak"
    assert check("The observations table stores records.", records=(record,)) == "table_name_leak"
    assert check("`observations`", records=(record,)) == "table_name_leak"
    assert check("entity_mentions", records=(record,)) == "table_name_leak"
