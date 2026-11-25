"""Immutable JSON objects shared by checkpoints, with bounded lazy traversal.

SQLite supplies the local object index and atomic transactions, not a new service.
Only the small per-run manifest is replaced on publication. Completed trace
objects retain their identity across state copies and are encoded once.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast

from photobench.domain.runs.frozen_trace import TRACE_TYPES, FrozenTraceDict, FrozenTraceList

FORMAT = 3
MANIFEST = "split-commit.json"
CHUNK_CHARS = 32 * 1024
_MARKER = "$photobench_object_v3"


class ObjectRef(dict[str, Any]):
    """Internal descriptors cannot be confused with user JSON having the same key."""


def reference(kind: str, digest: str) -> ObjectRef:
    return ObjectRef({_MARKER: [kind, digest]})


def is_reference(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and _MARKER in value


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def database(root: Path) -> Path:
    return root / ".split-results" / "objects.sqlite3"


def manifest(run_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((run_dir / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if value.get("version") != FORMAT:
        raise ValueError("unsupported split checkpoint format")
    return cast(dict[str, Any], value)


def publish(run_dir: Path, value: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    temporary = run_dir / (".split-commit-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, run_dir / MANIFEST)
        descriptor = os.open(run_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class ObjectReader:
    def __init__(
        self,
        path: Path,
        *,
        cache_bytes: int = 8 * 1024 * 1024,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.path = path.resolve()
        self._connection = connection
        self._local = threading.local()
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._bytes = 0
        self._limit = cache_bytes
        self._lock = threading.RLock()

    def _db(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if not hasattr(self._local, "db"):
            self._local.db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
            self._local.db.execute("PRAGMA query_only=ON")
        return cast(sqlite3.Connection, self._local.db)

    def close(self) -> None:
        if hasattr(self._local, "db"):
            self._local.db.close()
            del self._local.db

    def raw(self, ref: Mapping[str, Any]) -> bytes:
        kind, digest = ref[_MARKER]
        row = (
            self._db()
            .execute("SELECT kind, codec, payload FROM objects WHERE digest=?", (digest,))
            .fetchone()
        )
        if row is None or row[0] != kind:
            raise ValueError(f"missing or invalid checkpoint object: {digest}")
        return zlib.decompress(row[2]) if row[1] else row[2]

    def metadata(self, ref: Mapping[str, Any]) -> Any:
        digest = ref[_MARKER][1]
        with self._lock:
            cached = self._cache.get(digest)
            if cached is not None:
                self._cache.move_to_end(digest)
                return cached[0]
        raw = self.raw(ref)
        value = json.loads(raw)
        if len(raw) <= self._limit:
            with self._lock:
                previous = self._cache.pop(digest, None)
                if previous:
                    self._bytes -= previous[1]
                self._cache[digest] = value, len(raw)
                self._bytes += len(raw)
                while self._bytes > self._limit:
                    _, (_, size) = self._cache.popitem(last=False)
                    self._bytes -= size
        return value

    def value(self, value: Any) -> Any:
        if isinstance(value, (StoredDict, StoredList)):
            return value
        if not is_reference(value):
            return value
        kind = value[_MARKER][0]
        if kind == "d":
            return StoredDict(self, value)
        if kind == "l":
            return StoredList(self, value)
        if kind == "s":
            return "".join(self.raw(child).decode("utf-8") for child in self.metadata(value))
        raise ValueError(f"unexpected checkpoint object kind: {kind}")

    def descriptor(self, ref: Mapping[str, Any], key: str | int) -> Any:
        data = self.metadata(ref)
        return data[key]

    def walk(self, root: Any, path: list[str | int]) -> Any:
        value = root.ref if isinstance(root, (StoredDict, StoredList)) else root
        for key in path:
            if not is_reference(value):
                raise KeyError(str(key))
            value = self.descriptor(value, key)
        return value

    def iter_json(self, value: Any) -> Iterator[str]:
        """Serialize without constructing a full question or full-run JSON string."""
        if isinstance(value, (StoredDict, StoredList)):
            value = value.ref
        if not is_reference(value):
            yield json.dumps(value, ensure_ascii=False, allow_nan=False)
            return
        kind = value[_MARKER][0]
        data = self.metadata(value)
        if kind == "s":
            yield '"'
            for child in data:
                # Each chunk is a complete Unicode string; escape without quotes.
                yield json.dumps(self.raw(child).decode("utf-8"), ensure_ascii=False)[1:-1]
            yield '"'
        elif kind == "d":
            yield "{"
            for index, (key, child) in enumerate(data.items()):
                if index:
                    yield ","
                yield json.dumps(key, ensure_ascii=False) + ":"
                yield from self.iter_json(child)
            yield "}"
        elif kind == "l":
            yield "["
            for index, child in enumerate(data):
                if index:
                    yield ","
                yield from self.iter_json(child)
            yield "]"
        else:
            raise ValueError("invalid JSON object")

    def iter_canonical_json(self, value: Any) -> Iterator[str]:
        """Stream ``json.dumps(sort_keys=True, default=str)`` without joining a large trace."""
        if isinstance(value, (StoredDict, StoredList)):
            value = value.ref
        if isinstance(value, dict) and not is_reference(value):
            yield "{"
            for index, key in enumerate(sorted(value)):
                if index:
                    yield ", "
                yield json.dumps(key, ensure_ascii=False) + ": "
                yield from self.iter_canonical_json(value[key])
            yield "}"
            return
        if isinstance(value, list):
            yield "["
            for index, child in enumerate(value):
                if index:
                    yield ", "
                yield from self.iter_canonical_json(child)
            yield "]"
            return
        if not is_reference(value):
            yield json.dumps(value, ensure_ascii=False, default=str)
            return
        kind = value[_MARKER][0]
        data = self.metadata(value)
        if kind == "s":
            yield '"'
            for child in data:
                yield json.dumps(self.raw(child).decode("utf-8"), ensure_ascii=False)[1:-1]
            yield '"'
        elif kind == "d":
            yield "{"
            for index, key in enumerate(sorted(data)):
                if index:
                    yield ", "
                yield json.dumps(key, ensure_ascii=False) + ": "
                yield from self.iter_canonical_json(data[key])
            yield "}"
        elif kind == "l":
            yield "["
            for index, child in enumerate(data):
                if index:
                    yield ", "
                yield from self.iter_canonical_json(child)
            yield "]"
        else:
            raise ValueError("invalid JSON object")

    def fingerprint(self, value: Any) -> str:
        digest = hashlib.sha256()
        for fragment in self.iter_canonical_json(value):
            digest.update(fragment.encode("utf-8"))
        return digest.hexdigest()

    def content(self, value: Any, offset: int, limit: int) -> dict[str, Any]:
        """Bounded Unicode chunks; offsets are characters in the serialized JSON."""
        offset = max(0, offset)
        limit = max(1, min(limit, 64 * 1024))
        position = 0
        chunks: list[str] = []
        length = 0
        more = False
        fragments = iter(self.iter_json(value))
        for fragment in fragments:
            end = position + len(fragment)
            if end > offset:
                piece = fragment[max(0, offset - position) :]
                available = limit - length
                chunks.append(piece[:available])
                length += min(available, len(piece))
                if length == limit:
                    more = len(piece) > available or next(fragments, None) is not None
                    break
            position = end
        text = "".join(chunks)
        if len(json_bytes(text)) > 240 * 1024:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if len(json_bytes(text[:middle])) <= 240 * 1024:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low]
            length, more = low, True
        return {
            "text": text,
            "offset": offset,
            "next_offset": offset + length if more else None,
            "encoding": "json_unicode_characters",
        }


class StoredDict(FrozenTraceDict):
    __slots__ = ("reader", "ref")

    def __init__(self, reader: ObjectReader, ref: dict[str, Any]) -> None:
        # A nonempty backing table makes Python's JSON encoder call items().
        dict.__init__(self, {"__lazy__": None})
        self.reader, self.ref = reader, ref

    def __len__(self) -> int:
        return len(self.reader.metadata(self.ref))

    def __iter__(self) -> Iterator[str]:
        return iter(self.reader.metadata(self.ref))

    def __contains__(self, key: object) -> bool:
        return key in self.reader.metadata(self.ref)

    def __getitem__(self, key: str) -> Any:
        return self.reader.value(self.reader.descriptor(self.ref, key))

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> Any:
        return self.reader.metadata(self.ref).keys()

    def items(self) -> Any:
        return ((key, self[key]) for key in self)

    def values(self) -> Any:
        return (self[key] for key in self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, dict) and dict(self.items()) == other

    def json_fragments(self) -> Iterator[str]:
        yield from self.reader.iter_json(self.ref)

    def __repr__(self) -> str:
        return f"StoredDict({self.ref[_MARKER][1]})"


class StoredList(FrozenTraceList):
    __slots__ = ("reader", "ref")

    def __init__(self, reader: ObjectReader, ref: dict[str, Any]) -> None:
        list.__init__(self, [None])
        self.reader, self.ref = reader, ref

    def __len__(self) -> int:
        return len(self.reader.metadata(self.ref))

    def __iter__(self) -> Iterator[Any]:
        return (self.reader.value(child) for child in self.reader.metadata(self.ref))

    def __getitem__(self, key: Any) -> Any:
        data = self.reader.metadata(self.ref)[key]
        return [self.reader.value(v) for v in data] if isinstance(key, slice) else self.reader.value(data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, list) and list(self) == other

    def json_fragments(self) -> Iterator[str]:
        yield from self.reader.iter_json(self.ref)

    def __repr__(self) -> str:
        return f"StoredList({self.ref[_MARKER][1]})"


STORED_TYPES = (StoredDict, StoredList)


def thaw(value: Any) -> Any:
    """Detach mutable control/score metadata, retaining immutable execution traces."""
    from photobench.domain.runs.frozen_trace import ITEM_TRACE_FIELDS, TURN_TRACE_FIELDS

    if isinstance(value, dict):
        return {
            key: child if key in {*ITEM_TRACE_FIELDS, *TURN_TRACE_FIELDS} else thaw(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [thaw(child) for child in value]
    return value


class ObjectWriter:
    def __init__(self, root: Path) -> None:
        self.path = database(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=180, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS objects (digest TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "codec INTEGER NOT NULL, payload BLOB NOT NULL, size INTEGER NOT NULL) WITHOUT ROWID"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS derivations (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
        )
        self.db.commit()
        self.known: set[str] = set()
        self.identities: dict[int, tuple[Any, Any]] = {}
        self.inserted_bytes = 0
        self.inserted_objects = 0

    def derive(self, namespace: str, inputs: Any, compute: Callable[[], Any]) -> Any:
        key = hashlib.sha256(json_bytes([namespace, inputs])).hexdigest()
        row = self.db.execute("SELECT value FROM derivations WHERE key=?", (key,)).fetchone()
        if row is not None:
            value = json.loads(row[0])
            return ObjectRef(value) if is_reference(value) else value
        value = compute()
        self.db.execute("INSERT INTO derivations VALUES (?, ?)", (key, json_bytes(value).decode()))
        return value

    def raw(self, kind: str, raw: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(kind.encode() + b"\0" + raw).hexdigest()
        if digest not in self.known:
            compressed = len(raw) > 1024
            payload = zlib.compress(raw, 1) if compressed else raw
            changed = self.db.execute(
                "INSERT OR IGNORE INTO objects VALUES (?, ?, ?, ?, ?)",
                (digest, kind, int(compressed), payload, len(raw)),
            ).rowcount
            self.inserted_bytes += len(payload) * changed
            self.inserted_objects += changed
            self.known.add(digest)
        return reference(kind, digest)

    def put(self, value: Any) -> Any:
        if isinstance(value, ObjectRef):
            return value
        if isinstance(value, STORED_TYPES) and value.reader.path == self.path.resolve():
            return ObjectRef(value.ref)
        immutable = isinstance(value, TRACE_TYPES)
        if immutable and (cached := self.identities.get(id(value))) is not None:
            return cached[1]
        if isinstance(value, dict):
            ref = self.raw("d", json_bytes({k: self.put(v) for k, v in value.items()}))
        elif isinstance(value, list):
            ref = self.raw("l", json_bytes([self.put(v) for v in value]))
        elif isinstance(value, str) and len(value) > 2048:
            ref = self.raw(
                "s",
                json_bytes(
                    [
                        self.raw("c", value[i : i + CHUNK_CHARS].encode("utf-8"))
                        for i in range(0, len(value), CHUNK_CHARS)
                    ]
                ),
            )
        else:
            return value
        if immutable:
            self.identities[id(value)] = value, ref
        return ref

    def close(self) -> None:
        self.db.close()
