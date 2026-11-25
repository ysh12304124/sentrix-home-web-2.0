"""Explicit legacy indexing: stream JSON into immutable objects before building views.

Run with ``python -m photobench.infrastructure.results.index_legacy --help``.
The source is retained; HTTP readers never launch this conversion themselves.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, TextIO

from photobench.infrastructure.results.split_json import ObjectReader, ObjectWriter, database, manifest, thaw
from photobench.infrastructure.results.split_store import SplitResultWriter
from photobench.infrastructure.results.store import committed_path


class JsonObjectStream:
    """Keep Python JSON scalar types while writing each completed container by reference."""

    def __init__(self, source: TextIO, objects: ObjectWriter):
        self.source, self.objects = source, objects
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def fill(self) -> bool:
        if self.eof:
            return False
        self.buffer = self.buffer[self.position :]
        self.position = 0
        chunk = self.source.read(65536)
        self.eof = not chunk
        self.buffer += chunk
        return bool(chunk)

    def peek(self) -> str:
        while True:
            while self.position < len(self.buffer):
                char = self.buffer[self.position]
                if char not in " \r\n\t":
                    return char
                self.position += 1
            if not self.fill():
                return ""

    def take(self, expected: str) -> None:
        if self.peek() != expected:
            raise ValueError("invalid JSON container boundary")
        self.position += 1

    def scalar(self) -> Any:
        first = self.peek()
        if first == '"':
            while True:
                try:
                    value, end = self.decoder.raw_decode(self.buffer, self.position)
                    self.position = end
                    return value
                except json.JSONDecodeError:
                    if not self.fill():
                        raise
        while True:
            tail = self.buffer[self.position :]
            end = next((i for i, char in enumerate(tail) if char in " \r\n\t,]}"), None)
            if end is not None or self.eof:
                length = end if end is not None else len(tail)
                value = json.loads(tail[:length])
                self.position += length
                return value
            self.fill()

    def value(self) -> Any:
        first = self.peek()
        if first == "{":
            self.take("{")
            mapping = {}
            if self.peek() != "}":
                while True:
                    key = self.scalar()
                    if not isinstance(key, str):
                        raise ValueError("JSON object key must be a string")
                    self.take(":")
                    mapping[key] = self.value()
                    if self.peek() != ",":
                        break
                    self.take(",")
            self.take("}")
            return self.objects.put(mapping)
        if first == "[":
            self.take("[")
            values = []
            if self.peek() != "]":
                while True:
                    values.append(self.value())
                    if self.peek() != ",":
                        break
                    self.take(",")
            self.take("]")
            return self.objects.put(values)
        return self.objects.put(self.scalar())


def ingest(source: Path, objects: ObjectWriter) -> Any:
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as handle:
        stream = JsonObjectStream(handle, objects)
        root = stream.value()
        if stream.peek():
            raise ValueError("extra data after legacy JSON root")
        return root


def build_index(root: Path, run_id: str, source: Path | None = None) -> dict[str, Any]:
    directory = root / run_id
    if manifest(directory) is not None:
        raise ValueError("read index already exists")
    source = source or committed_path(directory, "run.json")
    before = source.stat()
    with source.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    objects = ObjectWriter(root)
    try:
        ref = ingest(source, objects)
        objects.db.commit()
    finally:
        objects.close()
    state = thaw(ObjectReader(database(root)).value(ref))
    if state["run_id"] != run_id:
        raise ValueError("source run identity mismatch")
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("source checkpoint changed while indexing")
    return SplitResultWriter(root).write(directory, state, source={"path": str(source), "sha256": digest})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        parser.error("invalid run id")
    print(json.dumps(build_index(args.results_root, args.run_id, args.source)))
