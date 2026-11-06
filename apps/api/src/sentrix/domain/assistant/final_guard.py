"""答案门禁：模型给出最终回答之前，用确定性规则过一道。

这是 1.0 ``backend/agent_runtime/final_guard.py`` 的**规则表忠实移植**。1.0 的模块
docstring 把边界写得很清楚，这里照抄那份判断：

    设计原则：回答"是否合格"（事实正确性、数字/词语等价、编造/矛盾/漏报）由 L2 模型
    评审判断，L1 不做词语/数字正则的合格性匹配——例如"三个人"与"3"是等价表达，不得由
    死代码判错。

    L1 保留的确定性检查分两类：纯结构兜底（安全硬拦 / 模板占位符 / 未检索却断言没有 /
    all 请求但还有更多 / 声称交付但交付数为 0），以及最小确定性存在性检查（工具确认
    存在却整体否认、工具确认不存在却断言找到）。

S3 曾经在这张表上做了两处改动，审计后都退了回来：

1. **自己加了一条「证据门」**——「还有开着的证据需求、预算也还有，就先别收尾」。
   1.0 没有这条规则。1.0 与它最接近的是 ``delivery_contradiction``：**检索已经把结果
   取完（``delivery_state == "complete"``）而交付数为 0** 才算矛盾。自加的那条更宽
   （只要需求还开着就拦），既没有 1.0 的证据，也把「模型如实说取不到」一并拦掉了。
   这里换回 1.0 的原规则。
2. **去掉了 ``_ANY_DENIAL`` 的整句否定门槛**，理由是「它把『没找到 A，但找到了 B』也
   一起豁免了」。1.0 的注释写的是这条门槛是**刻意**的：「含明确否定词时不视为断言找到/
   交付」。删掉它会让 ``fabrication_from_empty`` 在「只找到 3 张，没有更多了」这类
   正常回答上误报。没有证据支持这次删除，退回来。

1.0 里有两类规则在 S3 无处落地，理由记在各自的注释里：``query_memory_facts`` 的
``fact_exists_contradiction``（S3 没有这个工具）与 ``all_requested_but_has_more`` 的
``result_mode``（S3 的 ``search_memories`` 没有 ``mode`` 参数）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from sentrix.domain.assistant.evidence_ledger import EvidenceLedger
from sentrix.domain.assistant.task_state import TaskState
from sentrix.domain.assistant.tools import RETRIEVAL_TOOLS

# ---- 安全硬拦：1.0 ``final_guard.py:162-178`` 的五条 --------------------------
#
# 逐条照抄 1.0 的正则。``internal_id_leak`` 在 1.0 是 ``[a-f0-9]{6,}``（十六进制），
# 2.0 的 id 也是这样生成的（``asset_`` / ``turn_`` / ``ts_`` 后面跟 uuid 片段）。
_INTERNAL_ID = re.compile(r"\b(?:asset|obs|entity|mention|claim|turn|conversation)_[a-f0-9]{6,}\b")
_TABLE_NAME = re.compile(r"\b(?:assets|observations|entity_mentions|semantic_claims|agent_result_sets)\b")
_INTERNAL_SCHEMA = re.compile(r"\b(?:scope_id|viewer_id|read_write|allowed_tools|max_model_len)\s*[:=]")
# **2.0 把裸词 ``他人`` 收紧了**——1.0 逐字是
# ``别的?相册|他人|别的?人[的]?(?:相册|照片|隐私)|其他用户``，其中 ``他人`` 单独成项。
#
# 实测 2026-10-10 那轮有 5 道题被这条**硬拦**（整轮终结、答案走确定性兜底、均分 0），
# 而问题原文是「我想找带强子第一次近距离看活海星和鲎的那次参观照片」这类——相册里
# 全是家人，「与他人」「给他人的」是日常说法。硬拦不重试（``HARD_GUARD_CODES``），
# 一次误伤就是一道题归零。
#
# 收紧成「``他人`` + 隐私宾语」之后，真正要拦的表达（声称能看到别人家的相册、
# 照片、隐私）照样命中，而「与他人的合影」不再触发。
_VIEWER_ESCAPE = re.compile(
    r"别的?相册|他人[的]?(?:相册|照片|隐私|内容|信息)|别的?人[的]?(?:相册|照片|隐私)|其他用户"
)
_WRITE_NOT_ALLOWED = re.compile(r"(?:已经|帮你|可以)(?:写入|删除|修改|覆盖|清空)(?:了|过)?")

# ---- 模板占位符：1.0 ``final_guard.py:97-98`` --------------------------------
_PLACEHOLDER = re.compile(
    r"\[[^\[\]]{0,48}"
    r"(?:填入|此处|占位|待填|名称|名字|姓名|朋友|数量|时间|地点|内容|数字|照片|记录|店名|电话|价格|日期|金额)"
    r"[^\[\]]{0,36}\]"
)

# ---- 存在性检查：1.0 ``final_guard.py:36-53`` 的五条正则 ---------------------
# 整体否认存在（带明确宾语/动作）：total>0 时回答却说没找到。
# 只匹配"明确否认找到照片/记录"这类整体否定，不含"我没去过北京"这类出行否定。
_DENY_EXISTS = re.compile(
    r"没(?:有)?找到|未找到|找不到|查无|"
    r"没有(?:任何|符合|相关|一张|一个|一条|拍到|拍过|去过)?(?:照片|记录|回忆|记忆|相关)",
    re.I,
)
# 只是不确定/hedge，不算整体否认（"无法确认/还不能确定/可能没有"等）。
_HEDGE = re.compile(
    r"无法确认|不能确认|不确定|还不能|暂时|无法判断|记不清|可能没有|未必|没有完全|不能确定",
    re.I,
)
# 条件级否认："没找到能确认'爬山'的记录"≠"没有找到照片"，不算整体否认。
_CONDITION_LEVEL_DENY = re.compile(
    r"没(?:有)?找到(?:能|办法|足够|明确|可以){0,2}(?:确认|确定|验证|证实)", re.I
)
# 明确交付/找到断言（total=0 时回答却断言已找到/交付）。
_FOUND_DELIVERY = re.compile(
    r"(?:已|为)?(?:为您|为你)?找到|已找到|找到了|"
    r"找到\s*\d+\s*张|这是(?:您|你)要找的|这就是(?:您|你)?要的|"
    r"有(?:一张|两张|几张|相关|这些)?(?:照片|记录)",
    re.I,
)
# 整句否定门槛：含明确否定词时不视为"断言找到/交付"。1.0 的注释说明这是**刻意**的保守
# 偏向（宁可漏报，不误伤事实正确的回答）——删除它会让正常回答被误拦。
_ANY_DENIAL = re.compile(r"没|未|无|不|无法|尚未", re.I)

# 1.0 的硬拦集合（``guard_types.py`` 的 ``HARD_BLOCK_CODES``）。硬拦不重试，直接走
# 确定性兜底；其余（truth）允许带着提醒重试一次。
HARD_GUARD_CODES = frozenset({"internal_id_leak", "table_name_leak", "viewer_escape", "write_not_allowed"})

# 1.0 的 ``_RETRIEVAL_TOOLS``（``final_guard.py:32-33``）在 S3 只剩两个成员。
_RETRIEVAL_TOOLS = RETRIEVAL_TOOLS

# 1.0 的 ``_natural_message``（``final_guard.py:56-73``）的文案。
_MESSAGES: Mapping[str, str] = {
    "internal_id_leak": "回答里出现了内部标识（asset_ 编号或记录 id）。请用「这张照片」这类说法重写。",
    "table_name_leak": "回答里出现了数据库表名或内部字段名。请删掉它们，用用户看得懂的说法。",
    "viewer_escape": "回答里提到了别的相册或别人家的照片。你只能回答当前相册里的内容。",
    "write_not_allowed": "回答里声称已经写入/删除/修改了数据，而系统只能读。请如实说明你只是查看。",
    "placeholder_leak": "回答里出现了「地点名称/数量/时间」这类未填写的占位符，必须替换成真实数据或删除。",
    "denial_without_search": (
        "你的回答声称没有找到相关记录，但本轮没有调用任何检索工具。"
        "请按问题类型先调用 search_memories 或 search_documents，再基于工具结果回答。"
    ),
    "omission_conflict": (
        "工具确认存在相关结果（total>0），但你的回答却说没有找到。"
        "请基于工具结果如实回答存在的情况，不要整体否认。"
    ),
    "fabrication_from_empty": (
        "工具确认不存在相关结果（total=0），但你的回答却声称找到了/已交付。请删除没有证据的找到/交付断言。"
    ),
    "all_requested_but_has_more": (
        "用户要求全部结果，但结果集还有更多未交付。请继续取回剩余结果，或如实说明只交付了部分。"
    ),
}
# **``delivery_contradiction`` 已删除（2026-10-09）。** 判据见
# ``_delivery_completeness`` 上那段注释：它要求 ``delivered_count == 0``，而那个值在
# 2.0 的形状下取不到。


@dataclass(frozen=True, slots=True)
class GuardFacts:
    """1.0 门禁读到的那些 ``task_state`` 格子在 2.0 的等价物。

    1.0 用一个大 dict 传（``runtime.py:2224-2247``）；2.0 把同样几个事实固化成一个
    不可变的投影。两者都由 ``GuardFacts.from_state`` 从工具的**内部**观察算出——外部
    拿不到 ``total`` 与 ``has_more`` 这两个只给门禁用的字段。
    """

    tool_results: tuple[Mapping[str, object], ...] = ()
    has_more: bool = False
    # 1.0 的 ``result_mode`` 来自 ``search_memories(mode=...)``。S3 的工具契约里没有这个
    # 参数（``tools.py`` 的 SEARCH_MEMORIES 只有 query / limit），所以它恒为 None，
    # ``all_requested_but_has_more`` 在 S3 不会触发——规则照搬，输入留给重新引入 mode 的
    # 那一天。
    result_mode: str | None = None

    @classmethod
    def from_state(cls, state: TaskState) -> GuardFacts:
        """1.0 ``result_set.py`` 的检索簿记：按调用顺序走一遍记录，后面的覆盖前面的。

        **交付侧的簿记（``delivery_state`` / ``delivered_count``）已随
        ``delivery_contradiction`` 一起删除**（2026-10-09）：那条规则是它们唯一的读者，
        而它本身在 2.0 够不着（见 ``_delivery_completeness``）。留着一格没人读的状态
        比删掉更贵——它看起来还在守什么。
        """
        rows: list[Mapping[str, object]] = []
        has_more = False
        for record in state.records:
            if record.status != "ok" or record.tool not in _RETRIEVAL_TOOLS:
                continue
            rows.append({"tool": record.tool, "total": record.observation.get("total")})
            has_more = bool(record.observation.get("has_more"))
        return cls(tool_results=tuple(rows), has_more=has_more)


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    passed: bool
    code: str = ""
    message: str = ""
    # **打回的那段原文本身。** 没有它，「这一轮为什么被硬拦」在轨迹上只剩一个规则码——
    # 而 `viewer_escape` 那条正则里的 ``他人`` 是个日常词（家人合影里"与他人"很常见），
    # 是真命中还是误伤，**只有看到原句才判得了**。
    # 实测 20261010 那一轮 10 道题被它硬拦（整轮终结、走确定性兜底），
    # 而原文一个字都没留下来，于是那 10 道完全不可诊断。
    evidence: str = ""

    @property
    def hard(self) -> bool:
        """硬拦：不可恢复，调用方直接走确定性兜底，不再重试。"""
        return self.code in HARD_GUARD_CODES

    @staticmethod
    def ok() -> GuardVerdict:
        return GuardVerdict(passed=True)


#: ``evidence`` 里留多长。够看清命中的那句话，又不至于把整段回答抄进轨迹。
_EVIDENCE_LIMIT = 80


def _match_evidence(pattern: re.Pattern[str], text: str) -> str:
    """命中处前后各取一段，让读的人看得见上下文。"""
    found = pattern.search(text)
    if found is None:
        return ""
    start = max(0, found.start() - _EVIDENCE_LIMIT // 2)
    return text[start : start + _EVIDENCE_LIMIT]


def _verdict(code: str, evidence: str = "") -> GuardVerdict:
    return GuardVerdict(passed=False, code=code, message=_MESSAGES[code], evidence=evidence)


class FinalGuard:
    def check(self, *, answer: str, facts: GuardFacts) -> GuardVerdict:
        """按 1.0 ``final_guard.py:89-113`` 的顺序逐条过。

        顺序有意义：安全硬拦在前，任何一条命中就返回，后面的规则不再看。
        """
        for verdict in (
            self._safety(answer),
            self._placeholder(answer),
            self._denial_without_search(answer, facts),
            self._existence(answer, facts),
            self._delivery_completeness(facts),
        ):
            if verdict is not None:
                return verdict
        return GuardVerdict.ok()

    # ---- 规则 ---------------------------------------------------------------

    @staticmethod
    def _safety(answer: str) -> GuardVerdict | None:
        for pattern, code in (
            (_INTERNAL_ID, "internal_id_leak"),
            (_TABLE_NAME, "table_name_leak"),
            (_INTERNAL_SCHEMA, "table_name_leak"),
            (_VIEWER_ESCAPE, "viewer_escape"),
            (_WRITE_NOT_ALLOWED, "write_not_allowed"),
        ):
            if pattern.search(answer):
                return _verdict(code, evidence=_match_evidence(pattern, answer))
        return None

    @staticmethod
    def _placeholder(answer: str) -> GuardVerdict | None:
        if _PLACEHOLDER.search(answer):
            return _verdict("placeholder_leak", evidence=_match_evidence(_PLACEHOLDER, answer))
        return None

    @staticmethod
    def _denial_without_search(answer: str, facts: GuardFacts) -> GuardVerdict | None:
        searched = any(str(row.get("tool") or "") in _RETRIEVAL_TOOLS for row in facts.tool_results)
        if not searched and _DENY_EXISTS.search(answer):
            return _verdict("denial_without_search")
        return None

    @staticmethod
    def _existence(answer: str, facts: GuardFacts) -> GuardVerdict | None:
        """最小确定性存在性检查：工具确认存在/不存在 vs 回答的整体否认/交付断言。

        只做可确定性验证的存在性矛盾，不判断任何词语等价。1.0 的两道门槛原样保留：
        整体否认要排除 hedge 与条件级否认；找到断言要排除整句否定。
        """
        text = (answer or "").strip()
        if not text:
            # 空回答不归门禁管——1.0 ``final_guard.py:126-128`` 也是直接放行，
            # 交给收尾的 parse_failure 路径。
            return None

        denies = (
            bool(_DENY_EXISTS.search(text))
            and not _HEDGE.search(text)
            and not _CONDITION_LEVEL_DENY.search(text)
        )
        found_claim = bool(_FOUND_DELIVERY.search(text)) and not _ANY_DENIAL.search(text)

        totals = [row.get("total") for row in facts.tool_results if row.get("total") is not None]
        if any(int(str(total or 0)) > 0 for total in totals) and denies:
            return _verdict("omission_conflict")
        if any(int(str(total or 0)) == 0 for total in totals) and found_claim:
            return _verdict("fabrication_from_empty")
        return None

    @staticmethod
    def _delivery_completeness(facts: GuardFacts) -> GuardVerdict | None:
        """1.0 ``final_guard.py:106-112`` 的交付结构规则。

        **``delivery_contradiction`` 已删除（2026-10-09）。** 两条理由，第二条是主因：

        1. **它的输入取不到。** 规则要求 ``delivery_state == "complete"``、
           ``delivered_count == 0``（**整数** 0）。而 ``deliver()`` 的成功路径
           ``delivered`` 恒 ``>= 1``；失败路径走 ``_failure``（观察里没有 ``delivered``
           这一格），``_finish_with_final_answer`` 又把它记成 ``status="error"``，
           ``GuardFacts.from_state`` 只读 ``ok`` 记录。所以那一格永远是 ``>= 1`` 或
           ``None``，**命中不了**。
        2. **它的前提已经变了。** 规则设计于「省略的时代」——1.0 的 final 动作
           239/487（49%）根本不带 ``selected_image_handles``，dict 缺键不报错。2.0 把
           那个字段做成**必填**之后，零交付是一个明确写下的 ``[]``，不是一个遗漏。
           让它活起来，会在约 300 道「检索取完但没交图」的题上各打回一次、各花一个
           模型步，而且打回的是模型的**显式选择**。
        """
        if facts.result_mode == "all" and facts.has_more:
            return _verdict("all_requested_but_has_more")
        return None


def answered_with_gap(ledger: EvidenceLedger) -> bool:
    """收尾时是否还有没满足的需求——用来区分「答完了」与「如实说了答不出来」。"""
    return not ledger.is_complete
