"""已确认人物的目录：把实体仓储适配成助手工具要的档案。

S3 装配的是 ``NoIdentityDirectory``——那时身份层还没建起来，「查不到」与「没有这个人」
是同一件事，如实回 ``None`` 是唯一诚实的结果。S5 起库里有真的已确认人物，所以这条查询
有了真答案。**工具契约、账本与门禁一行都没动**（S3 那个占位实现的模块注释当初就是这么
约定的）：换掉的只是容器里那一个字段的实参。

**只认已确认的人。** 判据是 ``find_confirmed_person_by_name`` 本身——它查的正是
``entity_type='person' AND status='confirmed'``，先精确名再扫别名。未确认的候选人物簇
不进这个目录：那还只是一次聚类猜测，把它当档案答出去，等于让猜测变成检索事实
（``link_confirmed_mention`` 拒绝未确认实体，是同一条理由）。

**状态码与错误码不在这里。** 这个端口只有一个答案形状（``PersonProfile | None``），
「没有这个人」不是错误，是 ``None``——工具据此输出 gap 而不是抛异常。

**S7c 起它同时回答「这个人在家庭图谱里的归属与关系」。** 1.0 的同一件事在
``db.py:2840`` 的 ``person_profile_digest`` 里做，读的正是
``family_person_memberships`` / ``family_relationships`` / ``family_person_portraits``
三张表——**不是**通用 ``relationships`` 表。这里照同一条口径。
富化放在目录里而不是助手用例里：application 层只能依赖 domain 端口
（``application-purity`` 契约禁止 import ``sentrix.infrastructure``），
而「我们知道这个人的什么」本来就该由目录答。
"""

from __future__ import annotations

from sentrix.domain.assistant.models import PersonProfile, PersonRelationship
from sentrix.domain.identity.models import ENTITY_STATUS_PENDING, PENDING_CLUSTER_NAME
from sentrix.domain.identity.ports import EntityRepository, FamilyGraphRepository

# 对方实体被删、没名字或还没确认时用它，**不编造**，也不暴露内部 cluster id。
UNNAMED_MEMBER = PENDING_CLUSTER_NAME


class EntityPersonDirectory:
    def __init__(self, *, entities: EntityRepository, family: FamilyGraphRepository) -> None:
        self._entities = entities
        self._family = family

    def lookup(self, name: str, scope_id: str) -> PersonProfile | None:
        clean_name = str(name or "").strip()
        if not clean_name:
            return None
        match = self._entities.find_confirmed_person_by_name(scope_id, clean_name)
        if match is None:
            return None
        entity = self._entities.get(match.entity_id)
        if entity is None:
            return None
        membership = self._family.get_effective_membership(scope_id, entity.id)
        portrait = self._family.get_active_portrait(scope_id, entity.id)
        return PersonProfile(
            # 报**实体的本名**而不是查询串：按别名查到时，用户问的是别名、答案里的名字
            # 得是本名，否则模型会把这个人的两个名字当成两个人。
            name=entity.canonical_name,
            confirmed=True,
            # 1.0 ``db.py:2875-2876`` 的 ``or "unknown"`` / ``or ""``：没有归属行与
            # 「归属不明」是同一件事，不区分。
            membership=(membership.membership if membership is not None else "") or "unknown",
            membership_source=(membership.source if membership is not None else "") or "",
            summary=(portrait.portrait_text if portrait is not None else "") or "",
            relationships=self._relationships(scope_id, entity.id),
        )

    def _relationships(self, scope_id: str, entity_id: str) -> tuple[PersonRelationship, ...]:
        """只收**此人是 subject** 的边——``predicate`` 才是「此人是对方的什么」。

        2.0 的 ``set_relationship`` **一次写正向 + 反向两行**（F7 实测确认），不过滤的
        话同一段关系会出现两次（一次 ``父亲``、一次 ``儿子``），模型会把一个人说成两个身份。

        **1.0 的同一处过滤是残的**（``db.py:2852-2858``）：那边一段关系只按一个方向存，
        于是查「女儿」的档案拿不到父亲——而 1.0 的测试恰好只测了父为 subject 的方向，
        把它盖住了。2.0 因为双向存储，**同一个过滤拿到的反而是完整的关系集**。

        仓储那层是双向匹配（``subject_entity_id = ? OR object_entity_id = ?``），
        过滤放在这里、不放进仓储：仓储的那个方法别的消费方（图谱页）要的是全部边。
        """
        found: list[PersonRelationship] = []
        for row in self._family.list_effective_relationships(scope_id, entity_id):
            if row.subject_entity_id != entity_id:
                continue
            other = self._entities.get(row.object_entity_id)
            found.append(
                PersonRelationship(
                    predicate=row.predicate,
                    other_name=(
                        UNNAMED_MEMBER
                        if other is None
                        or other.status == ENTITY_STATUS_PENDING
                        or not other.canonical_name
                        else other.canonical_name
                    ),
                    source=row.source,
                )
            )
        return tuple(found)
