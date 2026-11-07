"""Build isolated ATM and PhotoBench evaluation packages; never edits input media."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from photobench.domain.evaluation.contract import VERSION, content_fingerprint, validate_evaluation

BAD_ATM_ID = "500f1059-8d20-4bd7-be43-32ef39bee511"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_qa(root: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        validate_evaluation(row)
    file = root / "qa" / (name + ".jsonl")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return {
        "qa_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "evaluation_version": VERSION,
        "count": len(rows),
        "schema_version": "typed-v1",
    }


def definition(row: dict[str, Any], form: str, scorer: str, maximum: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "version": VERSION,
        "answer_format": form,
        "scorer": scorer,
        "max_score": maximum,
        "applies_to_turn": max(0, len(row.get("conversation") or []) - 1),
        "content_sha256": content_fingerprint(row),
        **extra,
    }


def build_atm(source: Path, output: Path, media_mode: str = "reference") -> dict[str, Any]:
    root = output / "atm-bench-media"
    root.mkdir(parents=True)
    media: dict[str, dict[str, str]] = {}
    hashes = []
    for directory, kind, dest in (("image", "image", "photos"), ("video", "video", "videos")):
        (root / dest).mkdir()
        for original in sorted((source / "data/raw_memory" / directory).iterdir()):
            if original.suffix.lower() not in {".jpg", ".mp4"}:
                continue
            target = root / dest / original.name
            if media_mode == "copy":
                shutil.copy2(original, target)
            else:
                target.symlink_to(original.resolve())
            before = hashlib.sha256(original.read_bytes()).hexdigest()
            after = hashlib.sha256(target.read_bytes()).hexdigest()
            if before != after:
                raise ValueError("Media copy changed bytes")
            media[original.stem] = {
                "media_type": kind,
                "media_id": original.name if kind == "image" else original.stem,
            }
            hashes.append({"source": str(original), "path": str(target.relative_to(root)), "sha256": before})
    manifest: dict[str, Any] = {
        "album_id": root.name,
        "album_name": "ATM-Bench 媒体子集",
        "photos": [x["path"] for x in hashes if x["path"].startswith("photos/")],
        "videos": [x["path"] for x in hashes if x["path"].startswith("videos/")],
        "faces": [],
        "qa_sets": {},
        "qa_set_versions": {},
        "benchmark_id": "atm-bench",
        "source_commit": "ef4e5dff1a47ec71213a06e359f02753defa8fb1",
    }
    exclusions, differences, counts = [], [], {}
    for source_name, name, expected in (
        ("atm-bench.json", "atm-media-main-631q", 631),
        ("atm-bench-hard.json", "atm-media-hard-23q", 23),
    ):
        converted = []
        for original in json.loads((source / "data/atm-bench" / source_name).read_text()):
            evidence = original.get("evidence_ids") or []
            if not evidence or any(identifier not in media for identifier in evidence):
                exclusions.append(
                    {"split": source_name, "id": original["id"], "reason": "non_media_or_unresolved_evidence"}
                )
                continue
            ids = re.findall(r"\d{8}_\d{6}", original["answer"]) if original["qtype"] == "list_recall" else []
            if ids and set(ids) != set(evidence):
                differences.append(
                    {
                        "split": source_name,
                        "id": original["id"],
                        "answer_ids": ids,
                        "evidence_ids": evidence,
                        "excluded": original["id"] == BAD_ATM_ID,
                    }
                )
            if original["id"] == BAD_ATM_ID:
                exclusions.append(
                    {
                        "split": source_name,
                        "id": original["id"],
                        "reason": "official_answer_unknown_media_20230904_144627",
                        "source_qa": original,
                    }
                )
                continue
            if any(i not in media for i in ids):
                raise ValueError("Unresolved list target")
            abstention = bool(re.match(r"^ABSTENTION(?:[.：: ]|$)", original["answer"].strip(), re.I))
            row = {
                "qa_id": "atm-" + original["id"],
                "source_qa_id": original["id"],
                "benchmark_id": "atm-bench",
                "question": original["question"],
                "answer": original["answer"],
                "expected_action": "refuse" if abstention else "answer",
                "answerability": "insufficient_evidence" if abstention else "answerable",
                "retrieval_media_refs": [media[i] for i in evidence],
                "answer_evidence_media_refs": [media[i] for i in evidence],
                "source_qa": original,
            }
            qtype = original["qtype"]
            row["evaluation"] = definition(
                row,
                "media_list" if qtype == "list_recall" else "open",
                {"list_recall": "atm_list", "number": "atm_number", "open_end": "atm_open"}[qtype],
                source_qtype=qtype,
                **(
                    {
                        "target_media_refs": [media[i] for i in ids],
                        "retrieval_requirement": "all",
                        "official_answer": original["answer"],
                    }
                    if ids
                    else {}
                ),
            )
            converted.append(row)
        if len(converted) != expected:
            raise ValueError(f"Unexpected ATM count: {len(converted)}")
        manifest["qa_sets"][name] = f"qa/{name}.jsonl"
        manifest["qa_set_versions"][name] = write_qa(root, name, converted)
        counts[name] = dict(Counter(r["evaluation"]["source_qtype"] for r in converted))
    write_json(root / "manifest.json", manifest)
    write_json(root / "audit/media-sha256.json", hashes)
    write_json(root / "audit/exclusions.json", exclusions)
    write_json(root / "audit/list-target-evidence-differences.json", differences)
    write_json(root / "audit/media-id-map.json", media)
    return counts


def build_photobench(
    source: Path,
    subset: Path,
    research: Path,
    output: Path,
    reviewed: Path,
    media_mode: str = "reference",
) -> dict[str, Any]:
    root = output / "album3-max-video10"
    root.mkdir(parents=True)
    source_root = source.parent.parent
    manifest = json.loads((source_root / "manifest.json").read_text())
    # Copy only the media/catalog inputs; source fixed package remains immutable.
    for name in ("photos", "videos", "faces", "metadata", "identity"):
        if (source_root / name).exists():
            if media_mode == "copy" or name in {"metadata", "identity"}:
                shutil.copytree(source_root / name, root / name)
            else:
                (root / name).symlink_to((source_root / name).resolve(), target_is_directory=True)
    master = {r["qa_id"]: r for r in read_jsonl(research / "candidate.master.jsonl")}
    policy = {r["qa_id"]: r for r in read_jsonl(research / "candidate.evaluation-policy.jsonl")}
    review_rows = read_jsonl(reviewed)
    decisions = {r["qa_id"]: r for r in review_rows}
    if len(decisions) != len(review_rows):
        raise ValueError("Duplicate QA review decisions")
    rows = read_jsonl(source)
    if set(decisions) != {row["qa_id"] for row in rows}:
        raise ValueError("Every source QA requires exactly one review")
    audit = []
    for row in rows:
        identifier = row["qa_id"]
        old = master[identifier]
        changed = content_fingerprint(row) != content_fingerprint(old["open_qa"])
        entry = decisions[identifier]
        if entry.get("review_status") != "verified":
            raise ValueError(f"Unreviewed QA: {identifier}")
        if entry and entry["content_sha256"] != content_fingerprint(row):
            raise ValueError("Review does not match current QA content")
        pure = policy[identifier]["task"] == "open_retrieval"
        if entry:
            pure = entry.get("answer_format") == "media_list"
        if pure:
            requirement = entry.get("retrieval_requirement", "all") if entry else "all"
            row["evaluation"] = definition(
                row,
                "media_list",
                "media_set",
                target_media_refs=row["retrieval_media_refs"],
                retrieval_requirement=requirement,
                review_status=entry["review_status"] if entry else "pending_review",
            )
        elif entry and entry.get("answer_format") == "choice":
            choice = entry.get("multiple_choice") or old["multiple_choice"]
            row["evaluation"] = definition(
                row,
                "choice",
                "choice_judge",
                options=choice["options"],
                correct_option=choice["correct_option"],
                review_status=entry["review_status"],
            )
        else:
            row["evaluation"] = definition(
                row,
                "open",
                "open_judge",
                2,
                review_status=entry["review_status"] if entry else "pending_review",
            )
        audit.append(
            {
                "qa_id": identifier,
                "content_sha256": content_fingerprint(row),
                "changed_since_options": changed,
                "evaluation": row["evaluation"],
                "review": entry,
            }
        )
    ids = [r["qa_id"] for r in read_jsonl(subset)]
    by_id = {r["qa_id"]: r for r in rows}
    selected = [by_id[identifier] for identifier in ids]
    if len(rows) != 1171 or len(selected) != 487:
        raise ValueError("PhotoBench cardinality mismatch")
    manifest["album_name"] = "PhotoBench album3 题型评分候选"
    manifest["qa_sets"] = {}
    manifest["qa_set_versions"] = {}
    for name, group in (("typed-1171q", rows), ("typed-487q", selected)):
        manifest["qa_sets"][name] = f"qa/{name}.jsonl"
        manifest["qa_set_versions"][name] = write_qa(root, name, group)
    write_json(root / "manifest.json", manifest)
    write_json(root / "audit/qa-review.json", audit)
    return {
        "full": dict(Counter(r["evaluation"]["answer_format"] for r in rows)),
        "subset": dict(Counter(r["evaluation"]["answer_format"] for r in selected)),
        "pending_review": sum(r["evaluation"].get("review_status") != "verified" for r in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atm-source", type=Path, required=True)
    parser.add_argument("--photobench-source", type=Path, required=True)
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--media-mode", choices=("reference", "copy"), default="reference")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new directory")
    args.output.mkdir(parents=True)
    result = {
        "media_mode": args.media_mode,
        "atm": build_atm(args.atm_source, args.output, args.media_mode),
        "photobench": build_photobench(
            args.photobench_source, args.subset, args.research, args.output, args.reviewed, args.media_mode
        ),
    }
    write_json(args.output / "build-report.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
