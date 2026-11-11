"""把两个人物合并成一个。"""

from __future__ import annotations

from sentrix.domain.identity.models import EntityRecord
from sentrix.domain.identity.ports import EntityRepository, FaceRepository


class MergePeople:
    """人物级的合并：用户明确选择「这两个名字是同一个人」。

    人脸簇的 ``merge`` 只搬实例，两个已确认人物会被它拒绝。这里先决定保留哪个
    target 簇，再搬 source 的实例，最后让实体仓储把提及、事件、关系、归属一并挪过去；
    source 实体标 superseded，不再出现在人物列表或 agent 的已确认人物查询里。
    """

    def __init__(self, *, faces: FaceRepository, entities: EntityRepository) -> None:
        self._faces = faces
        self._entities = entities

    def execute(self, *, target_entity_id: str, source_entity_id: str) -> EntityRecord:
        if target_entity_id == source_entity_id:
            raise ValueError("cannot merge a person into themselves")
        target = self._entities.get(target_entity_id)
        source = self._entities.get(source_entity_id)
        if target is None or source is None:
            raise ValueError("person not found")

        target_cluster = self._faces.confirmed_cluster_for_entity(target.id)
        if target_cluster is None:
            target_cluster = self._faces.active_cluster_for_entity(target.id)

        for cluster in self._faces.list_clusters_for_entity(source.id):
            if target_cluster is None:
                # target 还没有可用的簇：直接把 source 的簇认领给 target。
                target_cluster = self._faces.confirm_cluster(cluster.id, target.id)
                continue
            if cluster.id == target_cluster.id:
                continue
            self._faces.move_instances(cluster.id, target_cluster.id)
            self._faces.retire_cluster(cluster.id, entity_id=target.id)

        if target_cluster is not None:
            self._faces.refresh_prototypes(target_cluster.id)

        return self._entities.merge_person_into(target.id, source.id)
