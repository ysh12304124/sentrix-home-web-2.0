"""证据绑定的人物画像：证据包 / 强制校验 / 确定性兜底。

逐行移植 1.0 ``backend/person_portraits.py``。三层分离是它的设计要点，2.0 原样保留：

- **证据包**（``compile_portrait_evidence``）只装能追溯到资产与事件的客观材料。
  名字与角色**只有在已确认时才进包**——未确认的假设进包，模型就会把它写成事实。
- **校验**（``validate_portrait``）是确定性的，与模型无关。画像在通过它之前不落库。
- **兜底**（``deterministic_portrait``）不调模型，纯模板。

**2.0 相对 1.0 修了三处（每处都有实施期实测，见 ``docs/refactor/s5-deviations.md``）：**

1. ``_evidence_ref_valid`` 的白名单后门：1.0 对 ``relationship`` / ``semantic_claim`` /
   ``appearance`` 三种 kind **直接放行**，于是模型编造的引用一律通过。2.0 只承认包里
   真有的东西。
2. ``deterministic_portrait`` 通不过自己的校验：1.0 的兜底模板实测 41 字 < 它自己的
   80 字下限，所以三级降级的最后一格永远落不了库。2.0 让校验知道**来源**
   （``source``），长度与主题条数这两条**由模板决定**的约束只对模型输出成立。
3. （第三条「校验失败完全静默」的落点在 application 层的日志，不在本模块。）

**没有 ``suggested_relationships``。** 裁决① 不搬 ``infer_graph``，而它是关系假设唯一的
写入者，0006 也没建 ``relationship_hypotheses``。留一个恒空的键会让读提示词的人以为
那条链路是通的，所以整个键不再出现在证据包里。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    PENDING_CLUSTER_NAME,
    PORTRAIT_SOURCE_DETERMINISTIC,
    EntityRecord,
    PersonMomentRecord,
    RelationshipRecord,
)
from sentrix.domain.identity.moments import SENSITIVE_TERMS

# 1.0 ``person_portraits.py:14-23`` 的提示词原文，一字不差。
PERSON_PORTRAIT_PROMPT = (
    "你是家庭相册的人物画像作者。输入是一个结构化的证据包，包含确认事实、客观人物瞬间、"
    "模型解释和未确认假设。\n"
    "以具体反复出现的瞬间和互动为主体，写一段 80 到 500 个汉字的鲜活人物画像。\n"
    "允许‘爱热闹、有仪式感、常照顾孩子’等低风险印象；不要以次数开头；\n"
    "未确认的解释必须使用‘似乎、可能、从照片看’等不确定措辞；禁止敏感推断和虚构。\n"
    "严格返回 JSON：\n"
    '{"portrait_text":"...","themes":[{"title":"...","summary":"...",'
    '"evidence_refs":[{"kind":"person_moment","id":"..."}]}]}'
)

# 1.0 ``person_portraits.py:43`` 的默认值。抽样预算：先每事件最多 2 条，去重后最多 12 条。
MAX_MOMENTS = 12
PER_EVENT = 2
# 每个主题最多引用几条瞬间（1.0 ``person_portraits.py:97`` 的切片上界）。
MAX_REFS_PER_THEME = 2
# 高频动作取前几个（1.0 ``person_portraits.py:88`` 的 ``most_common(3)``）。
TOP_ACTIONS = 3

MIN_HANZI, MAX_HANZI = 80, 500
MIN_THEMES, MAX_THEMES = 2, 6

# 1.0 ``person_portraits.py:25-27``。
_HEDGE_TERMS = ("似乎", "可能", "从照片看", "看起来", "像是")
_UNDECIDED = ("本人", "无法判断")
_HANZI = re.compile(r"[一-鿿]")

# 1.0 ``person_graph.py:29`` 的 ``ROLE_OPTIONS``。
#
# **为什么搬到这里。** 裁决① 把 ``person_graph.py`` 整个判死，而 1.0 的
# ``person_portraits.py:11`` 正是从它 import 这个词表。查过 2.0 的其余消费方：
# 1.0 ``db.py:6471`` 那处校验在 ``confirm_role_hypothesis`` 里，属角色假设功能，
# 同样随 person_graph 判死；2.0 的 ``name_person.py`` 不校验角色词表（它的入参来自
# 用户输入的路由，不是模型候选）。所以 2.0 里这个词表的**唯一**消费方就是下面那条
# 「未确认角色的模糊措辞」判据，跟着它住是唯一说得通的位置。
ROLE_OPTIONS = frozenset(
    {
        "本人",
        "父亲",
        "母亲",
        "配偶",
        "孩子",
        "祖父母",
        "兄弟姐妹",
        "其他亲属",
        "朋友",
        "同事",
        "同学",
        "邻居",
        "照护者",
        "老师",
        "亲友",
        "访客",
        "一次性人物",
        "无法判断",
    }
)


def _contains_sensitive(text: str) -> bool:
    return any(term in text for term in SENSITIVE_TERMS)


def _mentions_role(text: str) -> bool:
    """文本里有没有出现角色词。``_UNDECIDED`` 里的两个不算——「本人」是相册主人的
    自称、「无法判断」是模型在说它不知道，两者都不是对这个人角色的断言。"""
    for role in ROLE_OPTIONS:
        if role in _UNDECIDED:
            continue
        if role in text:
            return True
    return False


def compile_portrait_evidence(
    person: EntityRecord,
    moments: Sequence[PersonMomentRecord],
    relationships: Sequence[RelationshipRecord],
    *,
    max_moments: int = MAX_MOMENTS,
    per_event: int = PER_EVENT,
) -> dict[str, Any]:
    """把这个人的材料收成一个有界证据包。

    抽样三步的顺序是 1.0 的原文，**换序结果就不同**：
    先每事件按 confidence 取前 ``per_event`` 条（保证事件覆盖），
    再按 ``(action_text, interaction_target_ids)`` 去重（同一件事在多张照片里只算一条），
    最后按 ``created_at`` 排序截断到 ``max_moments``（留下最早的那些，让不同次生成的
    证据包可比）。

    1.0 收的是 ``store``、在函数里自己查库；2.0 收**已经取好的材料**——domain 不做 IO。
    查询本身（哪些瞬间算这个人的、关系怎么筛）落在仓储与用例那一层。
    """
    confirmed_name = person.canonical_name if person.status == ENTITY_STATUS_CONFIRMED else None
    confirmed_role = person.family_role if person.role_state == "confirmed" else None

    by_event: dict[str, list[PersonMomentRecord]] = {}
    for moment in moments:
        by_event.setdefault(moment.event_id, []).append(moment)
    sampled: list[PersonMomentRecord] = []
    for items in by_event.values():
        ordered = sorted(items, key=lambda item: -item.confidence)
        sampled.extend(ordered[:per_event])

    seen: dict[tuple[str, tuple[str, ...]], PersonMomentRecord] = {}
    for moment in sampled:
        key = (moment.action_text, tuple(moment.interaction_target_ids))
        current = seen.get(key)
        if current is None or moment.confidence > current.confidence:
            seen[key] = moment
    picked = sorted(seen.values(), key=lambda item: _created_key(item))[:max_moments]

    moment_evidence = [
        {
            "kind": "person_moment",
            "id": moment.id,
            "asset_id": moment.asset_id,
            "observation_id": moment.observation_id,
            "event_id": moment.event_id,
            "action_text": moment.action_text,
            "interaction_text": moment.interaction_text,
            "participation_style": moment.participation_style,
            "visible_affect": moment.visible_affect,
            "confidence": moment.confidence,
        }
        for moment in picked
    ]

    recurring = Counter(moment.action_text for moment in picked if moment.action_text).most_common(
        TOP_ACTIONS
    )
    appearance_themes = []
    for action, _count in recurring:
        appearance_themes.append(
            {
                "title": f"反复{action}",
                "summary": f"从照片看，他常{action}。",
                "evidence_refs": [
                    {"kind": "person_moment", "id": moment.id}
                    for moment in picked
                    if moment.action_text == action
                ][:MAX_REFS_PER_THEME],
            }
        )

    return {
        "person": {
            "id": person.id,
            # pending 实体的 canonical_name 是落库的内部名，不能进提示词：
            # 模型会把它原样写进画像，最后又回到用户界面。
            "display_name": (
                PENDING_CLUSTER_NAME
                if person.status == ENTITY_STATUS_PENDING or not person.canonical_name
                else person.canonical_name
            ),
            "confirmed_name": confirmed_name,
            "confirmed_role": confirmed_role,
            "identity_state": person.identity_state or "clustered",
        },
        "confirmed_relationships": [
            {
                "kind": "relationship",
                "id": item.id,
                "subject_entity_id": item.subject_entity_id,
                "predicate": item.predicate,
                "object_entity_id": item.object_entity_id,
                "subject_name": item.subject_name,
                "object_name": item.object_name,
                "confidence": item.confidence,
            }
            for item in relationships
        ],
        "moments": moment_evidence,
        "recurring_patterns": appearance_themes,
        "appearance_themes": appearance_themes,
        "time_changes": [],
        "contradictions": [],
        "unknowns": [],
    }


def _created_key(moment: PersonMomentRecord) -> str:
    """1.0 按 ``str(m.get("created_at") or "")`` 排。存的是 ISO 串，字典序即时序。"""
    return moment.created_at.isoformat() if moment.created_at is not None else ""


def _evidence_ref_valid(pack: dict[str, Any], ref: Any) -> bool:
    """引用必须指向**证据包里真有的东西**。

    1.0 ``person_portraits.py:119-126``：``person_moment`` 认真查，``relationship`` /
    ``semantic_claim`` / ``appearance`` 三种 kind **一律 ``return True``**——那是个白名单
    后门，模型编一条 ``{"kind": "relationship", "id": "..."}`` 就能给画像挂上不存在的
    证据。实测（``s5c_probe.py``）确认：伪造 ref 的画像 ``validate`` 返回 ``ok=True``。

    2.0 把「哪些 kind 在包里能落到实体上」写死成下面两个分支。``semantic_claim`` 与
    ``appearance`` **不是**能落地的 kind：语义断言属 S6，外观证据是另一条链路且不进画像
    包，所以它们在这里没有合法形态，一律不合法。
    """
    if not isinstance(ref, dict):
        return False
    kind = str(ref.get("kind") or "")
    ref_id = str(ref.get("id") or "")
    if not ref_id:
        return False
    if kind == "person_moment":
        return any(moment["id"] == ref_id for moment in pack.get("moments") or [])
    if kind == "relationship":
        return any(item["id"] == ref_id for item in pack.get("confirmed_relationships") or [])
    return False


# 1.0 person_insights.py:251 的字面量。与 PERSON_MOMENT_PROMPT_VERSION 同一条
# 口径：它标识的是**提示词的版本**，写了它的画像行能解释「这一版是哪套提示词产出的」。
# 改了 PERSON_PORTRAIT_PROMPT 就必须改它，否则两版结论混在一条时间线上分不开。
PERSON_PORTRAIT_PROMPT_VERSION = "person-portrait-v1"


def validate_portrait(
    pack: dict[str, Any],
    portrait: dict[str, Any],
    *,
    source: str,
) -> tuple[bool, list[str]]:
    """画像落库前的强制校验。返回 ``(是否通过, 错误列表)``。

    ``source`` 只有两个取值（``model`` / ``deterministic``），它决定**两条约束是否适用**：

    - 汉字数 80–500 —— 这是给**模型**的输出约束（提示词里就是这么写的）；
    - 主题条数 2–6 —— 模型的输出形状。

    这两条的输入都是「写作者的选择」，所以只对模型输出成立。确定性兜底的文案与主题
    条数**由模板唯一决定**：模板 41 字，要求它 ≥ 80 字等于要求模板恰好落在那个区间里
    ——1.0 的降级链最后一格因此永远落不了库（缺陷 2）。拿同一条校验去卡两种不同的东西，
    得到的不是严格，是一条永远为假的判据。

    **其余各条对两种来源一律成立**：引用必须真实、每个主题要有引用、敏感词黑名单、
    未确认角色必须带模糊措辞。兜底文案也要守这些线。
    """
    errors: list[str] = []
    deterministic = source == PORTRAIT_SOURCE_DETERMINISTIC
    text = str(portrait.get("portrait_text") or "")
    hanzi = len(_HANZI.findall(text))
    if not deterministic:
        if hanzi < MIN_HANZI:
            errors.append(f"portrait too short: {hanzi} hanzi < {MIN_HANZI}")
        if hanzi > MAX_HANZI:
            errors.append(f"portrait too long: {hanzi} hanzi > {MAX_HANZI}")

    themes = portrait.get("themes") or []
    if not isinstance(themes, list):
        errors.append("themes must be a list")
        themes = []
    elif not deterministic and not (MIN_THEMES <= len(themes) <= MAX_THEMES):
        errors.append(f"themes must be {MIN_THEMES}-{MAX_THEMES}, got {len(themes)}")

    for theme in themes:
        refs = theme.get("evidence_refs") or []
        if not refs:
            errors.append("theme missing evidence_refs")
        for ref in refs:
            if not _evidence_ref_valid(pack, ref):
                errors.append(f"invalid evidence ref: {ref}")

    if _contains_sensitive(text):
        errors.append("portrait contains sensitive attribute language")

    person = pack.get("person") or {}
    if (
        not person.get("confirmed_role")
        and _mentions_role(text)
        and not any(hedge in text for hedge in _HEDGE_TERMS)
    ):
        errors.append("unconfirmed role without hedging")
    return (not errors, errors)


def deterministic_portrait(pack: dict[str, Any]) -> dict[str, Any]:
    """不调模型的兜底画像：把最高频的动作套进固定模板。

    1.0 ``person_portraits.py:156-180``。两条分支：
    - 一条动作都没有 -> 一段说明证据不足的固定文案，``themes`` 为空；
    - 否则 -> 取前 ``TOP_ACTIONS`` 个动作，每个一条主题。

    **它仍是 ``source="deterministic"`` 送进 ``validate_portrait`` 的**，因为它的长度与
    主题条数由这两条分支唯一决定。注意兜底**不是**无条件通过：``action_text`` 是模型
    写的，若它含敏感词（例如「生病住院」），兜底画像照旧会被拒——这是有意的，兜底不
    等于豁免产品规则。
    """
    moments = pack.get("moments") or []
    actions = [moment.get("action_text") or "" for moment in moments if moment.get("action_text")]
    top = Counter(actions).most_common(TOP_ACTIONS)
    if not top:
        return {
            "portrait_text": "从照片看，这位家庭成员在相册中反复出现，但具体瞬间证据不足，无法形成完整画像。",
            "themes": [],
        }
    body = "，".join(f"他常{action}" for action, _count in top)
    text = f"从照片看，{body}。他从照片看常出现在这些瞬间里，让人感到熟悉和安心。"
    themes = []
    for action, _count in top:
        refs = [
            {"kind": "person_moment", "id": moment["id"]}
            for moment in moments
            if moment.get("action_text") == action
        ][:MAX_REFS_PER_THEME]
        themes.append(
            {
                "title": f"反复{action}",
                "summary": f"从照片看，他常{action}。",
                "evidence_refs": refs,
            }
        )
    return {"portrait_text": text, "themes": themes}


def normalize_writer_output(parsed: Any) -> dict[str, Any]:
    """把模型的自由输出收成 ``{portrait_text, themes}``。1.0 ``person_portraits.py:183-197``。

    两处防御都是必要的：``parsed`` 可能不是字典（模型回了数组或裸串），主题可能不是
    字典（回了字符串），引用缺 ``id`` 就没法回溯（丢掉，但**保留同主题的其余引用**）。
    """
    themes: list[dict[str, Any]] = []
    raw_themes = parsed.get("themes") if isinstance(parsed, dict) else []
    for theme in raw_themes or []:
        if not isinstance(theme, dict):
            continue
        title = str(theme.get("title") or "").strip()
        summary = str(theme.get("summary") or "").strip()
        refs = []
        for ref in theme.get("evidence_refs") or []:
            if isinstance(ref, dict) and ref.get("id"):
                refs.append({"kind": str(ref.get("kind") or "person_moment"), "id": str(ref["id"])})
        themes.append({"title": title, "summary": summary, "evidence_refs": refs})
    return {
        "portrait_text": str(parsed.get("portrait_text") or "").strip() if isinstance(parsed, dict) else "",
        "themes": themes,
    }
