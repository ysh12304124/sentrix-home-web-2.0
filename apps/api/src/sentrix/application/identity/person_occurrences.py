"""人物出现读取与单次归属驳回。

出现读取只返回本图一次出现：未命名时用稳定 ID 标签，不泄露内部簇名；姓名只在
身份状态允许时显示。驳回只解除这一次归属，不 reject 整个人、不删除图和脸。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.domain.identity.models import FaceInstanceRecord, PersonOccurrenceRecord
from sentrix.domain.identity.ports import FaceRepository


@dataclass(frozen=True, slots=True)
class PersonOccurrenceList:
    scope_id: str
    items: tuple[PersonOccurrenceRecord, ...]


class ReadPersonOccurrences:
    def __init__(self, *, faces: FaceRepository) -> None:
        self._faces = faces

    def execute(
        self,
        *,
        scope_id: str,
        person_id: str = "",
        face_instance_id: str = "",
        asset_id: str = "",
        description_query: str = "",
        identity_state: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> PersonOccurrenceList:
        if not scope_id.strip():
            raise ValueError("scope_id is required")
        return PersonOccurrenceList(
            scope_id=scope_id,
            items=tuple(
                self._faces.list_occurrences(
                    scope_id=scope_id,
                    person_id=person_id,
                    face_instance_id=face_instance_id,
                    asset_id=asset_id,
                    description_query=description_query,
                    identity_state=identity_state,
                    limit=limit,
                    offset=offset,
                )
            ),
        )


class RejectFaceAssignment:
    def __init__(self, *, faces: FaceRepository) -> None:
        self._faces = faces

    def execute(self, face_instance_id: str, *, expected_cluster_id: str) -> FaceInstanceRecord:
        if not face_instance_id.strip() or not expected_cluster_id.strip():
            raise ValueError("face_instance_id and expected_cluster_id are required")
        return self._faces.reject_assignment(face_instance_id, expected_cluster_id=expected_cluster_id)


class ConfirmFaceAssignment:
    def __init__(self, *, faces: FaceRepository) -> None:
        self._faces = faces

    def execute(self, face_instance_id: str, *, expected_cluster_id: str) -> FaceInstanceRecord:
        if not face_instance_id.strip() or not expected_cluster_id.strip():
            raise ValueError("face_instance_id and expected_cluster_id are required")
        return self._faces.confirm_assignment(face_instance_id, expected_cluster_id=expected_cluster_id)
