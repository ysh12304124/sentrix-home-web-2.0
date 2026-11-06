"""``JsonAlbumSource`` 的用例：主链的输入侧。

三件事值得钉：**路径穿越**（``album_id`` 来自请求体）、**``import_target`` 的文件数**
（它是 2.0 ``/imports`` 的 ``limit``，填小了会静默少导）、以及**完整性摘要的
``missing_files``**（那份数据是「这次 run 用了哪些字节」的唯一凭证）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photobench.infrastructure.datasets.albums import JsonAlbumSource

MANIFEST = {
    "album_id": "album-x",
    "qa_sets": {"qa-1": "qa/one.jsonl"},
    "photos": ["photos/a.jpg", "photos/b.jpg"],
    "faces": [{"canonical_name": "明明", "ref_image": "faces/1.jpg"}],
}
POLICY = {"defaults": [{"album_id": "album-x", "qa_set": "qa-1", "expected_action": "answer"}]}


def _source(tmp_path: Path, *, policy: dict | None = None) -> JsonAlbumSource:
    album = tmp_path / "album-x"
    (album / "photos").mkdir(parents=True)
    (album / "faces").mkdir(parents=True)
    (album / "qa").mkdir(parents=True)
    (album / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (album / "photos" / "a.jpg").write_bytes(b"A")
    (album / "photos" / "b.jpg").write_bytes(b"B")
    # album 目录里混一个非媒体文件：``import_target`` 必须**不数它**
    (album / "photos" / "notes.txt").write_text("x", encoding="utf-8")
    (album / "faces" / "1.jpg").write_bytes(b"F")
    (album / "qa" / "one.jsonl").write_text(
        json.dumps({"qa_id": "q1", "question": "谁?", "answer": "明明"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "qa_task_actions.json"
    policy_path.write_text(json.dumps(POLICY if policy is None else policy), encoding="utf-8")
    return JsonAlbumSource(tmp_path, policy_path)


def test_qa_rows_get_the_task_action_default_applied(tmp_path: Path):
    """QA 集级的 ``expected_action`` 默认值在这里补（1.0 ``:1629``）。

    没有它，``behavior-6q`` 那种「只有可答题」的题库会让全部题目都缺
    ``expected_action``，而 task-action 那一维的分数就是空的。
    """
    rows = _source(tmp_path).qa_rows("album-x", "qa-1")
    assert rows == [{"qa_id": "q1", "question": "谁?", "answer": "明明", "expected_action": "answer"}]


def test_import_target_counts_media_files_only(tmp_path: Path):
    """``limit`` 数的是**会被导入的后缀**，不是「目录里的文件数」。

    夹具在 ``photos/`` 里放了一个 ``notes.txt``：一个按全部文件数给的实现会返回 3，
    而正确答案是 2。这条差异在真机上表现为「limit 比实际多一点」——**无害**，
    但那说明它不是「这个目录会被导入几张」，而这条数据的唯一用途就是那个。
    """
    path, count = _source(tmp_path).import_target("album-x", "photos")
    assert (path, count) == (str(tmp_path / "album-x" / "photos"), 2)


def test_import_target_reports_zero_for_a_directory_that_does_not_exist(tmp_path: Path):
    """数不出来返回 0，由调用方决定怎么办——**不在这里猜一个「够大」的数**。"""
    source = _source(tmp_path)
    _path, count = source.import_target("album-x", "videos")
    assert count == 0


def test_an_album_id_with_a_path_separator_is_not_found(tmp_path: Path):
    """``album_id`` 来自请求体，``../`` 能让 ``read_file`` 读到数据根之外。

    **这条断言必须是「抛 KeyError 且没有读到东西」**：只断言抛异常的话，
    一个不拦但恰好文件不存在的实现同样会抛——本仓库踩过这个坑（S9b-1 的路径穿越）。
    所以夹具真的在数据根的**兄弟目录**放一份 ``secret.txt``：
    只有拦住的实现才读不到它。
    """
    source = _source(tmp_path)
    (tmp_path.parent / "album-x").mkdir(exist_ok=True)
    (tmp_path.parent / "album-x" / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    with pytest.raises(KeyError):
        source.manifest("../album-x")
    with pytest.raises(KeyError):
        source.read_file("../album-x", "secret.txt")


def test_a_missing_qa_set_or_face_file_is_a_key_error_not_a_crash(tmp_path: Path):
    """三种「不在盘上」在 HTTP 上是同一类回答（404），所以三种都抛 ``KeyError``。"""
    source = _source(tmp_path)
    with pytest.raises(KeyError):
        source.qa_rows("album-x", "qa-不存在")
    with pytest.raises(KeyError):
        source.read_file("album-x", "faces/缺失.jpg")
    with pytest.raises(KeyError):
        source.manifest("album-不存在")


def test_integrity_hashes_every_input_and_lists_what_is_missing(tmp_path: Path):
    """ "两次 run 的指标不一样" 要能区分成「模型变了」还是「数据集被改过」。

    夹具刻意少一张 ``b.jpg``：那条必须出现在 ``missing_files`` 里，否则「缺文件」
    与「文件在但内容一样」在摘要里没有区别。
    """
    source = _source(tmp_path)
    (tmp_path / "album-x" / "photos" / "b.jpg").unlink()
    integrity = source.integrity("album-x", MANIFEST, "qa-1")
    assert integrity["algorithm"] == "sha256"
    assert integrity["missing_files"] == ["photos/b.jpg"]
    assert integrity["files_checked"] == 4  # manifest + qa + a.jpg + faces/1.jpg
    assert len(str(integrity["dataset_sha256"])) == 64
    assert integrity["task_action_policy_sha256"] is not None


def test_a_changed_byte_changes_the_dataset_digest(tmp_path: Path):
    """摘要的全部用途就是「内容变了它要变」。**不比对两次摘要，这条断言是空的。**"""
    source = _source(tmp_path)
    before = source.integrity("album-x", MANIFEST, "qa-1")["dataset_sha256"]
    (tmp_path / "album-x" / "photos" / "a.jpg").write_bytes(b"A2")
    assert source.integrity("album-x", MANIFEST, "qa-1")["dataset_sha256"] != before


def test_a_broken_policy_file_does_not_take_the_dataset_down(tmp_path: Path):
    """策略文件是**可选**输入（1.0 ``:1613`` 那条 except）：坏了就当没有默认值，
    而不是让整轮评测起不来。"""
    source = _source(tmp_path)
    (tmp_path / "qa_task_actions.json").write_text("{ 这不是 JSON", encoding="utf-8")
    broken = JsonAlbumSource(tmp_path, tmp_path / "qa_task_actions.json")
    assert broken.qa_rows("album-x", "qa-1") == [{"qa_id": "q1", "question": "谁?", "answer": "明明"}]
    assert source.qa_rows("album-x", "qa-1")[0]["expected_action"] == "answer"


# ---- S10a：展示层加的两个方法（列表与媒体交付） -----------------------------


def test_list_album_ids_is_sorted_and_directories_only(tmp_path: Path):
    """``GET /api/manifests`` 靠它遍历；1.0 那边是 ``sorted(iterdir())``。

    夹具混进一个**文件**（``README.txt``）与两个目录，名字顺序刻意不是排序后的顺序
    ——只列目录且按名字排序的实现才会给出 ``["album-x", "zzz"]``。
    """
    source = _source(tmp_path)
    (tmp_path / "zzz").mkdir()
    (tmp_path / "README.txt").write_text("x", encoding="utf-8")
    assert source.list_album_ids() == ["album-x", "zzz"]


def test_media_file_returns_the_path_inside_the_collection(tmp_path: Path):
    source = _source(tmp_path)
    assert source.media_file("album-x", "photos", "a.jpg") == str(tmp_path / "album-x" / "photos" / "a.jpg")


def test_media_file_serves_only_manifest_declared_external_links(tmp_path: Path):
    source = _source(tmp_path)
    original = tmp_path / "original.jpg"
    original.write_bytes(b"original media")
    photo = tmp_path / "album-x/photos/a.jpg"
    photo.unlink()
    photo.symlink_to(original)
    (photo.parent / "undeclared.jpg").symlink_to(original)
    assert source.media_file("album-x", "photos", "a.jpg") == str(original)
    assert source.media_file("album-x", "photos", "undeclared.jpg") is None


def test_media_file_blocks_every_traversable_segment(tmp_path: Path):
    """三段都来自 URL，所以三段都要拦。

    夹具在**数据根之外**放一份真文件：不拦的实现会把它交出去，只判「文件在不在」的
    实现同样会交出去——所以断言必须落在「返回了 None」，而不是「抛了什么」。
    """
    source = _source(tmp_path)
    (tmp_path.parent / "secret.txt").write_text("TOP SECRET", encoding="utf-8")

    assert source.media_file("..", "photos", "a.jpg") is None
    assert source.media_file("album-x/..", "photos", "a.jpg") is None
    assert source.media_file("album-x", "..", "secret.txt") is None
    assert source.media_file("album-x", "photos", "../a.jpg") is None
    assert source.media_file("album-x", "photos", "../../secret.txt") is None
    assert source.media_file("album-x", "photos", "/etc/passwd") is None
    # 白名单之外的一级目录名也要拦住（1.0 在路由里判，2.0 两处都判）。
    assert source.media_file("album-x", "not-a-collection", "a.jpg") is None


def test_build_integrity_without_qa_still_hashes_media_and_face_seeds(tmp_path):
    source = _source(tmp_path)
    manifest = dict(MANIFEST, qa_sets={})
    integrity = source.integrity("album-x", manifest, "")
    assert integrity["qa_sha256"] is None
    assert integrity["files_checked"] == 4
    assert integrity["missing_files"] == []
