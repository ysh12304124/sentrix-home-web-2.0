"""Pin frozen QA definitions and exact GT image bytes for AI analysis."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from photobench.domain.datasets.ports import AlbumSource
from photobench.domain.runs.analysis import media_key
from photobench.domain.runs.judge_recovery import gt_image_refs


def image_catalog(albums: AlbumSource | None, album: str) -> dict[tuple[str, str], list[str]]:
    """Use the same typed media identity as Judge, restricted to this album's manifest."""
    catalog: dict[tuple[str, str], list[str]] = {}
    if not albums or not album:
        return catalog
    try:
        manifest = albums.manifest(album)
    except KeyError:
        return catalog
    for entry in manifest.get("photos") or []:
        relative = PurePosixPath(str(entry))
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(entry):
            continue
        if len(relative.parts) < 2 or relative.parts[0] != "photos":
            continue
        name = relative.relative_to("photos").as_posix()
        catalog.setdefault(media_key("image", name), []).append(name)
    return catalog


def image_file(
    albums: AlbumSource | None, album: str, media_id: str, catalog: dict[tuple[str, str], list[str]]
) -> tuple[str | None, str | None]:
    """Logical GT prefixes are identity namespaces, not filesystem subdirectories."""
    logical = PurePosixPath(media_id)
    if logical.is_absolute() or ".." in logical.parts or "\\" in media_id:
        return None, None
    candidates = catalog.get(media_key("image", media_id), [])
    if len(candidates) != 1 or not albums:
        return None, None
    relative = candidates[0]
    return albums.media_file(album, "photos", relative), "photos/" + relative


def freeze_side(
    target: Path,
    side: str,
    parent: Path,
    source: dict[str, Any],
    albums: AlbumSource | None,
    indices: set[int],
    *,
    include_raw: bool = True,
) -> None:
    rows = source.pop("_qa_rows", [])
    counts = Counter(str(r.get("qa_id") or "") for r in rows)
    definitions = {str(r["qa_id"]): r for r in rows if r.get("qa_id") and counts[str(r["qa_id"])] == 1}
    directory = target / side
    directory.mkdir()
    album = str((source.get("qa_snapshot") or {}).get("album_id") or "")
    catalog = image_catalog(albums, album)
    with (parent / "input.jsonl").open() as stream:
        for index, line in enumerate(stream):
            if not line.strip() or index not in indices:
                continue
            item = json.loads(line)
            qa = definitions.get(str(item.get("qa_id") or ""))
            package: dict[str, Any] = {
                "index": index,
                "qa": qa,
                "item": item,
                "gt_images": [],
                "missing_evidence": [] if qa else ["frozen_qa_definition_not_recorded_or_ambiguous"],
                "trace_fields_recorded": [
                    k
                    for k in ("runtime_turns", "execution_trace", "tool_trace", "model_call_metrics")
                    if item.get(k)
                ],
                "trace_completeness": (
                    "all_saved_fields_included; original_unrecorded_calls_cannot_be_reconstructed"
                ),
            }
            if not package["trace_fields_recorded"]:
                package["missing_evidence"].append("execution_trajectory_not_recorded")
            turns = item.get("runtime_turns") or [item]
            for turn_index, turn in enumerate(turns):
                refs, ref_source = gt_image_refs(qa or item, turn)
                if ref_source == "not_recorded":
                    package["missing_evidence"].append(f"turn{turn_index}:gt_references_not_recorded")
                for ref_index, ref in enumerate(refs):
                    entry: dict[str, Any] = {**ref, "turn": turn_index, "source": ref_source}
                    if ref["media_type"] != "image":
                        package["missing_evidence"].append(
                            f"turn{turn_index}:video_gt_not_attached:{ref['media_id']}"
                        )
                        continue
                    path, dataset_path = image_file(albums, album, ref["media_id"], catalog)
                    entry["dataset_path"] = dataset_path
                    mime = mimetypes.guess_type(ref["media_id"])[0]
                    if path and mime in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                        try:
                            data = Path(path).read_bytes()
                            sha = hashlib.sha256(data).hexdigest()
                            file = directory / f"{sha}.image"
                            if not file.exists():
                                file.write_bytes(data)
                            entry.update(
                                file=file.name,
                                sha256=sha,
                                mime=mime,
                                status="attached",
                                provenance=(
                                    "current_dataset_bytes_at_analysis_creation; "
                                    "original_run_image_hash_not_verified"
                                ),
                            )
                        except OSError:
                            entry.update(status="unavailable", error="gt_image_read_failed")
                    else:
                        entry.update(status="unavailable", error="exact_gt_image_unavailable_or_unsupported")
                    entry["id"] = f"{side}:i{index}:t{turn_index}:gt{ref_index}"
                    package["gt_images"].append(entry)
            if not include_raw:
                for image in package["gt_images"]:
                    image["id"] = image["id"].removeprefix(f"{side}:")
                package = {k: package[k] for k in ("index", "gt_images", "missing_evidence")}
            (directory / f"input-{index}.json").write_text(json.dumps(package, ensure_ascii=False))
    (directory / "source.json").write_text(json.dumps(source, ensure_ascii=False))
