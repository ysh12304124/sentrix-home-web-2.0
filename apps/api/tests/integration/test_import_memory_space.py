"""导入路径建记忆空间：全新部署导入一整册之后，人物页不该是空的。

**这条缺口是 dev §2k 第 97 条。** ``FaceRepository.list_clusters`` **内连**
``memory_spaces``（``include_in_people``），而 2.0 在本次裁决之前**全仓零写入者**：
簇与脸都在库里，`/face-clusters` 一个都读不出来——不是「少一条数据」，是审阅页的
「待命名成员」那半边在默认路径上不可见。（第 97 条说 ``list_entities`` 也内连，
**那半句不成立**，第 103 条纠正：它用的是子查询，不受影响。）

1.0 建空间的地方是 ``db.py:1462`` 的 ``create_asset``（单张 ``/api/ingest`` 与批量
``/api/import`` 都从它过），名字与类型是 ``create_memory_space(scope_id, scope_id)``
（``name = scope_id``、``kind`` 取签名默认 ``household``）。2.0 的同一个落点是
``ImportAssets.execute``——``ImportAlbum`` 也走它，所以单张与整册两条入口一次覆盖。

**门禁的形式是「读不出来」而不是「ensure 被调过」。** 只断言协作者被调用的实现，
在「调了但写错了 scope」时同样是绿的；这里断言的是两个列表端点真的看得见那个簇。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sentrix.application.identity.enroll_face import EnrollFace, EnrollFaces
from sentrix.application.media.import_album import AlbumImportRequest, ImportAlbum
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.identity.models import FaceDetection
from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

SCOPE = "album-new"


def unit(*values: float) -> tuple[float, ...]:
    numbers = list(values) + [0.0] * (8 - len(values))
    norm = sum(value * value for value in numbers) ** 0.5
    return tuple(value / norm for value in numbers)


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        path = self._root / f"{asset_id}-{file_name}"
        path.write_bytes(content)
        return path

    def copy_from_path(self, asset_id: str, file_name: str, source_path: Path) -> Path:
        return self.store(asset_id, file_name, source_path.read_bytes())


class FakeMetadata:
    def read(self, path: Path) -> tuple[Any, GpsPoint | None, Any]:
        from datetime import UTC, datetime

        return datetime(2017, 10, 5, 12, 0, tzinfo=UTC), None, None


class OneFaceDetector:
    """每张图都检出一张脸——这一条验的是空间行，人脸只是把簇造出来的手段。"""

    def detect(self, path: str) -> list[FaceDetection]:
        return [
            FaceDetection(
                embedding=unit(1.0),
                bbox=(10.0, 10.0, 110.0, 110.0),
                confidence=0.95,
                quality=0.9,
                pose_bucket="frontal",
                area_ratio=0.1,
                sharpness=0.3,
                embedding_model="buffalo_l",
                embedding_version="buffalo_l",
                validity="verified",
            )
        ]


@pytest.fixture
def repos(migrations_ready_db: Database) -> dict[str, Any]:
    return {
        "faces": SqliteFaceRepository(migrations_ready_db),
        "entities": SqliteEntityRepository(migrations_ready_db),
        "vectors": SqliteVectorRepository(migrations_ready_db),
        "assets": SqliteAssetRepository(migrations_ready_db),
        "observations": SqliteObservationRepository(migrations_ready_db),
        "spaces": SqliteSpaceRepository(migrations_ready_db),
    }


def build_importer(db: Database, repos: dict[str, Any], tmp_path: Path, *, with_spaces: bool) -> ImportAssets:
    enroll = EnrollFace(
        faces=repos["faces"],
        entities=repos["entities"],
        vectors=repos["vectors"],
        assets=repos["assets"],
    )
    detector = OneFaceDetector()

    collaborators: dict[str, Any] = {
        "assets": repos["assets"],
        "observations": repos["observations"],
        "storage": FakeStorage(tmp_path),
        "metadata": FakeMetadata(),
        "face_enroller": EnrollFaces(detector=detector, enroll=enroll),
    }
    if with_spaces:
        collaborators["spaces"] = repos["spaces"]
    return ImportAssets(**collaborators)


def cluster_rows(db: Database, scope_id: str) -> int:
    """直接数库里的簇——绕开那两个内连 ``memory_spaces`` 的读端点。"""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM face_clusters WHERE scope_id = ?", (scope_id,)
        ).fetchone()
    return int(row[0])


def test_a_cluster_can_exist_in_the_database_and_still_be_invisible_on_the_people_page(
    migrations_ready_db: Database, repos: dict[str, Any], tmp_path: Path
) -> None:
    """**反向的那一半。** 不建空间行时，簇确实在库里，而簇列表一个都读不到。

    这一条不是「顺便验一下旧行为」：它把「人物页为空」这件事的物理原因钉在
    ``memory_spaces`` 上——少了那一行，「空」与「没有」就被混成同一件事了（第 47 条）。

    **它红在哪一步是量过的，订正见第 113 条。** 原话写的是「把内连接换成左连接，它会
    立刻变红」——**不成立**：``list_clusters`` 的约束是**两半**，``JOIN memory_spaces ms
    ON ms.id = fc.scope_id`` ∧ ``ms.include_in_people = 1``，而只换成 ``LEFT JOIN``（M6）
    时，没匹配上的行被补 ``NULL``、``NULL = 1`` 不成立，谓词自己就把它们挡掉了——两半在
    这个查询里是**等价的**，所以没有任何用例能把它们分开。把整条约束一起拿掉（M6b）才会
    红，红的正是这一条。

    谓词那一半另有两条门禁，不靠这一条：
    ``test_identity_repositories.test_list_clusters_honours_include_in_people`` 与
    ``test_identity_routes.test_clusters_from_a_hidden_space_never_enter_the_people_view``
    （造一行 ``include_in_people = 0`` 的空间）。两半各有各的用例，合起来才是那条约束
    ——所以这里**不**再补一条「钉住内连接」的用例：想钉的是那句话，不是 ``INNER JOIN``
    这个写法。

    **顺带纠正第 97 条的一处措辞。** 那条说 ``list_entities`` 也内连 ``memory_spaces``
    ——不是：它用的是 ``scope_id NOT IN (SELECT id FROM memory_spaces WHERE
    include_in_people = 0)`` 这个子查询（1.0 ``db.py:4043`` 的 ``list_entities`` 原样），
    没登记过的 scope 反而**照常列出来**。所以缺空间行真正打掉的是**簇**这一面
    （``/face-clusters``，也就是审阅页的「待命名成员」列表），不是人物列表。这条断言
    把它钉住，免得后来人照着那句描述去「修」一个不存在的对称。
    """
    importer = build_importer(migrations_ready_db, repos, tmp_path, with_spaces=False)
    result = importer.execute(
        ImportRequest(scope_id=SCOPE, file_name="a.jpg", content_type="image/jpeg", content=b"a")
    )

    assert result.faces == 1
    assert cluster_rows(migrations_ready_db, SCOPE) == 1
    assert repos["faces"].list_clusters() == []
    assert [item.scope_id for item in repos["entities"].list_entities(scope_id=SCOPE)] == [SCOPE]
    assert repos["spaces"].get(SCOPE) is None


def test_the_import_path_ensures_the_space_so_the_cluster_is_visible(
    migrations_ready_db: Database, repos: dict[str, Any], tmp_path: Path
) -> None:
    """接了空间协作者之后，同一个导入让簇列表看得见这个簇（人物列表本来就看得见它）。"""
    importer = build_importer(migrations_ready_db, repos, tmp_path, with_spaces=True)
    result = importer.execute(
        ImportRequest(scope_id=SCOPE, file_name="a.jpg", content_type="image/jpeg", content=b"a")
    )

    assert result.faces == 1
    clusters = repos["faces"].list_clusters()
    assert [item.scope_id for item in clusters] == [SCOPE]
    assert [item.member_count for item in clusters] == [1]
    people = repos["entities"].list_entities(scope_id=SCOPE)
    assert [item.id for item in people] == [clusters[0].entity_id]


def test_the_space_row_carries_the_one_point_zero_name_and_kind(
    migrations_ready_db: Database, repos: dict[str, Any], tmp_path: Path
) -> None:
    """1.0 ``db.py:1462`` 的 ``create_memory_space(scope_id, scope_id)``。

    ``kind`` 取签名默认 ``household``，**不是**批量端点里那个 ``benchmark``——
    那是 1.0 的评测旁路（dev 第 6/30 条：benchmark 内容不移植进生产代码）。
    """
    build_importer(migrations_ready_db, repos, tmp_path, with_spaces=True).execute(
        ImportRequest(scope_id=SCOPE, file_name="a.jpg", content_type="image/jpeg", content=b"a")
    )

    space = repos["spaces"].get(SCOPE)
    assert space is not None
    assert (space.id, space.name, space.kind, space.status) == (
        SCOPE,
        SCOPE,
        "household",
        "active",
    )


def test_an_existing_space_is_left_untouched(
    migrations_ready_db: Database, repos: dict[str, Any], tmp_path: Path
) -> None:
    """``ensure`` 是幂等的：用户给空间起过的名字不该被导入覆盖成 scope id。"""
    repos["spaces"].ensure(SCOPE, "外婆家", kind="household")
    build_importer(migrations_ready_db, repos, tmp_path, with_spaces=True).execute(
        ImportRequest(scope_id=SCOPE, file_name="a.jpg", content_type="image/jpeg", content=b"a")
    )

    space = repos["spaces"].get(SCOPE)
    assert space is not None and space.name == "外婆家"


def test_the_album_entry_ensures_the_space_too(
    migrations_ready_db: Database, repos: dict[str, Any], tmp_path: Path
) -> None:
    """整册导入（``/api/v1/imports``）与单张（``/api/v1/media/import``）共用同一条链。

    分开接的话，「整册导入之后人物页是空的、单张导入之后不是」这种差异在两端各看一半时
    都看不出来——与 ``build_import_assets`` 的模块注释是同一条理由。
    """
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "one.jpg").write_bytes(b"one")
    importer = build_importer(migrations_ready_db, repos, tmp_path, with_spaces=True)

    result = ImportAlbum(importer=importer).execute(AlbumImportRequest(scope_id=SCOPE, directory=str(photos)))

    assert result.imported == 1
    assert repos["spaces"].get(SCOPE) is not None
    assert len(repos["faces"].list_clusters()) == 1
