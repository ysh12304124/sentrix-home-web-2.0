"""按实际 tokenizer 预算分段，偏移始终指向文档原文。"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

import httpx

from sentrix.domain.media.text_chunks import TextChunk


@lru_cache(maxsize=8)
def _tokenizer(path: str, endpoint: str) -> tuple[Tokenizer, str]:
    from tokenizers import Tokenizer

    if not path:
        with httpx.Client(timeout=10, trust_env=False) as client:
            info = client.get(endpoint.rstrip("/") + "/health").raise_for_status().json()
        path = str(Path(info["model"]) / "tokenizer.json")
    artifact = Path(path)
    tokenizer = Tokenizer.from_file(str(artifact))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer, hashlib.sha256(artifact.read_bytes()).hexdigest()


def document_chunks(
    text: str, context: str, *, tokenizer_path: str, endpoint: str, token_budget: int
) -> list[TextChunk]:
    budget = token_budget - 64  # 留出服务端文本包装及特殊 token。
    # UTF-8 字节数是 byte-BPE/WordPiece token 数的保守上界；短文无需装载 tokenizer。
    if len((context + "\n" + text).encode("utf-8")) <= budget:
        return [TextChunk(context + "\n" + text, 0, len(text), f"utf8-upper-bound-v1:{token_budget}")]
    tokenizer, digest = _tokenizer(tokenizer_path, endpoint)
    prefix_ids = tokenizer.encode(context, add_special_tokens=False).ids[: max(1, budget // 4)]
    prefix = tokenizer.decode(prefix_ids, skip_special_tokens=False) + "\n" if prefix_ids else ""
    protocol = f"tokenizer-v1:{digest}:{token_budget}"
    if len(tokenizer.encode(context + "\n" + text, add_special_tokens=False).ids) <= budget:
        return [TextChunk(context + "\n" + text, 0, len(text), protocol)]
    available = budget - len(prefix_ids) - 8
    encoded = tokenizer.encode(text, add_special_tokens=False)
    result = []
    index = 0
    while index < len(encoded.ids):
        stop = min(index + available, len(encoded.ids))
        start, end = encoded.offsets[index][0], encoded.offsets[stop - 1][1]
        while len(tokenizer.encode(prefix + text[start:end], add_special_tokens=False).ids) > budget:
            stop -= 1
            end = encoded.offsets[stop - 1][1]
        result.append(TextChunk(prefix + text[start:end], start, end, protocol))
        if stop == len(encoded.ids):
            break
        index = max(index + 1, stop - min(32, available // 8))
    return result
