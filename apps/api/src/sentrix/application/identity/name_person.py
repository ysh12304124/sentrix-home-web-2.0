"""命名用例：改名，以及按**实体**确认一个人。

逐分支移植 1.0 ``backend/db.py`` 的 ``rename_person`` :4436 与 ``confirm_person_entity``
:5247。两者回答的是同一个问题的两种问法：这一簇脸是谁（簇入口，Task 73 的
``ReviewCluster``）／这个人是谁（人入口，这里）。

**为什么人入口不能直接调簇入口。** ``confirm_person_entity`` 的入参是**实体**，簇要由
它反查出来；而且当这个人一个簇都没有时它仍然要能完成确认（1.0 ``db.py:5270`` 的兜底
分支只写 ``entities`` 一行）。这一层就是那段反查与兜底，确认本身交给 ``ReviewCluster``
——同名并入、提及、家庭角色那几条规则只有一份。

**改名就是确认。** 2026-11-10 的用户裁决：在人物页给待确认面孔填名字，表示用户已经
认得这个人。因此改名后 ``status`` 也转 confirmed，agent 才能按已确认人物查到；若名字
撞上另一个已确认的人，沿用确认路的同名合并规则。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.application.identity.review_cluster import (
    ConfirmResult,
    InvalidReviewRequestError,
    ReviewCluster,
)
from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    ENTITY_TYPE_PERSON,
    EntityRecord,
)
from sentrix.domain.identity.ports import EntityRepository, FaceRepository


@dataclass(frozen=True, slots=True)
class RenameResult:
    """改名的结论。``aliases`` 是改完之后这个人的别名表（旧名在里面）。

    1.0 的路由（``app.py:2244``）改完名回读一次 ``person_aliases`` 再拼进响应；2.0 把
    这次回读收进结论里——它是改名这个动作的**结果**，不是一个可选的展示字段。
    """

    entity: EntityRecord
    aliases: tuple[str, ...]


class NamePerson:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        review: ReviewCluster,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._review = review

    def rename(self, entity_id: str, new_name: str) -> RenameResult | None:
        """给一个人改名，并把这个动作视为一次确认。

        产品语义（2026-11-10 用户裁决）：用户在人物页给一个未确认的面孔填了名字，
        就是明确表态「这是我认识的人」。因此改名不能再只把 ``name_state`` 置为
        ``confirmed`` 而让 ``status`` 停在 ``pending``——那样 agent 按已确认人物
        查名字时永远查不到他。改名后走 ``confirm_entity``：有可确认的簇就一并确认
        簇、写提及、刷事件；没有簇就直接确认实体。

        非 person 实体返回 ``None``（1.0 的 ``rename_person`` 对非 person 返回 None，
        路由据此报 404）；空名字抛 ``InvalidReviewRequestError``（400），那是调用方出错，
        不是「这个人不存在」。

        空名字在这里判而不是留给仓储：仓储抛的是裸 ``ValueError``，它不带码、也不是
        ``DomainError``，一路漏到处理器外面就成了 500——调用方传了个空串，收到的却是
        「服务器内部错误」。判据与 ``ReviewCluster.confirm`` 用的是同一个类。
        """
        if not str(new_name or "").strip():
            raise InvalidReviewRequestError("person name is required")
        entity = self._entities.rename_person(entity_id, new_name)
        if entity is None:
            return None
        if entity.status != ENTITY_STATUS_CONFIRMED:
            confirmed = self.confirm_entity(
                entity.id, name=str(new_name).strip(), family_role=entity.family_role
            )
            if confirmed is not None:
                entity = confirmed.entity
        return RenameResult(entity=entity, aliases=self._entities.person_aliases(entity.id))

    def confirm_entity(
        self,
        entity_id: str,
        *,
        name: str,
        family_role: str | None = None,
    ) -> ConfirmResult | None:
        """按实体确认一个人：1.0 ``confirm_person_entity``。

        三步，顺序是 1.0 的原文顺序：

        1. 这个人身上有没有一个还能确认的簇（``active_cluster_for_entity``，``pending``
           优先）——有就交给 ``ReviewCluster.confirm``，同名并入那条路也在它里面；
        2. 没有簇就走兜底：直接给实体置名、转 ``confirmed``，**不写修订**（1.0 这一段
           只有一条 ``UPDATE``）；
        3. 实体不存在或不是 person → ``None``（路由据此报 404）。

        1.0 在第一步之前先做了一次「有没有同名的人」的判断，命中就直接并进那个人。
        **2.0 不再重复这一次判断**：它选的簇与第二步的判据完全相同，而
        ``ReviewCluster.confirm`` 自己会做同名查询且带上了「命中的就是本簇实体」那条
        守卫（见该函数的注释）。留着等于把同一条判据写两遍，两遍漂开一次就是两种行为。
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise InvalidReviewRequestError("entity name is required")
        role = str(family_role or "").strip() or None

        entity = self._entities.get(entity_id)
        if entity is None or entity.entity_type != ENTITY_TYPE_PERSON:
            return None

        cluster = self._faces.active_cluster_for_entity(entity_id)
        if cluster is not None:
            return self._review.confirm(cluster.id, name=clean_name, family_role=role)

        confirmed = self._entities.confirm_person(
            entity_id,
            name=clean_name,
            family_role=role,
            summary=entity.summary,
        )
        return ConfirmResult(entity=confirmed, merged_into=None)
