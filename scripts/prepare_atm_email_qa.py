"""Register ATM email QA and package the email corpus for full-run ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from build_benchmark_adapters import definition, write_qa


def register_email_qa(source: Path, album: Path) -> dict[str, int]:
    manifest_path = album / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("album_id") != "atm-bench-media":
        raise ValueError("Expected the existing atm-bench-media album")
    emails_path = source / "data/raw_memory/email/emails.json"
    email_rows = json.loads(emails_path.read_text(encoding="utf-8"))
    email_ids = {row["id"] for row in email_rows}
    if len(email_ids) != len(email_rows):
        raise ValueError("Duplicate source email IDs")
    media = json.loads((album / "audit/media-id-map.json").read_text(encoding="utf-8"))
    outputs: dict[str, list[dict]] = {}
    for filename, suffix in (("atm-bench.json", "main"), ("atm-bench-hard.json", "hard")):
        for original in json.loads((source / "data/atm-bench" / filename).read_text(encoding="utf-8")):
            evidence = original.get("evidence_ids") or []
            if not any(str(identifier).startswith("email") for identifier in evidence):
                continue
            mixed = any(not str(identifier).startswith("email") for identifier in evidence)
            group = "mixed-hard" if mixed and suffix == "hard" else "mixed-main" if mixed else suffix
            refs = []
            for identifier in evidence:
                if identifier in email_ids:
                    refs.append({"media_type": "email", "media_id": identifier})
                elif identifier in media:
                    refs.append(media[identifier])
                else:
                    raise ValueError(f"Unresolved evidence: {identifier}")
            abstention = bool(re.match(r"^ABSTENTION(?:[.：: ]|$)", original["answer"].strip(), re.I))
            row = {
                "qa_id": "atm-" + original["id"],
                "source_qa_id": original["id"],
                "benchmark_id": "atm-bench",
                "question": original["question"],
                "answer": original["answer"],
                "source_qa": original,
                "expected_action": "refuse" if abstention else "answer",
                "answerability": "insufficient_evidence" if abstention else "answerable",
                "retrieval_media_refs": refs,
                "answer_evidence_media_refs": refs,
            }
            qtype = original["qtype"]
            extra = {}
            if qtype == "list_recall":
                targets = re.findall(r"email\d+|\d{8}_\d{6}", original["answer"])
                if not targets or any(
                    identifier not in email_ids and identifier not in media for identifier in targets
                ):
                    raise ValueError("Unresolved list target")
                extra = {
                    "target_media_refs": [
                        {"media_type": "email", "media_id": i} if i in email_ids else media[i]
                        for i in targets
                    ],
                    "retrieval_requirement": "all",
                    "official_answer": original["answer"],
                }
            row["evaluation"] = definition(
                row,
                "media_list" if qtype == "list_recall" else "open",
                {"list_recall": "atm_list", "number": "atm_number", "open_end": "atm_open"}[qtype],
                source_qtype=qtype,
                **extra,
            )
            outputs.setdefault(group, []).append(row)
    counts = {}
    # Preflight every target before producing files; existing registered QA is not overwritten.
    for group, rows in outputs.items():
        name = f"atm-email-{group}-{len(rows)}q"
        if name in manifest.get("qa_sets", {}) or (album / "qa" / f"{name}.jsonl").exists():
            raise ValueError(f"QA set already exists: {name}")
    email_memory_path = album / "text" / "emails.json"
    email_memory_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(emails_path, email_memory_path)
    manifest["text_memories"] = "text/emails.json"
    for group, rows in outputs.items():
        name = f"atm-email-{group}-{len(rows)}q"
        version = write_qa(album, name, rows)
        version.update(
            {
                "memory_status": "imported_on_full_run",
                "registration_scope": "full_run_text_memory_import",
                "description": "全链路运行会先将 ATM-Bench 邮件导入统一文本记忆并建索引，再执行 QA。",
            }
        )
        manifest.setdefault("qa_sets", {})[name] = f"qa/{name}.jsonl"
        manifest.setdefault("qa_set_versions", {})[name] = version
        counts[name] = len(rows)
    manifest.setdefault("evaluation_notes", {})["email_qa"] = {
        "status": "email_memory_import_enabled",
        "counts": counts,
        "email_corpus_sha256": hashlib.sha256(emails_path.read_bytes()).hexdigest(),
        "note": (
            "邮件及混合题独立分组；全链路评测会将 emails.json 导入选定相册，"
            "复用测评要求该相册已完成邮件导入。"
        ),
    }
    temporary = manifest_path.with_suffix(".json.email-qa.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--album", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(register_email_qa(args.source, args.album), ensure_ascii=False, indent=2))
