"""条件判定：一条约束在某张图上算「满足 / 可能 / 矛盾 / 未知」。纯规则，零 IO。

对应 1.0 ``backend/evidence_retrieval.py:414-495`` 的 ``_evaluate`` 与 ``_condition``。
**这是 S6 的「条件证据判定」**——``application/retrieval/retrieve_evidence.py`` 的模块
注释写着「S4 只搬前两步（召回 + 融合），后两步属于 S6」，这就是第三步。

## 为什么它必须与召回分开

1.0 有**两套**机制，混起来看会得出相反结论：

- ``HardFilterContext`` 传给各通道，是**召回前筛**——只有 ``metadata`` 通道真用
  （``metadata.py:33`` 的早退），``visual_ann`` / ``lexical`` / ``entity`` / ``text_ann``
  只从它取 scope。它影响「召回什么」，**不保证结果满足条件**；
- ``spec.constraints`` 里 ``strictness == HARD`` 的约束逐条判定并排除。**这才是
  「硬条件」的全部含义。**

只做前者，查询「拍过某个人的照片」会返回一批**没有那个人**的照片——视觉与词法通道
照常召回，而没有任何一步把它们排除掉。**这个错误在界面上看起来完全正常。**

## 四值，不是三值

``MATCHED`` / ``POSSIBLE`` / ``CONTRADICTED`` / ``UNKNOWN``。少一个 ``POSSIBLE``
就会把「弱信号支持」当成「确定支持」，而 HARD 档只认 ``MATCHED``——

- ``POSSIBLE``：来源不可信（见 ``TRUSTED_SOURCE_TYPES``），或只是文本池里出现了那个词
  （1.0 ``_evaluate_place`` 的第三条）；
- ``UNKNOWN``：**没有据以判断的东西**。它与 ``CONTRADICTED`` 在 HARD 档下都会排除候选，
  但含义相反：``UNKNOWN`` 是「没证据」，``CONTRADICTED`` 是「有证据反对」。
  合成一个值之后，「照片没有 GPS 所以被全删了」这种事实就查不出来了。

## 一处与 1.0 的差异

1.0 的 ``_evaluate`` 把 ``source_type`` 的信任表放在**方法内部的一个类常量**里；
2.0 把它提到模块级常量（``TRUSTED_SOURCE_TYPES``），因为它是一条**产品口径**
  （「哪些证据算数」），而不是某个方法的实现细节。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sentrix.domain.media.capture_time import within_time_bounds
from sentrix.domain.media.models import Asset, Observation
from sentrix.domain.places.aliases import EMPTY_ALIASES, PlaceAliases
from sentrix.domain.places.matching import place_admin_matches, place_text_matches
from sentrix.domain.places.models import Place
from sentrix.domain.retrieval.query_contracts import Strictness
from sentrix.domain.retrieval.time_expression import parse_time_expression

# ``Strictness`` 从契约层 import，不在这里定义——与 1.0 的层序一致：
# ``query_contracts`` 定义 HARD / SEMANTIC，``evidence_retrieval``（本模块）引用它们。
# 反过来会让契约模块依赖一个判分模块，那是错的层序。T121a 把它移了过去。


class ConditionStatus(StrEnum):
    MATCHED = "matched"
    POSSIBLE = "possible"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


# **只有这些来源的 matched 算数**，其余降级成 possible（1.0 ``_evaluate`` 的 419-420 行）。
# 它决定「模型说它看见了」与「结构化字段说它是」不是同一件事——双层防伪的第一层。
#
# **抄的是代码，不是配置。** 1.0 有**两份**来历不同的清单：
#
#   - ``evidence_retrieval.py:409-412`` 的类常量——**真正生效的那份**，五个来源，
#     ``confirmed_bridge`` 与 ``entity_bridge_confirmed`` **两个都在**；
#   - ``configs/retrieval/defaults.json`` 的 ``matched_source_types``——只有四个，
#     漏了 ``confirmed_bridge``（而 ``_condition`` 的人物分支产出的正是这个），
#     而且 ``RetrievalConfig.matched_source_types``（``config.py:78``）**全仓没有调用者**。
#
# 第一版照抄了配置那份，于是**人物硬条件永远不可能满足**：命中判成 matched、
# 随即被降级成 possible，而 HARD 档只认 matched。表现是「按人搜照片一张都搜不出来」。
# **一份没人读的配置看起来与生效的那份一样权威**——这次是靠一个用例炸出来的。
TRUSTED_SOURCE_TYPES = frozenset(
    {
        "asset_metadata",
        "observation_field_exact",
        "confirmed_bridge",
        "entity_bridge_confirmed",
        "subject_binding",
    }
)


@dataclass(frozen=True, slots=True)
class Condition:
    """一条结构化约束。``key`` 是它在结果里的编号（1.0 ``constraint.key``）。"""

    dimension: str
    value: str
    strictness: Strictness = Strictness.HARD
    negated: bool = False
    key: str = ""


@dataclass(frozen=True, slots=True)
class Evidence:
    """判定结果。``source_type`` 与 ``source_id`` 照 1.0 一起带出来——
    没有它们，「为什么这张图被排除了」就只剩一个状态字。"""

    status: ConditionStatus
    source_type: str | None = None
    source_id: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """判一条约束需要的外部事实。**由调用方查好注入**——domain 不做 IO。

    ``place`` 为空是**一个有意义的状态**（这张图没解析出地点），不是「没传」。
    与 ``EvidenceRequest`` 里那几个字段同一个口径。
    """

    place: Place | None = None
    place_aliases: PlaceAliases = EMPTY_ALIASES
    confirmed_person: Callable[[str], bool] | None = None


# 模块级单例而不是在签名里现造：ruff 的 B008 挡的是「默认值里做函数调用」，
# 而这个 dataclass 是 frozen 的，共享一个实例没有别名风险。
_EMPTY_CONTEXT = EvidenceContext()


def evaluate(
    condition: Condition,
    asset: Asset,
    observation: Observation,
    *,
    context: EvidenceContext = _EMPTY_CONTEXT,
) -> Evidence:
    """这条约束在这张图上算哪种。"""
    if condition.dimension == "time":
        return _time(condition, asset, observation)
    if condition.dimension == "media":
        return _media(condition, asset)
    if condition.dimension == "place":
        return _place(condition, asset, observation, context)
    if condition.dimension == "person":
        return _person(condition, observation, context)
    # 认不出的维度一律 UNKNOWN，**绝不 CONTRADICTED**：从一个不认识的条件上推出
    # 「矛盾」会让它在 HARD 档下悄悄删掉候选，而没人知道为什么少了照片。
    return Evidence(ConditionStatus.UNKNOWN)


def trusted(evidence: Evidence) -> Evidence:
    """来源不可信的 ``MATCHED`` 降级成 ``POSSIBLE``。1.0 ``_evaluate`` 的 419-420 行。

    **这一步不能省。** 少了它，一个「捕获文本里出现过这个词」的弱命中会与
    「结构化字段说是」拿到同一种处置，而 HARD 档下前者不该让候选留下。
    """
    if evidence.status is ConditionStatus.MATCHED and evidence.source_type not in TRUSTED_SOURCE_TYPES:
        return Evidence(
            ConditionStatus.POSSIBLE, evidence.source_type, evidence.source_id, evidence.confidence
        )
    return evidence


def excludes(condition: Condition, evidence: Evidence) -> bool:
    """这条判定结果要不要把候选排除掉。1.0 ``evidence_retrieval.py:422-425``。

    - HARD：非 ``MATCHED`` 即排除（``negated`` 时反过来）；``POSSIBLE`` **不算满足**；
    - SEMANTIC：只有 ``CONTRADICTED`` 才排除——``UNKNOWN`` 与 ``POSSIBLE`` 都放行。

    **两档的差别是这一整个模块存在的理由。** 合成一条规则会让「用户明确说的」与
    「模型猜的」得到同一种处置，而它们的证据强度差着一个量级。
    """
    if condition.strictness is Strictness.HARD:
        matched = evidence.status is ConditionStatus.MATCHED
        return matched if condition.negated else not matched
    return evidence.status is ConditionStatus.CONTRADICTED


# ---- 四个维度 ------------------------------------------------------------------


def _time(condition: Condition, asset: Asset, observation: Observation) -> Evidence:
    bounds = parse_time_expression(condition.value)
    if bounds is None:
        # **认不出来不是「不满足」。** 返回 CONTRADICTED 会在 HARD 档下把所有候选
        # 一次删光，而用户看到的是「没找到照片」——一次解析失败伪装成了一次空结果。
        return Evidence(ConditionStatus.UNKNOWN)
    captured = asset.captured_at or observation.captured_at
    matched = within_time_bounds(bounds, captured)
    if matched is None:
        return Evidence(ConditionStatus.UNKNOWN)
    return Evidence(
        ConditionStatus.MATCHED if matched else ConditionStatus.CONTRADICTED,
        "asset_metadata",
        asset.id,
        1.0,
    )


def _media(condition: Condition, asset: Asset) -> Evidence:
    matched = str(asset.media_type) == condition.value
    return Evidence(
        ConditionStatus.MATCHED if matched else ConditionStatus.CONTRADICTED,
        "asset_metadata",
        asset.id,
        1.0,
    )


def _place(
    condition: Condition, asset: Asset, observation: Observation, context: EvidenceContext
) -> Evidence:
    """1.0 ``_evaluate_place``（``:559-588``）的**顺序**逐条照抄——顺序是语义。

    权威来源是地理编码（2.0 是 ``asset_places`` 解析出的 ``Place``），
    ``observation.place`` 是场景类型（「室内餐厅或咖啡馆」），**只作弱文本信号、不产生矛盾**。

    所以最后那条 ``if context.place`` 是关键的：**有权威 geocode 但确不匹配才判矛盾；
    没有 geocode 一律 UNKNOWN**——「不能因为照片没 GPS 就剔除」（1.0 原文）。
    写成「什么都不匹配就矛盾」会把所有没有 GPS 的照片一次删光，
    而那看起来只是「搜出来的照片少了」。
    """
    value = condition.value.strip()
    if not value:
        return Evidence(ConditionStatus.UNKNOWN)

    if place_admin_matches(value, context.place, context.place_aliases):
        return Evidence(ConditionStatus.MATCHED, "asset_metadata", asset.id, 0.9)

    if place_text_matches(value, context.place, context.place_aliases):
        return Evidence(ConditionStatus.POSSIBLE, "nearby_or_coarse_location", asset.id, 0.6)

    observation_place = observation.place.strip()
    if observation_place and observation_place == value:
        return Evidence(
            ConditionStatus.MATCHED, "observation_field_exact", observation.id, observation.confidence
        )

    pool = " ".join(filter(None, [observation.place, observation.caption]))
    if value in pool:
        # **possible 而不是 matched**：捕获文本里出现了那个词，不等于这张图在那个地方。
        return Evidence(ConditionStatus.POSSIBLE, "observation", observation.id, observation.confidence)

    if context.place is not None and not context.place.admin.is_empty:
        return Evidence(ConditionStatus.CONTRADICTED, "asset_metadata", asset.id, 0.9)
    return Evidence(ConditionStatus.UNKNOWN)


def _person(condition: Condition, observation: Observation, context: EvidenceContext) -> Evidence:
    """**两条路，任一成立即命中**（1.0 ``:470-481`` 是 ``or``，照抄）。

    1. ``name in observation.people`` —— 这条观察的**已确认人物快照**里有这个名字。
       这是逐观察的事实，比「这个 scope 里有这个人」精确得多（后者会让那个人出现的
       任何一张图都通过）；
    2. ``context.confirmed_person(name)`` —— **这个 scope 里**这个名字是已确认人物。
       1.0 在这一支上每次重查 ``entity_mentions`` + ``status == 'confirmed'``，
       在 2.0 由 ``EntityRepository.confirmed_person_names`` 承担。

    **一处记下来的观察，没有改行为。** 2.0 的 ``observations.people`` 是**追加式快照**
    （``link_confirmed_mention`` 在写提及的同一个调用里调 ``_append_person_snapshot``），
    实体后来被取消确认时它不会回退。所以第 1 支可能命中一个**已经不是**已确认人物的名字。
    1.0 同样如此（它第 1 支也不查新鲜度）——**这是照抄的行为，不是这里引入的**。
    改它会让「按人搜照片」的结果与 1.0 不同，那是另一个决定。

    查不到时返回 ``UNKNOWN`` 而不是 ``CONTRADICTED``（1.0 ``:479``）：图里没有这个名字
    有两种可能——这个人没被拍到，或者那条提及还没建。HARD 档下两者都排除，
    但 SEMANTIC 档下只有后者该排除，所以这个区别必须留着。
    """
    name = condition.value.strip()
    if not name:
        return Evidence(ConditionStatus.UNKNOWN)
    if name in observation.people:
        return Evidence(ConditionStatus.MATCHED, "confirmed_bridge", observation.id, 1.0)
    if context.confirmed_person is not None and context.confirmed_person(name):
        return Evidence(ConditionStatus.MATCHED, "confirmed_bridge", observation.id, 1.0)
    return Evidence(ConditionStatus.UNKNOWN)


__all__ = [
    "Condition",
    "ConditionStatus",
    "Evidence",
    "EvidenceContext",
    "Strictness",
    "TRUSTED_SOURCE_TYPES",
    "evaluate",
    "excludes",
    "parse_time_expression",
    "trusted",
]
