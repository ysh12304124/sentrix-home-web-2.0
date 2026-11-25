"""核对转换包的全部地址、原件 SHA、EXIF/GPS 和图视频内容。"""

import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from sentrix.infrastructure.media.source_address import read_source_address


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify(raw: Path, package: Path) -> dict[str, Any]:
    checks = json.loads((package / "audit/media-checksums.json").read_text())

    def one(check: dict[str, Any]) -> dict[str, Any]:
        relative = Path(check["path"])
        folder = "image" if relative.parts[0] == "photos" else "video"
        source = raw / folder / relative.name
        target = package / relative
        cache = raw / "geocoding_cache" / folder / (source.stem + "_location_name.json")
        expected = json.loads(cache.read_text())["location_name"]
        result = read_source_address(target)
        assert result.precise_address == expected, relative
        assert result.address_source == "atm_geocoding_cache", relative
        assert result.address_source_sha256 == digest(cache), relative
        assert digest(source) == check["source_sha256"], relative
        assert digest(target) == check["output_sha256"], relative
        if folder == "image":
            with Image.open(source) as before, Image.open(target) as after:
                assert before.getexif().tobytes() == after.getexif().tobytes(), relative
                assert before.size == after.size and before.mode == after.mode, relative
                assert (
                    hashlib.sha256(before.tobytes()).digest() == hashlib.sha256(after.tobytes()).digest()
                ), relative
        else:

            def streams(path: Path) -> str:
                return subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-map",
                        "0",
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
                ).stdout

            def tags(path: Path) -> dict[str, Any]:
                result: dict[str, Any] = json.loads(
                    subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format_tags:stream_tags",
                            "-of",
                            "json",
                            str(path),
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                )
                return result

            assert tags(source) == tags(target), relative
            assert streams(source) == streams(target), relative
        return {
            "path": str(relative),
            "address": True,
            "original_unchanged": True,
            "capture_metadata_unchanged": True,
            "content_unchanged": True,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, checks))
    return {"passed": True, "count": len(rows), "checks": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--raw-memory", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.raw_memory, args.package)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "count": report["count"], "report": str(args.report)}))
