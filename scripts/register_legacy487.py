#!/usr/bin/env python3
"""Register the byte-identical historical 487QA using a separate album overlay.

The SHA matches completed run
20261023-001533-album3-max-video10-qwen35-mixed-3p1-33b6f2. Original task tags
remain present; the later evaluation definitions and QA revisions are absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

ALBUM = "album3-max-video10"
QA_SET = "mixed-image-video-487q"
QA_SHA256 = "73fa4512dde072fb0df9cf45ff3ebd192192db606f9764054bde002dcbf0be47"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(album: Path, legacy_qa: Path, output: Path, *, activate: bool = False) -> dict[str, Any]:
    source = album.resolve(strict=True)
    original_link = os.readlink(album) if album.is_symlink() else None
    if activate and original_link is None:
        raise ValueError("Activation requires an existing album symlink")
    raw = legacy_qa.read_bytes()
    if hashlib.sha256(raw).hexdigest() != QA_SHA256:
        raise ValueError("Historical QA SHA256 mismatch")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ids = [row.get("qa_id") for row in rows]
    if len(rows) != 487 or not all(ids) or len(set(ids)) != 487:
        raise ValueError("Historical QA must contain 487 unique IDs")
    if any("evaluation" in row or "ability_taxonomy_version" in row for row in rows):
        raise ValueError("Historical QA must precede typed evaluation and ability annotations")
    manifest_path = source / "manifest.json"
    manifest_sha = digest(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("album_id") != ALBUM:
        raise ValueError("Unexpected source album")
    before = dict(manifest["qa_sets"])
    if QA_SET in before:
        raise ValueError("Historical QA is already registered")
    existing_qa = {name: digest(source / relative) for name, relative in before.items()}
    output = output.resolve()
    if output.exists() or output.is_symlink() or output.is_relative_to(source):
        raise ValueError("Output must be a new directory outside the source album")

    output.mkdir(parents=True)
    for entry in source.iterdir():
        if entry.name not in {"manifest.json", "qa"}:
            (output / entry.name).symlink_to(entry.resolve(), target_is_directory=entry.is_dir())
    qa = output / "qa"
    qa.mkdir()
    for entry in (source / "qa").iterdir():
        if entry.name == QA_SET + ".jsonl":
            continue
        (qa / entry.name).symlink_to(entry.resolve(), target_is_directory=entry.is_dir())
    relative = "qa/" + QA_SET + ".jsonl"
    (output / relative).write_bytes(raw)
    manifest["qa_sets"][QA_SET] = relative
    manifest.setdefault("qa_set_versions", {})[QA_SET] = {
        "dataset_version": "legacy487-" + QA_SHA256[:12],
        "schema_version": "legacy-original-v1",
        "qa_sha256": QA_SHA256,
        "count": 487,
        "description": "老版本487QA：原题、原答案、原多轮及证据；未加入后来的评分题型定义。",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if digest(manifest_path) != manifest_sha:
        raise ValueError("Source manifest changed during registration")
    if any(digest(output / before[name]) != sha for name, sha in existing_qa.items()):
        raise ValueError("Existing QA changed during registration")
    receipt = {
        "album_id": ALBUM,
        "qa_set": QA_SET,
        "qa_count": len(rows),
        "qa_sha256": digest(output / relative),
        "legacy_source": str(legacy_qa.resolve()),
        "source_album": str(source),
        "source_manifest_sha256": manifest_sha,
        "overlay_album": str(output),
        "qa_sets_before": before,
        "qa_sets_after": manifest["qa_sets"],
        "existing_qa_sha256": existing_qa,
        "activated": False,
    }
    if activate:
        if not album.is_symlink() or os.readlink(album) != original_link:
            raise ValueError("Album registration changed before activation")
        link = album.with_name("." + album.name + "-" + uuid.uuid4().hex)
        link.symlink_to(output, target_is_directory=True)
        os.replace(link, album)
        receipt["activated"] = True
        receipt["previous_album_link"] = original_link
    (output / "legacy487-registration.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--album", type=Path, required=True)
    parser.add_argument("--legacy-qa", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            register(args.album, args.legacy_qa, args.output, activate=args.activate), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
