"""文本向量（``semantic`` / ``episodic``）的 SQLite 仓储。

与 ``SqliteVectorRepository`` 共用 ``VectorTableRepository`` 的实现；差别只有表名。
分表的理由见 ``0015_text_vectors.sql``：文本检索只扫文本行，视频清理与将来的文本
ANN 也可以只针对这张表操作。
"""

from __future__ import annotations

from sentrix.infrastructure.persistence.repositories.vectors import VectorTableRepository


class SqliteTextVectorRepository(VectorTableRepository):
    """文本向量的权威存储（``text_vectors``）。"""

    _TABLE = "text_vectors"
