"""在线匹配：一张人脸进来，决定它属于谁、或者给它单开一个待确认的簇。

逐分支移植 1.0 ``backend/db.py:4815-4911`` 的 ``add_face_instance``。它的九条分支各自
对应一个「静默出错」的形态，所以这里一条都没合并、一条都没重排：

1. ``scope_id`` **由 asset 决定，调用方传不进来**——参数表里没有它，不是忘了加；
2. 资格闸门是 ``identity_eligible`` 与 ``validity == "verified"`` 的合取，不合格就落一条
   ``cluster_id = NULL`` 的实例直接返回，**不建簇**；
3. ``quality >= 0.30`` 才进候选循环——低质量人脸最容易同时像好几个人，放进匹配就是把
   两个身份连成一片；
4. 候选是**同 scope** 且 ``status IN ('pending','confirmed')``；
5. 原型签名 ``(model_name, model_version)`` 与新向量不一致 → 跳过该簇（不同模型的向量
   不可比）；**没有原型时不跳过**，退回代表向量；
6. 分数取与所有原型余弦的**最大值**；
7. 都不像（或全被跳过了）→ 建新簇，**同时建一个 pending person 实体**；
8. 落实例 → 刷原型 → 写 ``memory_vectors(space='visual', source_type='face_instance')``；
9. **只有 ``status='confirmed'`` 的实体才写提及**。

**两个 0.30 不是同一个 0.30。** ``MATCH_THRESHOLD``（1.0 的 ``threshold=0.30``）是余弦
阈值；``MINIMUM_MATCH_QUALITY``（1.0 的 ``quality >= 0.30``）是质量门槛。数值相同、
语义无关，改一个不影响另一个，所以它们必须是两个独立的名字——合成一个常量之后，
下一次有人调「匹配太松」会连带把质量门一起调掉，而这两件事的方向恰好相反。

**没有做的事**：不写 ``entity_mentions`` 之外的任何投影。事件参与者、重分段、人物画像
是 Task 81 / 86，它们读的正是这里写的提及，顺序反过来就是循环。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from sentrix.domain.identity.faces import compute_face_quality, cosine, normalize_embedding, pose_bucket
from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    PENDING_CLUSTER_SUMMARY,
    FaceDetection,
    pending_cluster_name,
)
from sentrix.domain.identity.ports import (
    EntityRepository,
    FaceDetector,
    FaceEnroller,
    FaceRepository,
    PreparedFaces,
)
from sentrix.domain.media.ports import AssetRepository
from sentrix.domain.retrieval.ports import VectorRecord, VectorRepository
from sentrix.domain.retrieval.scheme import VISUAL_SPACE

# 余弦阈值：新脸与候选簇的最高分小于它就不算同一个人。1.0 ``db.py:4815`` 的默认实参。
MATCH_THRESHOLD = 0.30
# 质量门槛：低于它的人脸根本不参与匹配。1.0 ``db.py:4868`` 的字面量。
# 与上一行数值相同纯属巧合——一个是「像不像」，一个是「这张脸够不够清楚」。
MINIMUM_MATCH_QUALITY = 0.30

# 1.0 ``add_face_instance(model_name="buffalo_l")`` 的默认值。
DEFAULT_EMBEDDING_MODEL = "buffalo_l"
# 检测器没说版本时的兜底。1.0 ``db.py:4846`` 的字面量：``"legacy"`` 是一个**真实**的
# 签名取值，它会参与 (model_name, model_version) 比对，不是一个占位符。
LEGACY_EMBEDDING_VERSION = "legacy"

# 空间名不在这里重定义：``scheme.py`` 是写入 / 查询 / 重建索引三处唯一的来源
# （``domain/retrieval/scheme.py`` 的模块注释写了理由）。
FACE_INSTANCE_SOURCE = "face_instance"


@dataclass(frozen=True, slots=True)
class FaceEnrollment:
    """一次匹配的结论。1.0 返回的是 ``{"id", "cluster_id", "score", "embedding"}``。"""

    instance_id: str
    cluster_id: str | None
    score: float
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedFace:
    """闸门过完之后的一张脸：模型身份、质量、姿态桶都已定下来。

    单独一层是为了让「算过一次」这件事在类型上可见——``quality`` 在这里不再是
    ``float | None``，后面的代码不许再猜它有没有被算过。
    """

    embedding: tuple[float, ...]
    pose: tuple[float, ...]
    quality: float
    pose_bucket: str
    embedding_model: str
    embedding_version: str
    identity_eligible: bool


def _resolved(face: FaceDetection, model_name: str) -> _ResolvedFace:
    """1.0 ``db.py:4824-4849``：归一化、补算质量与姿态桶、定签名、算资格。"""
    embedding = tuple(normalize_embedding(face.embedding))
    pose = tuple(float(value) for value in face.pose)
    quality = face.quality
    bucket = face.pose_bucket
    if quality is None:
        bucket = bucket or pose_bucket(pose)
        quality = compute_face_quality(face.confidence, face.area_ratio, face.sharpness, pose)
    else:
        quality = float(quality)
        if not bucket:
            bucket = pose_bucket(pose)
    # ``identity_eligible`` 缺席时取 ``identity_ready``，而 ``identity_ready`` 缺省为真。
    eligible = face.identity_ready if face.identity_eligible is None else face.identity_eligible
    return _ResolvedFace(
        embedding=embedding,
        pose=pose,
        quality=float(quality),
        pose_bucket=str(bucket or "unknown"),
        embedding_model=str(face.embedding_model or model_name or "unknown"),
        embedding_version=str(face.embedding_version or LEGACY_EMBEDDING_VERSION),
        # 空向量是「没有可用向量」：它连资格都没有，更谈不上匹配。
        identity_eligible=bool(eligible) and face.validity == "verified" and bool(embedding),
    )


class EnrollFace:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        vectors: VectorRepository,
        assets: AssetRepository,
        commit_guard: Callable[[str], AbstractContextManager[None]] | None = None,
    ) -> None:
        self._commit_guard = commit_guard
        self._faces = faces
        self._entities = entities
        self._vectors = vectors
        self._assets = assets

    def enroll(
        self,
        asset_id: str,
        observation_id: str,
        face: FaceDetection,
        *,
        threshold: float = MATCH_THRESHOLD,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
    ) -> FaceEnrollment:
        guard = self._commit_guard(asset_id) if self._commit_guard else nullcontext()
        with guard:
            return self._enroll_active(
                asset_id, observation_id, face, threshold=threshold, model_name=model_name
            )

    def _enroll_active(
        self,
        asset_id: str,
        observation_id: str,
        face: FaceDetection,
        *,
        threshold: float,
        model_name: str,
    ) -> FaceEnrollment:
        # scope 只能从 asset 读：1.0 ``db.py:4816`` 的 ``asset.get("scope_id")``。
        # 让调用方传进来等于开一条「把这张脸算进别人家簇里」的入口，而它不报错。
        asset = self._assets.get(asset_id)
        scope_id = (asset.scope_id if asset else "") or DEFAULT_SCOPE_ID
        resolved = _resolved(face, model_name)

        if not resolved.identity_eligible:
            return self._store(
                asset_id,
                observation_id,
                face,
                resolved,
                scope_id=scope_id,
                cluster_id=None,
                score=0.0,
                metadata_extra={"identity_eligible": False},
            )

        cluster_id, score = self._best_cluster(scope_id, resolved, threshold)
        if cluster_id is None:
            cluster_id = self._create_pending_cluster(resolved, scope_id)
        return self._store(
            asset_id,
            observation_id,
            face,
            resolved,
            scope_id=scope_id,
            cluster_id=cluster_id,
            score=score,
            metadata_extra={},
        )

    # ---- 匹配 --------------------------------------------------------------------

    def _best_cluster(
        self, scope_id: str, resolved: _ResolvedFace, threshold: float
    ) -> tuple[str | None, float]:
        """同 scope 的候选簇里挑一个最像的；没有够像的就给 ``None``。

        质量门在循环**外面**（1.0 ``db.py:4867`` 的 ``if quality >= 0.30:``）：它省掉的
        是整轮匹配，不只是某一次比对。
        """
        best_id: str | None = None
        best_score = 0.0
        if resolved.quality < MINIMUM_MATCH_QUALITY:
            return None, 0.0
        for cluster in self._faces.list_candidates(scope_id):
            representatives = self._representatives(cluster.id, resolved)
            if representatives is None:
                continue
            score = max(
                (cosine(resolved.embedding, item) for item in representatives),
                default=0.0,
            )
            # 严格大于：并列时保留先到的那个，结果与候选顺序无关。
            if score > best_score:
                best_id, best_score = cluster.id, score
        if best_id is None or best_score < threshold:
            return None, best_score
        return best_id, best_score

    def _representatives(
        self, cluster_id: str, resolved: _ResolvedFace
    ) -> Sequence[tuple[float, ...]] | None:
        """这个簇拿来比对的向量；``None`` 表示整簇跳过。

        签名不匹配就跳过（不同模型的向量不可比）。**一个原型都没有时不跳过**——那正是
        刚建出来还没落实例的簇所处状态，跳过它等于每次都另开一簇。
        """
        prototypes = self._faces.list_prototypes(cluster_id)
        signatures = {(item.model_name, item.model_version) for item in prototypes}
        if signatures and (resolved.embedding_model, resolved.embedding_version) not in signatures:
            return None
        if prototypes:
            return [item.embedding for item in prototypes]
        cluster = self._faces.get_cluster(cluster_id)
        if cluster is None or not cluster.representative_embedding:
            return None
        return [cluster.representative_embedding]

    # ---- 写入 --------------------------------------------------------------------

    def _create_pending_cluster(self, resolved: _ResolvedFace, scope_id: str) -> str:
        """新建簇 + 它的 pending 实体，返回簇 id。

        实体名用的是**簇 id**，所以必须先建簇再建实体（1.0 的三条语句就是这个顺序：
        建簇 → 建实体 → 回填 ``entity_id``）。
        """
        cluster = self._faces.create_cluster(
            resolved.embedding, confidence=resolved.quality, scope_id=scope_id
        )
        entity = self._entities.create_person(
            pending_cluster_name(cluster.id),
            status=ENTITY_STATUS_PENDING,
            confidence=resolved.quality,
            summary=PENDING_CLUSTER_SUMMARY,
            scope_id=scope_id,
        )
        self._faces.link_entity(cluster.id, entity.id)
        return cluster.id

    def _store(
        self,
        asset_id: str,
        observation_id: str,
        face: FaceDetection,
        resolved: _ResolvedFace,
        *,
        scope_id: str,
        cluster_id: str | None,
        score: float,
        metadata_extra: dict[str, object],
    ) -> FaceEnrollment:
        instance = self._faces.add_instance(
            asset_id=asset_id,
            observation_id=observation_id,
            cluster_id=cluster_id,
            bbox=face.bbox,
            embedding=resolved.embedding,
            detection_confidence=face.confidence,
            quality=resolved.quality,
            pose=resolved.pose,
            pose_bucket_name=resolved.pose_bucket,
            embedding_model=resolved.embedding_model,
            embedding_version=resolved.embedding_version,
            embedding_quality_signal=face.quality_signal,
            validity=face.validity,
            area_ratio=face.area_ratio,
            sharpness=face.sharpness,
        )
        if cluster_id is not None:
            self._faces.refresh_prototypes(cluster_id)
        self._write_vector(
            instance.id, asset_id, observation_id, scope_id, resolved, cluster_id, metadata_extra
        )
        if cluster_id is not None:
            self._link_confirmed(instance.id, observation_id, cluster_id, face.confidence)
        return FaceEnrollment(
            instance_id=instance.id,
            cluster_id=cluster_id,
            score=score,
            embedding=resolved.embedding,
        )

    def _write_vector(
        self,
        instance_id: str,
        asset_id: str,
        observation_id: str,
        scope_id: str,
        resolved: _ResolvedFace,
        cluster_id: str | None,
        metadata_extra: dict[str, object],
    ) -> None:
        """权威向量。空向量不写——1.0 的 ``upsert_vector`` 自己会挡，2.0 的仓储直接抛。"""
        if not resolved.embedding:
            return
        metadata: dict[str, object] = {
            "asset_id": asset_id,
            "observation_id": observation_id,
            "model_version": resolved.embedding_version,
            "quality": resolved.quality,
            "pose_bucket": resolved.pose_bucket,
        }
        if cluster_id is not None:
            metadata["cluster_id"] = cluster_id
        metadata.update(metadata_extra)
        self._vectors.upsert(
            VectorRecord(
                # scope 随 asset 走，与实例落的是同一个值。
                scope_id=scope_id,
                space=VISUAL_SPACE,
                source_type=FACE_INSTANCE_SOURCE,
                source_id=instance_id,
                model_name=resolved.embedding_model,
                vector=tuple(float(value) for value in resolved.embedding),
                metadata=metadata,
            )
        )

    def _link_confirmed(
        self, instance_id: str, observation_id: str, cluster_id: str, confidence: float
    ) -> None:
        """只有已确认的实体才写提及。

        1.0 在调用点上判 ``entity.get("status") == "confirmed"``；2.0 的仓储也会自己
        挡一道——两处都判是有意的：这一处决定要不要写，仓储那一处保证谁都绕不过去。
        """
        cluster = self._faces.get_cluster(cluster_id)
        if cluster is None or cluster.entity_id is None:
            return
        entity = self._entities.get(cluster.entity_id)
        if entity is None or entity.status != ENTITY_STATUS_CONFIRMED:
            return
        self._entities.link_confirmed_mention(
            entity.id,
            observation_id,
            face_instance_id=instance_id,
            confidence=confidence,
        )


class EnrollFaces(FaceEnroller):
    """导入链上的人脸两段式：``prepare`` 只检测，``commit`` 做匹配与全部写库。

    ``EnrollFace`` 是**单张脸**的用例；这一层包住「一张图 → 若干张脸」的循环与
    检测本身。拆开是因为 ``detect`` 是这一段里唯一能离开串行侧的活：匹配读的是
    同 scope 的候选簇，而那些簇是同批前一张照片刚写进去的——把它挪到并行侧会把
    同一个人的两张照片各建一个 pending 簇，**不报错**。
    """

    def __init__(self, *, detector: FaceDetector, enroll: EnrollFace) -> None:
        self._detector = detector
        self._enroll = enroll

    def prepare(self, path: str) -> PreparedFaces:
        """只检测（本地 ONNX，可并行）。空表是有效结论——这张图确实没有脸。"""
        return PreparedFaces(detections=tuple(self._detector.detect(path)))

    def commit(self, asset_id: str, observation_id: str, prepared: PreparedFaces) -> int:
        """匹配 + 建簇 + 实例 / 原型 / 向量 / 提及。**全部写库都在这里。**"""
        for face in prepared.detections:
            self._enroll.enroll(asset_id, observation_id, face)
        return len(prepared.detections)
