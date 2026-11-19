"""Future ATM packages retain explicit abstention labels and immutable inputs."""

import importlib.util
import json
from pathlib import Path

import pytest

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


def test_email_qa_registration_preserves_sources_and_separates_mixed_questions(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    from prepare_atm_email_qa import register_email_qa

    source, album = tmp_path / "source", tmp_path / "atm-bench-media"
    qa = source / "data/atm-bench"
    qa.mkdir(parents=True)
    email = source / "data/raw_memory/email/emails.json"
    email.parent.mkdir(parents=True)
    email.write_text(json.dumps([{"id": "email202401010001"}]))
    original = {
        "id": "mail-q",
        "question": "Which invoice?",
        "answer": "email202401010001",
        "qtype": "list_recall",
        "notes": "",
        "evidence_ids": ["email202401010001"],
    }
    main = qa / "atm-bench.json"
    main.write_text(json.dumps([original]))
    hard = qa / "atm-bench-hard.json"
    hard.write_text(
        json.dumps(
            [
                {**original, "id": "hard", "qtype": "open_end", "answer": "ABSTENTION. Unknown."},
                {
                    **original,
                    "id": "mixed",
                    "qtype": "list_recall",
                    "answer": "20240101_120000",
                    "evidence_ids": ["email202401010001", "20240101_120000"],
                },
            ]
        )
    )
    (album / "audit").mkdir(parents=True)
    manifest = {
        "album_id": "atm-bench-media",
        "album_name": "Existing album",
        "qa_sets": {"existing": "qa/existing.jsonl"},
        "qa_set_versions": {},
    }
    (album / "manifest.json").write_text(json.dumps(manifest))
    (album / "audit/media-id-map.json").write_text(
        json.dumps(
            {
                "20240101_120000": {"media_type": "image", "media_id": "20240101_120000.jpg"},
            }
        )
    )
    snapshots = {p: p.read_bytes() for p in (main, hard, email)}
    counts = register_email_qa(source, album)
    assert counts == {"atm-email-main-1q": 1, "atm-email-hard-1q": 1, "atm-email-mixed-hard-1q": 1}
    rows = BUILDER.read_jsonl(album / "qa/atm-email-main-1q.jsonl")
    assert rows[0]["source_qa"] == original
    assert rows[0]["retrieval_media_refs"] == [{"media_type": "email", "media_id": "email202401010001"}]
    assert rows[0]["evaluation"]["target_media_refs"] == rows[0]["retrieval_media_refs"]
    mixed = BUILDER.read_jsonl(album / "qa/atm-email-mixed-hard-1q.jsonl")[0]
    assert mixed["evaluation"]["target_media_refs"] == [
        {"media_type": "image", "media_id": "20240101_120000.jpg"},
    ]
    assert len(mixed["retrieval_media_refs"]) == 2
    updated = json.loads((album / "manifest.json").read_text())
    assert updated["qa_sets"]["existing"] == manifest["qa_sets"]["existing"]
    assert updated["text_memories"] == "text/emails.json"
    assert updated["qa_set_versions"]["atm-email-main-1q"]["memory_status"] == "imported_on_full_run"
    assert json.loads((album / "text/emails.json").read_text())[0]["id"] == "email202401010001"
    assert all(p.read_bytes() == content for p, content in snapshots.items())
    with pytest.raises(ValueError, match="already exists"):
        register_email_qa(source, album)
