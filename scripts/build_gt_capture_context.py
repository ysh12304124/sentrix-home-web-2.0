"""Build an offline Judge-only sidecar from versioned media annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from photobench.domain.evaluation.gt_context import SCHEMA, context_index
from photobench.domain.evaluation.profiles import digest


def file_sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def source_location(image: dict[str, Any]) -> str:
    """Keep consistent administrative labels; nearby POI names prove no venue."""
    candidates = [
        candidate["properties"]
        for lookup in image["location_evidence"].get("current_online_lookups", [])
        if lookup.get("http_status") == 200
        for candidate in lookup.get("candidates", [])
    ]
    chain: list[str] = []
    for key in ("country", "state", "city"):
        values = {c[key] for c in candidates if c.get(key)}
        if len(values) != 1:
            break
        value = next(iter(values))
        # Photon can label a village as city. Only Chinese 市 labels have been reviewed here.
        if key == "city" and (chain[0] != "中国" or not value.endswith("市")):
            break
        if value not in chain:
            chain.append(value)
    return " / ".join(chain)


def build(album: Path, verify_against: Path | None = None) -> dict[str, Any]:
    manifest = json.loads((album / "manifest.json").read_text())
    photo_path = album / "metadata/image_metadata.jsonl"
    video_path = album / "metadata/video_source_images.json"
    photos = [json.loads(line) for line in photo_path.read_text().splitlines() if line.strip()]
    photo_index = {p["filename"]: p for p in photos}
    videos = {v["video_id"]: v for v in json.loads(video_path.read_text())["videos"]}
    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "album_id": manifest["album_id"],
        "annotation_version": manifest["location_annotation_version"],
        "sources": [
            {"path": str(p.relative_to(album)), "sha256": file_sha(p)}
            for p in (album / "manifest.json", photo_path, video_path)
        ],
        "entries": [],
    }
    for kind, paths in (("image", manifest["photos"]), ("video", manifest["videos"])):
        for relative in paths:
            path = album / relative
            media_sha = file_sha(path)
            if verify_against is not None and file_sha(verify_against / relative) != media_sha:
                raise ValueError("Media content differs: " + relative)
            if kind == "image":
                photo = photo_index[path.name]
                evidence = photo.get("capture_time_evidence") or {}
                if evidence.get("sha256") and evidence["sha256"] != media_sha:
                    raise ValueError("Annotation does not match media content: " + relative)
                media_id = photo["image_id"]
                metadata = {
                    "captured_at": evidence.get("captured_at_local") or photo.get("capture_datetime"),
                    "capture_time_note": "EXIF本地时间；时区根据GPS推定"
                    if evidence.get("timezone_is_inferred")
                    else "已记录拍摄时间；未标明时区的值不推定时区",
                    "readable_location": photo.get("readable_location"),
                    "location_note": "采用审核后的地点粒度；不确定的细地点保留上一级范围",
                }
                gps = photo.get("gps_coordinates")
                provenance = {"location_evidence_sha256": digest(photo.get("location_evidence"))}
            else:
                media_id = path.stem
                video = videos[media_id]
                captures = [
                    {
                        "media_id": image["image_id"],
                        "captured_at": image.get("capture_time"),
                        "capture_time_note": "来源照片EXIF时间；未记录时区",
                        "readable_location": source_location(image) or None,
                        "location_note": "来源照片GPS的保守地图行政范围，不认定最近POI为拍摄场所",
                    }
                    for image in video["images"]
                ]
                times = sorted(c["captured_at"] for c in captures if c["captured_at"])
                locations = list(
                    dict.fromkeys(c["readable_location"] for c in captures if c["readable_location"])
                )
                metadata = {
                    "captured_at": " ~ ".join(dict.fromkeys((times[0], times[-1]))) if times else None,
                    "capture_time_note": "来源照片拍摄时间范围，非合成视频创建时间；未记录时区",
                    "readable_location": "；".join(locations) or None,
                    "location_note": "仅为来源照片地点范围及GPS锚点，不是轨迹，不证明全部视频帧在同一场所",
                    "source_captures": captures,
                }
                gps = [
                    {"media_id": image["image_id"], "gps": image.get("gps_coordinates")}
                    for image in video["images"]
                ]
                provenance = {"source_images_sha256": digest(video["images"])}
            aliases = [kind + ":" + name for name in (relative, path.name) if name != media_id]
            result["entries"].append(
                {
                    "media_type": kind,
                    "media_id": media_id,
                    "aliases": aliases,
                    "relative_path": relative,
                    "media_sha256": media_sha,
                    "metadata": metadata,
                    "gps_original": gps,
                    "provenance": provenance,
                }
            )
    context_index(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--album", type=Path, required=True)
    parser.add_argument("--verify-against", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.album, args.verify_against)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"entries": len(result["entries"]), "sha256": file_sha(args.output)}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
