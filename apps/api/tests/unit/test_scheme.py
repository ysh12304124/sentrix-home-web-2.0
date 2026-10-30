"""嵌入方案：**整方案原子切换**，禁止两个槽各切各的。

1.0 的历史：IMAGE/TEXT 嵌入器被 env/.env 各自切换过（clip / chinese_clip / bge），
memory_vectors、ANN 索引、FTS 各套并存且互不匹配，检索时好时坏。

2026-10-20 为 16 GB Mac mini 恢复 legacy 方案时，保留的约束是：

1. 只能选一个**具名方案**；
2. 不存在「图片用 A、文本用 B」的独立开关；
3. 默认必须是 wemm（153 不设变量即行为不变）。
"""

from __future__ import annotations

import pytest

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme


def test_default_scheme_is_wemm_and_shapes_are_pinned():
    wemm = scheme.get_scheme("wemm")
    assert scheme.DEFAULT_SCHEME == "wemm"
    assert wemm.visual_model == "tencent/WeMM-Embedding-2B"
    assert wemm.text_model == "tencent/WeMM-Embedding-2B"
    assert wemm.visual_dimension == 2048
    assert wemm.text_dimension == 2048
    assert wemm.unified is True


def test_legacy_scheme_matches_1_0():
    legacy = scheme.get_scheme("legacy")
    assert legacy.visual_model == "chinese-clip-ViT-L-14"
    assert legacy.text_model == "BAAI/bge-m3"
    assert legacy.visual_dimension == 768
    assert legacy.text_dimension == 1024
    # **两个独立空间**：legacy 不是统一嵌入，visual 与 semantic/episodic 各自比。
    assert legacy.unified is False


def test_unknown_scheme_fails_loudly():
    with pytest.raises(ValueError, match="未知的嵌入方案"):
        scheme.get_scheme("clip")

    with pytest.raises(ValueError, match="SENTRIX_EMBEDDING_SCHEME"):
        Settings(embedding_scheme="clip")


def test_settings_expose_only_the_atomic_scheme_switch(tmp_path, monkeypatch):
    """允许 ``embedding_scheme``，仍然禁止按槽位/后端的零散开关。

    ``text_embedder_url`` / ``wemm_url`` / ``legacy_clip_url`` 不在禁用名单里：
    它们是**服务地址**，不是模型选择。禁的是「选哪套嵌入」，不是「那套嵌入在哪台机器上」。
    """
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SENTRIX_EMBEDDING_SCHEME", raising=False)
    forbidden = {
        "vector_backend",
        "image_embedder",
        "visual_embedder",
        "text_embedder",
        "embedding_backend",
        "ann_backend",
        "clip_model",
        "clip_checkpoint",
    }

    assert forbidden & set(Settings.model_fields) == set()
    assert "embedding_scheme" in Settings.model_fields
    # 默认值必须是 wemm——153 不设这个变量时不能悄悄换方案。
    assert Settings(data_dir=tmp_path).embedding_scheme == "wemm"
