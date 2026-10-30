"""数据集侧的只读端口：QA 元数据。

**为什么读侧需要它。** 1.0 的 ``get_run_items``（``:6441``）把每个 item 先过一遍
``_hydrate_qa_metadata``（``:4919``），用 **数据集里** 的 ``qa_id`` 元信息补齐历史 run
当时没存下的字段（``task_type`` / ``difficulty`` / ``answerability`` / ``tags`` …）。
那份元信息来自 ``data/*/manifest.json`` 指向的 qa ``.jsonl``，加上
``config/qa_task_actions.json`` 的 QA 集级默认值（``apply_task_action_defaults`` ``:1629``）。

所以「列出某个 run 的题目」这件事依赖数据集目录，**不是**纯结果读取。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QaMetadataSource(Protocol):
    """``qa_id`` → 数据集侧元信息的只读映射。"""

    def metadata_by_qa_id(self) -> dict[str, dict[str, Any]]:
        """返回 ``{album_id\0qa_set\0qa_sha256\0qa_id: {字段: 值}}``。

        不允许仅按 QA ID 从不同集合或版本补齐历史标签。

        1.0 在 ``OrchestratorRepository.__init__`` 里建这张表（``:4885``），一次扫描、
        进程内常驻；2.0 保持「进程内建一次」的语义，但由 application 在需要时惰性触发
        （见 ``application.runs.read_runs.ReadRuns.qa_metadata``），因为不使用题库元信息的
        列表页不该为它付一次全库扫描。
        """
        ...


@runtime_checkable
class AlbumSource(Protocol):
    """一个 benchmark album 的**输入侧**：manifest、题目、参考图字节、完整性摘要。

    1.0 是 ``BenchmarkRun.__init__`` 里几行直读（``:1966-1969``）：
    ``BENCHMARK_DATA_ROOT / album_id``、``manifest["qa_sets"][qa_set]``、
    ``album_dir / face["ref_image"]``。2.0 收成一个端口，理由有两条：

    1. **application 不许碰文件系统之外的 IO 形状**——主链拿到的是「这个 album 的
       manifest / 题目 / 某个相对路径的字节」，不是「一个 Path 再加一堆拼接」。
    2. **``PHOTOBENCH_DATA_ROOT`` 已经是可覆盖的**（``photobench.config``），而 2.0 的
       整册导入要把**服务端绝对路径**交给主 API（``POST /api/v1/imports`` 的
       ``directory``）。两个地址（本地读 vs 交给服务端）必须来自同一处，
       否则「导的是哪个目录」与「算完整性的是哪个目录」会悄悄分家。
    """

    def manifest(self, album_id: str) -> dict[str, Any]:
        """``<data_root>/<album_id>/manifest.json``。读不到抛 ``KeyError``（→ 404）。"""
        ...

    def qa_rows(self, album_id: str, qa_set: str) -> list[dict[str, Any]]:
        """该 QA 集的题目行，**已叠加 QA 集级 ``expected_action`` 默认值**。

        ``apply_task_action_defaults``（1.0 ``:1629``）在这一步做，与 1.0 的
        ``BenchmarkRun.__init__`` 同一位置：默认值属于「这批题怎么答」，
        不属于「这道题写的是什么」。
        """
        ...

    def read_file(self, album_id: str, relative: str) -> bytes:
        """读 album 里的一个文件（参考图）。读不到抛 ``KeyError``。"""
        ...

    def import_target(self, album_id: str, relative: str) -> tuple[str, int]:
        """把一个媒体目录变成 ``(服务端绝对路径, 目录里的媒体文件数)``。

        路径不是给本地读的——它是交给 ``POST /api/v1/imports`` 的 ``directory``。
        由这份实现算出来，才能保证它与 ``read_file`` 看的是同一个根。

        文件数是同一个端点的 ``limit``，**必须数实**：2.0 的 ``ImportAlbum.execute``
        取 ``paths[:limit]``（``import_album.py:103``），limit 给小了会**静默**少导几张
        ——没有报错、没有失败条目，只是库里少了几行。数不出来（目录不在）时返回 0，
        由调用方决定怎么办，不在这里猜一个「看起来够大」的数。

        数的是**媒体后缀**那一组，与 2.0 的 ``SCAN_SUFFIXES``（图片 ∪ 视频 ∪ 音频）
        对齐，不是「目录里所有文件」：album 目录里混着 ``manifest.json`` 之类的东西，
        按全部文件数当 limit 会多给，虽然多给无害，但它就不是「这个目录会被导入几张」了。
        """
        ...

    def integrity(self, album_id: str, manifest: dict[str, Any], qa_set: str) -> dict[str, Any]:
        """1.0 ``dataset_integrity``（``:1655``）：这一轮到底用了哪些字节。

        它把 manifest / qa / 每张媒体 / 每张参考图都哈希一遍并合出一个
        ``dataset_sha256``。有了它，「两次 run 的指标不一样」才能区分成
        「模型变了」还是「数据集被改过」。
        """
        ...

    def list_album_ids(self) -> list[str]:
        """数据根下每个 album 的**目录名**，按名字排序（1.0 ``:5147`` 的 ``sorted``）。

        ``GET /api/manifests`` 靠它逐目录读 manifest。返回名字而不是摘要，是因为那份
        摘要的形状（缺 ``album_id`` 就跳过、计数怎么取）是展示层的规则，归
        ``domain.datasets.catalog``；让每个适配器各写一遍，两份就会各偏一点。
        """
        ...

    def media_file(self, album_id: str, collection: str, file_name: str) -> str | None:
        """``<data_root>/<album_id>/<collection>/<file_name>`` 的**绝对路径**，交付不了给 ``None``。

        ``collection`` 只认 ``photos`` / ``faces`` / ``videos``。返回路径而不是字节，
        是因为 HTTP 层的 ``FileResponse`` 要按 Range 分段读同一个文件——先读成 bytes
        会把一整个视频吞进内存。

        **穿越拦截在这一层**（与 ``_album_dir`` 同一条口径）：``album_id``、``collection``、
        ``file_name`` 三段都来自 URL，解析之后的路径必须仍在 media 根之内。1.0 只比对了
        ``file_name`` 那一段（``:1190``），``album_id`` 上的 ``../`` 它能穿出去。
        """
        ...
