"""Serialize existing trace/SFT contracts one question at a time."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.profiles import scoring_summary
from photobench.domain.runs import exports


def json_chunks(value: Any) -> Iterator[str]:
    stream = getattr(value, "json_fragments", None)
    if stream is not None:
        yield from stream()
    elif isinstance(value, Mapping):
        yield "{"
        for index, (key, child) in enumerate(value.items()):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=False) + ":"
            yield from json_chunks(child)
        yield "}"
    elif isinstance(value, list):
        yield "["
        for index, child in enumerate(value):
            if index:
                yield ","
            yield from json_chunks(child)
        yield "]"
    else:
        yield from json.JSONEncoder(ensure_ascii=False).iterencode(value)


def trace_json(
    state: Mapping[str, Any], *, scores: Sequence[int] | None = None, min_score: int | None = None
) -> Iterator[str]:
    scoring = scoring_summary(state)
    yield "["
    first = True
    for item in state.get("items") or []:
        for row in exports.export_traces({**state, "items": [item]}, scores=scores, min_score=min_score):
            row["scoring"] = scoring
            if not first:
                yield ","
            first = False
            yield from json_chunks(row)
    yield "]"


def sft_json(
    state: Mapping[str, Any], *, scores: Sequence[int] | None = None, min_score: int | None = None
) -> Iterator[str]:
    header = exports.export_sft({**state, "items": []}, scores=scores, min_score=min_score)
    header["scoring"] = scoring_summary(state)
    header = {k: v for k, v in header.items() if k not in {"items", "samples", "count", "item_count"}}
    yield "{"
    for index, (key, value) in enumerate(header.items()):
        if index:
            yield ","
        yield from json_chunks(key)
        yield ":"
        yield from json_chunks(value)
    yield ',"items":['
    item_count = 0
    for item in state.get("items") or []:
        payload = exports.export_sft({**state, "items": [item]}, scores=scores, min_score=min_score)
        for row in payload["items"]:
            if item_count:
                yield ","
            item_count += 1
            yield from json_chunks(row)
    yield '],"samples":['
    count = 0
    for item in state.get("items") or []:
        payload = exports.export_sft({**state, "items": [item]}, scores=scores, min_score=min_score)
        for sample in payload["samples"]:
            if count:
                yield ","
            count += 1
            yield from json_chunks(sample)
    yield '],"count":' + str(count) + ',"item_count":' + str(item_count) + "}"


def buffered_chunks(chunks: Iterator[str], size: int = 65536) -> Iterator[str]:
    """Batch small JSON tokens before crossing the HTTP thread-pool boundary."""
    pending: list[str] = []
    length = 0
    for chunk in chunks:
        pending.append(chunk)
        length += len(chunk)
        if length >= size:
            yield "".join(pending)
            pending.clear()
            length = 0
    if pending:
        yield "".join(pending)
