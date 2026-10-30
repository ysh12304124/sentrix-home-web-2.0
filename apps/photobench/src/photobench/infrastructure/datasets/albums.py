"""``AlbumSource`` 的文件实现：``<data_root>/<album_id>/`` 下的 manifest / qa / 参考图。

1.0 的对应物散在 ``benchmark_orchestrator.py`` 的三处：
``BenchmarkRun.__init__``（``:1966-1969`` 读 manifest 与 qa 行）、``load_jsonl``
（``:1600``）、``dataset_integrity``（``:1655``）。2.0 把后两个连
``apply_task_action_defaults``（``:1629``）一起收进这一个类。

**一个刻意的差别：文件缺了抛 ``KeyError``，不抛 ``FileNotFoundError``。**
1.0 那边缺文件是「跑到一半炸」，2.0 的 api 层把 ``KeyError`` 映射成 404
（``photobench.api.errors``），所以「这个 album 不存在」「这个 qa_set 不存在」
「这张参考图没了」在 HTTP 上是同一类回答——**它们对调用方是同一件事**：
这次请求指的东西不在盘上。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from photobench.domain.datasets.catalog import ALBUM_MEDIA_COLLECTIONS
from photobench.domain.datasets.qa_taxonomy import validate_qa_annotations
from photobench.infrastructure.datasets.qa_metadata import (
    apply_task_action_defaults,
    load_jsonl,
    load_task_action_policy,
)

#: 2.0 ``application/media/import_album.py`` 的 ``SCAN_SUFFIXES``（图片 ∪ 视频 ∪ 音频）
#: 逐条对齐。**这份清单会漂**——它是 2.0 端点行为的镜像，改了 2.0 就得改这里；
#: 之所以复制而不是 import，是因为 ``photobench-isolation`` 契约禁止读 ``sentrix.*``。
MEDIA_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".webp",
        ".mp4",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
    }
)


def sha256_file(path: Path) -> str:
    """1.0 ``:1646``：分块哈希，不把文件读进内存（参考图之外还有几十 MB 的视频）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class JsonAlbumSource:
    """数据根下每个 album 一个目录，目录里是 ``manifest.json`` 与它指向的 qa ``.jsonl``。"""

    def __init__(self, data_root: Path, task_action_policy: Path) -> None:
        self._data_root = data_root
        self._policy_path = task_action_policy
        # 策略文件一次读一次即可（1.0 在模块导入时读进 TASK_ACTION_DEFAULTS ``:1634``）。
        # 与 1.0 的差别是**读失败不炸**（``load_task_action_policy`` 里那条 except），
        # 1.0 同样如此。
        self._policy = load_task_action_policy(task_action_policy)

    def _album_dir(self, album_id: str) -> Path:
        """挡住 ``album_id`` 里的路径穿越。

        ``album_id`` 来自请求体（1.0 的 ``POST /api/runs``），与 ``FileRunStore``
        拦 ``run_id`` 是同一条理由：不加这一层，``../../etc`` 就能让``read_file``
        读到数据根之外的东西。
        """
        if not album_id or "/" in album_id or "\\" in album_id or album_id in {".", ".."}:
            raise KeyError(album_id)
        return self._data_root / album_id

    def list_album_ids(self) -> list[str]:
        """1.0 ``:5146-5147``：数据根下的条目名，**按名字排序**。

        1.0 遍历的是 ``iterdir()`` 的**全部**条目（文件也进循环，只是 ``d /
        "manifest.json"`` 不存在就跳过）。这里只留目录——数据根里那个 ``.gitkeep``
        进不进循环对结果没有差别，而「只列目录」让这个方法的返回值与它的名字一致。
        数据根不存在时回空表（1.0 的 ``if BENCHMARK_DATA_ROOT.exists()``）。
        """
        try:
            entries = sorted(self._data_root.iterdir())
        except OSError:
            return []
        return [entry.name for entry in entries if entry.is_dir()]

    def media_file(self, album_id: str, collection: str, file_name: str) -> str | None:
        """1.0 ``_resolve_album_media_file``（``:1188``），**三段都拦穿越**。

        1.0 只比对 ``file_name`` 那一段（``media_path.is_relative_to(media_root)``），
        而 ``album_id`` 是与它拼在一起的——``album_id="../../etc"`` 会让 ``media_root``
        自己挪到根之外，比对随之失效。这里 ``album_id`` 先过 ``_album_dir``（挡住
        ``/``、``\\``、``..``），``collection`` 白名单，``file_name`` 才做相对性比对。

        ``collection`` 的白名单不能挪到路由去：只要这个方法能被直接调用，它就是
        唯一知道「media 根在哪」的地方。两份判据里少一份，``collection="../../etc"``
        就能读到 ``../../etc/passwd``。
        """
        if collection not in ALBUM_MEDIA_COLLECTIONS:
            return None
        try:
            album_dir = self._album_dir(album_id)
        except KeyError:
            return None
        media_root = (album_dir / collection).resolve()
        media_path = (media_root / file_name).resolve()
        if not media_path.is_relative_to(media_root) or not media_path.is_file():
            return None
        return str(media_path)

    def manifest(self, album_id: str) -> dict[str, Any]:
        path = self._album_dir(album_id) / "manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(album_id) from exc
        if not isinstance(value, dict):
            raise KeyError(album_id)
        return value

    def qa_rows(self, album_id: str, qa_set: str) -> list[dict[str, Any]]:
        manifest = self.manifest(album_id)
        relative = (manifest.get("qa_sets") or {}).get(qa_set)
        if not relative:
            raise KeyError(f"{album_id}/{qa_set}")
        path = self._album_dir(album_id) / str(relative)
        try:
            rows = load_jsonl(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(f"{album_id}/{qa_set}") from exc
        definition = (manifest.get("qa_set_versions") or {}).get(qa_set) or {}
        if definition.get("schema_version") in {"2.0", "3.0"}:
            ids = [row.get("qa_id") for row in rows]
            if not all(ids) or len(set(ids)) != len(rows):
                raise ValueError("QA IDs must be present and unique")
            if definition.get("qa_sha256") != sha256_file(path):
                raise ValueError("versioned QA file fingerprint mismatch")
            for row in rows:
                if definition.get("schema_version") == "3.0" and (
                    not row.get("ability_taxonomy_version")
                    or row.get("ability_taxonomy_version") != definition.get("ability_taxonomy_version")
                ):
                    raise ValueError("ability version missing or inconsistent with manifest")
                validate_qa_annotations(row)
        rows = apply_task_action_defaults(
            rows, str(manifest.get("album_id") or album_id), qa_set, self._policy
        )
        for row in rows:
            for source, target in (
                ("dataset_version", "dataset_version"),
                ("schema_version", "qa_schema_version"),
                ("taxonomy_version", "taxonomy_version"),
            ):
                if definition.get(source):
                    row[target] = definition[source]
        return rows

    def read_file(self, album_id: str, relative: str) -> bytes:
        path = self._album_dir(album_id) / relative
        try:
            return path.read_bytes()
        except OSError as exc:
            raise KeyError(relative) from exc

    def import_target(self, album_id: str, relative: str) -> tuple[str, int]:
        directory = (self._album_dir(album_id) / relative).resolve()
        try:
            count = sum(
                1 for path in directory.iterdir() if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
            )
        except OSError:
            count = 0
        return str(directory), count

    def integrity(self, album_id: str, manifest: dict[str, Any], qa_set: str) -> dict[str, Any]:
        """1.0 ``:1655`` 逐行照搬，只把数据根的来源换成这个类的。

        ``task_action_policy_sha256`` 在文件缺失时给 ``None`` 而不是报错——1.0 就是
        这么写的（那里用 ``is_file()`` 兜了一下），因为策略文件是**可选**的：
        没有它时题目按自己的 ``expected_action`` 走，那也是一份合法的输入。
        """
        album_dir = self._album_dir(album_id)
        relative_qa = str((manifest.get("qa_sets") or {})[qa_set]) if qa_set else ""
        entries = ["manifest.json"] + ([relative_qa] if relative_qa else [])
        entries.extend(
            str(item).strip()
            for key in ("photos", "videos")
            for item in (manifest.get(key) or [])
            if str(item).strip()
        )
        entries.extend(
            str(face.get("ref_image")) for face in manifest.get("faces") or [] if face.get("ref_image")
        )
        missing: list[str] = []
        file_digests: list[dict[str, Any]] = []
        for relative in sorted(set(entries)):
            path = album_dir / relative
            if not path.is_file():
                missing.append(relative)
                continue
            file_digests.append({"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size})
        combined = hashlib.sha256(
            "\n".join(f"{entry['path']}:{entry['sha256']}" for entry in file_digests).encode()
        ).hexdigest()
        return {
            "algorithm": "sha256",
            "manifest_sha256": sha256_file(album_dir / "manifest.json"),
            "qa_sha256": sha256_file(album_dir / relative_qa) if relative_qa else None,
            "task_action_policy_sha256": sha256_file(self._policy_path)
            if self._policy_path.is_file()
            else None,
            "dataset_sha256": combined,
            "files_checked": len(file_digests),
            "missing_files": missing,
        }
