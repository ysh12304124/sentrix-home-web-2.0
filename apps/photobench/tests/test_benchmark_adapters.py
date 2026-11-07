"""Future ATM packages retain explicit abstention labels and immutable inputs."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "benchmark_adapters", Path(__file__).resolve().parents[3] / "scripts/build_benchmark_adapters.py"
)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def test_atm_package_marks_explicit_abstention_without_rewriting_sources(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    image = source / "data/raw_memory/image"
    image.mkdir(parents=True)
    (image / "20240801_201540.jpg").write_bytes(b"fixture")
    (source / "data/raw_memory/video").mkdir()
    qa = source / "data/atm-bench"
    qa.mkdir()
    original = {}
    for filename, count in (("atm-bench.json", 631), ("atm-bench-hard.json", 23)):
        rows = [
            {
                "id": str(i),
                "question": "Which poster?",
                "qtype": "open_end",
                "answer": "ABSTENTION. Info not included in the image." if i == 0 else "Stanford",
                "evidence_ids": ["20240801_201540"],
            }
            for i in range(count)
        ]
        path = qa / filename
        path.write_text(json.dumps(rows))
        original[path] = path.read_bytes()
    BUILDER.build_atm(source, output)
    for name in ("atm-media-main-631q", "atm-media-hard-23q"):
        rows = BUILDER.read_jsonl(output / "atm-bench-media/qa" / (name + ".jsonl"))
        assert rows[0]["expected_action"] == "refuse"
        assert rows[0]["answerability"] == "insufficient_evidence"
        assert rows[0]["evaluation"]["scorer"] == "atm_open"
        assert rows[0]["retrieval_media_refs"] == [{"media_type": "image", "media_id": "20240801_201540.jpg"}]
        assert all(
            row["expected_action"] == "answer" and row["answerability"] == "answerable" for row in rows[1:]
        )
    assert all(path.read_bytes() == data for path, data in original.items())
