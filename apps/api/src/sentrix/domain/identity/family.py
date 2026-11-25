"""家庭图谱的四类落库记录（S7b1 · T302）。

对应 1.0 ``db.py`` 里那六张表的行。**记录只描述「库里有什么」，判断在别处**：
成员归属的取值合法性、边的标签白名单（``family_graph.py``）、以及
「model 写的行遇到 locked 要跳过」这类决定，全在用例与仓储里。

**三处列语义与直觉不符，照搬并在此记明**（与迁移 0009 的注释是同三件事）：

1. ``state`` 取 ``effective`` / ``superseded`` / ``retracted``——**不是布尔**，
   读侧一律先按 ``effective`` 过滤。``retracted`` 只在关系那一侧出现（用户撤回），
   成员与画像只有 ``effective`` / ``superseded``。
2. ``locked`` 的含义是「**用户写下的，模型不许覆盖**」。类型上挑 ``bool`` 而不是
   库里的 ``INTEGER 0/1``：1.0 的每一处判据都是真值判断（``current.get("locked")``），
   ``bool`` 与原意等价，而 ``locked == 1`` 那种写法会让人以为还有别的取值。
3. ``evidence_refs`` 在库里是 ``evidence_refs_json``，**1.0 与 2.0 的 ``relationships``
   不同名**（那边叫 ``evidence_ids_json``）。照搬这个名字，不统一——两列在不同的表上，
   而且 1.0 的 family 侧从来只写 observation id。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sentrix.domain.identity.models import ENTITY_STATUS_PENDING

# 1.0 ``db.py:5617`` 的 ``_FAMILY_MEMBERSHIPS``。**"unknown" 是合法值不是缺失**——
# 模型推断不出归属时要显式写 unknown，而不是不写行（那样下次读出来是 None，
# 与「确实是 unknown」在响应里长得一样）。
FAMILY_MEMBERSHIPS = frozenset({"family", "friend", "unknown"})

# 1.0 ``db.py:5618`` 的 ``_FAMILY_SOURCES``。只有两个来源，且**决定 locked**：
# ``user_override`` 写的行 locked=1，``model`` 写的 locked=0。
#: `role_seed` 是第三种：由已确认人物的 `family_role`（父亲/母亲/妻子）确定性补出的边。
#: 它不是 `model` 推断（证据闸根本不会通过），也不是 `user_override`（用户没有直接
#: 编辑这条边），所以单列一类——图谱页可以把三者分开显示，`locked` 仍只认 `user_override`。
FAMILY_SOURCES = frozenset({"model", "user_override", "role_seed"})

SOURCE_MODEL = "model"
SOURCE_USER_OVERRIDE = "user_override"

# 1.0 的 ``state`` 三个取值。``retracted`` 只出现在 ``family_relationships`` 上：
# 用户撤回一条边时置它，并**同时**往 ``family_relationship_blocks`` 落一行挡住模型恢复。
STATE_EFFECTIVE = "effective"
STATE_SUPERSEDED = "superseded"
STATE_RETRACTED = "retracted"

# 家庭图谱里未命名人物的占位名。与 ``PENDING_CLUSTER_NAME`` 用同一句用户文案：
# 两个端点过去各叫一个词，用户在同一页看到两个“待命名”没有意义。
FAMILY_PENDING_DISPLAY_NAME = "未确认姓名人物"

# 1.0 ``family_graph_people`` 的代表照上限（``db.py:5668`` 的 ``LIMIT 3``）。
REPRESENTATIVE_MEDIA_LIMIT = 3


@dataclass(frozen=True, slots=True)
class FamilyMembershipRecord:
    """``family_person_memberships`` 一行：一个人在这个 scope 里的归属。"""

    id: str
    scope_id: str
    entity_id: str
    membership: str
    source: str
    confidence: float
    evidence_refs: tuple[str, ...]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FamilyRelationshipRecord:
    """``family_relationships`` 一行。

    **一条边在库里是两行**（正 + 反），这是 ``set_family_relationship`` 的落库形状：
    查某个人的关系时不用做反向映射，直接按 ``subject_entity_id`` 取就是他说的话。
    """

    id: str
    scope_id: str
    family_graph_id: str | None
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    inverse_predicate: str
    source: str
    confidence: float
    evidence_refs: tuple[str, ...]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FamilyAnalysisRunRecord:
    """``family_analysis_runs`` 一行：一次推断运行。

    ``config`` / ``stats`` 是库里的两个 ``*_json`` 列解出来的对象。**``stats`` 里的
    ``event_watermark`` 是触发去抖的唯一依据**（S7b2）——完成时写入当时的事件数，
    下次触发时事件数没涨就不跑；失败时 ``stats`` 保持空，于是下次一定重跑。
    """

    id: str
    scope_id: str
    status: str
    current_stage: str
    config: Mapping[str, object]
    stats: Mapping[str, object]
    error: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class FamilyPortraitRecord:
    """``family_person_portraits`` 一行：家庭画像的那段文字。

    **与 S5c 的 ``person_portrait_revisions`` 不是一套表。** 两套在 1.0 并存、服务两个
    端点（``/api/people/{id}/portrait`` 对 ``GET /api/family-graph``），2.0 保持并存——
    把家庭那套并进画像那套会让「家庭图谱里的那段话」与「人物画像」这两个不同产物
    变成一个，而它们的内容与更新时机都不一样。
    """

    id: str
    scope_id: str
    entity_id: str
    portrait_text: str
    evidence_refs: tuple[str, ...]
    source: str
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FamilyPersonProjection:
    """``GET /family-graph`` 里 ``people[]`` 的一项（1.0 ``db.py:5649-5688``）。

    **三个派生字段各有自己的一套查询，不能复用 ``EntityDecoration``**：
    头像的排序键多一位 ``quality DESC``；``representative_media`` 走的是
    「这个人的脸出现在哪些照片」（``face_instances⋈face_clusters⋈assets``）而不是
    「这个实体最新的观察」；``appearance_count`` 数的是**人脸实例行数**而不是不同资产数。
    三处差异都是 1.0 原样，见 ``read_family_graph.py``。
    """

    id: str
    scope_id: str
    display_name: str
    membership: FamilyMembershipRecord | None
    portrait: FamilyPortraitRecord | None
    avatar_face_instance_id: str | None
    appearance_count: int
    representative_media: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    # 实体自己的 ``status``（``pending`` / ``confirmed``）。名录与详情据此决定给「确认身份」
    # 还是「改名」：只改名不确认，问答与按人找照片都看不到这个人（2026-11-11 王王事故）。
    status: str = ENTITY_STATUS_PENDING
    # 用户起的其他称呼。名录与选人列表按别名也能搜到人，不必再逐个拉 ``GET /people/{id}``。
    aliases: tuple[str, ...] = ()
    # 用户标成「路人」的人：名录把他收进偶遇面孔区，不删任何数据。
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class FamilyEvidenceRow:
    """``_build_text_evidence`` 的一条原始行（1.0 ``family_graph_service.py:174-190``）。

    一次「某个人的某张脸出现在某条观察里」。**它是给模型看的文本的来源**，
    所以每一列都是输入的一部分。

    1.0 那条查询选的是 ``caption`` / ``activity`` / ``canonical_json`` / ``detail_json``。
    前两列 2.0 同名；后两列 2.0 **都没有写入者**（``detail_json`` 恒 ``'{}'``，
    ``canonical_json`` 连列都没有）。但 1.0 装在 canonical 里的那批语义，2.0 是
    **平铺列**且**有值**——``place`` / ``event_type`` / ``objects_json`` /
    ``clothing_json`` / ``people_json`` / ``facts_json`` / ``ocr_text``。
    所以这里带的是它们的等价物，由 ``_semantic_description`` 重建成同一个块。

    **默认值不是装饰**：这些字段是后补的，给默认值让既有构造点不必逐个改。
    """

    entity_id: str
    face_instance_id: str
    confidence: float
    observation_id: str
    caption: str
    activity: str
    detail_json: str
    place: str = ""
    event_type: str = ""
    ocr_text: str = ""
    objects_json: str = "[]"
    clothing_json: str = "[]"
    people_json: str = "[]"
    facts_json: str = "[]"
