import copy
import runpy
from pathlib import Path

from photobench.domain.datasets.validation import validate_targets
from photobench.domain.evaluation.contract import content_fingerprint, validate_evaluation
from photobench.domain.evaluation.profiles import list_score
from photobench.domain.evaluation.sources import source_metrics_for_record, source_set_metrics
from photobench.domain.runs.exports import export_sft, export_traces

adapt_qa = runpy.run_path(str(Path(__file__).parents[3] / "scripts/prepare_atm_sources.py"))["adapt_qa"]


def test_both_exports_preserve_candidate_read_and_citation_sources():
    source = {"source_type": "email", "source_id": "email1"}
    item = {
        "qa_id": "q1",
        "source_mapping_complete": True,
        "retrieved_source_refs": [source],
        "read_source_refs": [source],
        "predicted_source_refs": [],
        "source_metrics": {"citations": {"recall": 0}},
    }
    state = {"items": [item]}
    for exported in [export_traces(state)[0], export_sft(state)["items"][0]]:
        assert exported["retrieval"]["retrieved_source_refs"] == [source]
        assert exported["retrieval"]["read_source_refs"] == [source]
        assert exported["retrieval"]["predicted_source_refs"] == []
        assert exported["retrieval"]["source_metrics"] == item["source_metrics"]
    assert "retrieval" not in export_traces({"items": [{"qa_id": "old"}]})[0]


def test_email_id_list_uses_source_delivery_and_not_answer_text():
    identifier = "email202410070004"
    row = adapt_qa(
        {
            "id": "eccv",
            "question": "Find invoice email",
            "answer": identifier,
            "qtype": "list_recall",
            "evidence_ids": [identifier],
        },
        {},
        {identifier},
    )
    validate_evaluation(row)
    assert row["evaluation"]["answer_format"] == "source_list"
    assert "target_media_refs" not in row["evaluation"]
    record = {"answer": identifier, "predicted_source_refs": [], "source_mapping_complete": True}
    assert list_score(row["evaluation"]["target_source_refs"], record, [])["score"] == 0
    record["predicted_source_refs"] = row["retrieval_source_refs"]
    assert list_score(row["evaluation"]["target_source_refs"], record, [])["score"] == 1
    manifest = {"documents": [identifier + ".txt"], "document_source_refs": row["retrieval_source_refs"]}
    assert validate_targets([row], manifest)["can_start"]
    assert not validate_targets([row], {"documents": [], "document_source_refs": []})["can_start"]


def test_fancett_answer_targets_remain_three_photos_and_two_email_evidence():
    names = ["20250221_133131", "20250221_135227", "20250221_142725"]
    emails = {"email202502110016", "email202502190009"}
    media = {name: {"media_type": "image", "media_id": name + ".jpg"} for name in names}
    row = adapt_qa(
        {
            "id": "fancett",
            "question": "Photos of Fancett lunch?",
            "answer": ", ".join(names),
            "qtype": "list_recall",
            "evidence_ids": names + sorted(emails),
        },
        media,
        emails,
    )
    assert len(row["evaluation"]["target_media_refs"]) == 3
    assert "target_source_refs" not in row["evaluation"]
    assert len(row["retrieval_source_refs"]) == 2
    assert len(row["retrieval_media_refs"]) == 3
    assert (
        list_score(
            row["evaluation"]["target_media_refs"],
            {"predicted_media": list(media.values()), "media_mapping_complete": True},
            list(media.values()),
        )["score"]
        == 1
    )


def test_source_fingerprint_and_candidate_read_citation_separation():
    source = {"source_type": "email", "source_id": "e1"}
    row = {"question": "Total?", "answer": "$432.07", "retrieval_source_refs": [source]}
    changed = copy.deepcopy(row)
    changed["retrieval_source_refs"][0]["source_id"] = "e2"
    assert content_fingerprint(row) != content_fingerprint(changed)
    result = source_metrics_for_record(
        row,
        {
            "retrieved_source_refs": [source],
            "read_source_refs": [source],
            "predicted_source_refs": [],
            "source_mapping_complete": True,
        },
    )
    assert result["candidates"]["recall"] == result["reads"]["recall"] == 1
    assert result["citations"]["recall"] == 0
    assert source_set_metrics([], [source])["status"] == "not_applicable"


def test_citation_delivery_enters_answer_fingerprint_without_changing_old_records():
    from photobench.domain.evaluation.profiles import answer_fingerprint, digest

    old = {"message": "Invoice?", "answer": "email1", "turn_id": "t1"}
    assert answer_fingerprint(old) == digest(
        {
            key: old.get(key)
            for key in ("message", "answer", "predicted_media", "media_mapping_complete", "turn_id")
        }
    )
    cited = {
        **old,
        "source_mapping_complete": True,
        "predicted_source_refs": [{"source_type": "email", "source_id": "email1"}],
    }
    missing = {**cited, "predicted_source_refs": []}
    assert answer_fingerprint(cited) != answer_fingerprint(missing)
