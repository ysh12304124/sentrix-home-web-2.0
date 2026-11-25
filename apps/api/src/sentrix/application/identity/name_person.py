"""命名用例：改名，以及按**实体**确认一个人。

逐分支移植 1.0 ``backend/db.py`` 的 ``rename_person`` :4436 与 ``confirm_person_entity``
:5247。两者回答的是同一个问题的两种问法：这一簇脸是谁（簇入口，Task 73 的
``ReviewCluster``）／这个人是谁（人入口，这里）。

**为什么人入口不能直接调簇入口。** ``confirm_person_entity`` 的入参是**实体**，簇要由
它反查出来；而且当这个人一个簇都没有时它仍然要能完成确认（1.0 ``db.py:5270`` 的兜底
分支只写 ``entities`` 一行）。这一层就是那段反查与兜底，确认本身交给 ``ReviewCluster``
——同名并入、提及、家庭角色那几条规则只有一份。

**改名不做同名合并。** 1.0 的 ``rename_person`` 只改名；把 B 改成 A 的名字不会把两人
并起来。同名合并只发生在**确认**这条路上（那时才有人脸证据说明这是同一个人）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.application.identity.review_cluster import (
    ConfirmResult,
    InvalidReviewRequestError,
    ReviewCluster,
)
from sentrix.domain.identity.models import (
    ENTITY_TYPE_PERSON,
    EntityRecord,
)
from sentrix.domain.identity.ports import EntityRepository, FaceRepository
from sentrix.domain.memories.ports import MemoryCorrectionRepository


@dataclass(frozen=True, slots=True)
class RenameResult:
    """改名的结论。``aliases`` 是改完之后这个人的别名表（旧名在里面）。

    1.0 的路由（``app.py:2244``）改完名回读一次 ``person_aliases`` 再拼进响应；2.0 把
    这次回读收进结论里——它是改名这个动作的**结果**，不是一个可选的展示字段。
    """

    entity: EntityRecord
    aliases: tuple[str, ...]
    sync_job_id: str = ""


class NamePerson:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        review: ReviewCluster,
        corrections: MemoryCorrectionRepository | None = None,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._review = review
        self._corrections = corrections

    def rename(self, entity_id: str, new_name: str) -> RenameResult | None:
        """给一个人改名：旧名折进别名，``name_state`` 转 ``confirmed``，留一条修订。

        非 person 实体返回 ``None``（1.0 的 ``rename_person`` 对非 person 返回 None，
        路由据此报 404）；空名字抛 ``InvalidReviewRequestError``（400），那是调用方出错，
        不是「这个人不存在」。

        空名字在这里判而不是留给仓储：仓储抛的是裸 ``ValueError``，它不带码、也不是
        ``DomainError``，一路漏到处理器外面就成了 500——调用方传了个空串，收到的却是
        「服务器内部错误」。判据与 ``ReviewCluster.confirm`` 用的是同一个类。
        """
        if not str(new_name or "").strip():
            raise InvalidReviewRequestError("person name is required")
        if self._corrections is not None:
            saved = self._corrections.rename_person(entity_id, new_name)
            return RenameResult(*saved) if saved else None
        entity = self._entities.rename_person(entity_id, new_name)
        if entity is None:
            return None
        return RenameResult(entity=entity, aliases=self._entities.person_aliases(entity.id))

    def confirm_entity(
        self,
        entity_id: str,
        *,
        name: str,
        family_role: str | None = None,
        merge_same_name: bool = True,
    ) -> ConfirmResult | None:
        """按实体确认一个人：1.0 ``confirm_person_entity``。

        ``merge_same_name`` 原样交给 ``ReviewCluster.confirm``：用户说了「只是同名」时为
        ``False``，不做同名并入。

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
            return self._review.confirm(
                cluster.id, name=clean_name, family_role=role, merge_same_name=merge_same_name
            )

        confirmed = self._entities.confirm_person(
            entity_id,
            name=clean_name,
            family_role=role,
            summary=entity.summary,
        )
        return ConfirmResult(entity=confirmed, merged_into=None)
