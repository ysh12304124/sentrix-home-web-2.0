#!/usr/bin/env python3
"""Create an APFS model copy with a Transformers-compatible image-processor class.

The ModelScope originals stay immutable. Weights, normalization, resizing and
tokenizer bytes are unchanged; the derived preprocessor hash is recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def prepare(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists() or source in destination.parents:
        raise ValueError("use a new independent model directory")
    record_path = destination.parent / (destination.name + ".compatibility.json")
    if record_path.exists():
        raise ValueError("refusing to overwrite an existing model compatibility record")
    config = source / "processor_config.json"
    original = config.read_bytes()
    processor = json.loads(original)
    if processor.get("image_processor", {}).get("image_processor_type") != "Qwen3VLImageProcessor":
        raise ValueError("expected the ModelScope nested Qwen3VL image processor configuration")
    subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], check=True)
    processor["image_processor"]["image_processor_type"] = "Qwen2VLImageProcessor"
    derived = (json.dumps(processor, indent=2) + "\n").encode()
    (destination / config.name).write_bytes(derived)
    record = {
        "change": (
            "Use the Transformers Qwen3VL image backend Qwen2VLImageProcessor "
            "in the nested processor config; retain all preprocessing parameters"
        ),
        "file": config.name,
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "derived_sha256": hashlib.sha256(derived).hexdigest(),
        "weights_tokenizer_template_unchanged": True,
    }
    record_path.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    prepare(arguments.source, arguments.destination)
