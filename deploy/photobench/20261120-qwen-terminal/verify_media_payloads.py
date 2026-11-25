"""Compare decoded images and copied AV packets, ignoring changed container metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

DB = "/var/tmp/sentrix-atm-doubao-20261112/product-data/sentrix.db"
SCOPE = "PhotoBench-20261112-174516-atm-bench-media-current"
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses")


def fingerprint(path, kind):
    if kind == "image":
        with Image.open(path) as picture:
            orientation = picture.getexif().get(274, 1)
            rgb = picture.convert("RGB")
            return (rgb.size, orientation, hashlib.sha256(rgb.tobytes()).hexdigest())
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-f",
            "streamhash",
            "-hash",
            "sha256",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return result.stdout


def compare(record):
    old = fingerprint(record["path"], record["media_type"])
    new = fingerprint(record["new_path"], record["media_type"])
    return {
        "id": record["id"],
        "file_name": record["file_name"],
        "kind": record["media_type"],
        "matches": old == new,
        "old_fingerprint": old,
        "new_fingerprint": new,
    }


def main():
    started = time.monotonic()
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    paths = {Path(p).name: PACKAGE / p for p in manifest["photos"] + manifest["videos"]}
    with sqlite3.connect("file:" + DB + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        records = [
            dict(r)
            for r in db.execute(
                "select id,file_name,path,media_type from assets "
                "where scope_id=? and parent_asset_id is null order by file_name",
                (SCOPE,),
            )
        ]
    for row in records:
        row["new_path"] = str(paths[row["file_name"]])
    counts = Counter()
    mismatches = []
    digest = hashlib.sha256()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index, result in enumerate(pool.map(compare, records), 1):
            counts[result["kind"]] += 1
            counts[result["kind"] + "_matches"] += result["matches"]
            digest.update(json.dumps(result, sort_keys=True).encode() + b"\n")
            if not result["matches"]:
                mismatches.append(result)
            if index % 500 == 0:
                print(
                    json.dumps({"checked": index, "elapsed_s": round(time.monotonic() - started, 1)}),
                    flush=True,
                )
    output = {
        "read_only": True,
        "scope_id": SCOPE,
        "counts": dict(counts),
        "mismatches": mismatches,
        "comparison_sha256": digest.hexdigest(),
        "elapsed_s": round(time.monotonic() - started, 1),
    }
    target = Path(__file__).parent / "media-payload-acceptance.json"
    with target.open("x") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
    print(json.dumps(output, ensure_ascii=False), flush=True)
    assert counts["image"] == counts["image_matches"] == 3759
    assert counts["video"] == counts["video_matches"] == 533


if __name__ == "__main__":
    main()
