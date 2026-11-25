"""将官方 ATM 材料与 QA 转换为独立数据包；不改原件，不将 QA 写进记忆。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from photobench.domain.datasets.validation import validate_targets
from photobench.domain.evaluation.contract import VERSION, content_fingerprint, validate_evaluation
from sentrix.application.media.documents import parse_document_package


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def write_suites(
    output: Path, suites: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]
) -> dict[str, Any]:
    reports = {}
    exclusions = {}
    for name, rows in list(suites.items()):
        report = validate_targets(rows, manifest)
        invalid = {issue["qa_id"] for issue in report["issues"] if issue["severity"] == "error"}
        if invalid:
            verified_name = name + "-validated"
            suites[verified_name] = [row for row in rows if row["qa_id"] not in invalid]
            exclusions[verified_name] = sorted(invalid)
    for name, rows in suites.items():
        if not rows:
            continue
        relative = f"qa/{name}.jsonl"
        write_jsonl(output / relative, rows)
        manifest["qa_sets"][name] = relative
        manifest["qa_set_versions"][name] = {
            "count": len(rows),
            "evaluation_version": VERSION,
            "schema_version": "typed-v1",
            "qa_sha256": sha(output / relative),
            **({"excluded_qa_ids": exclusions[name]} if name in exclusions else {}),
        }
        reports[name] = validate_targets(rows, manifest)
    return reports


def adapt_qa(
    original: dict[str, Any], media: dict[str, dict[str, str]], email_ids: set[str]
) -> dict[str, Any]:
    def ref(identifier: str) -> dict[str, str]:
        if identifier in email_ids:
            return {"source_type": "email", "source_id": identifier}
        if identifier in media:
            return dict(media[identifier])
        # 保留来源里的不一致 ID，由预检报告定位，不猜测映射。
        return {"media_type": "image", "media_id": identifier + ".jpg"}

    evidence = [ref(identifier) for identifier in original["evidence_ids"]]
    media_refs = [r for r in evidence if "media_type" in r]
    sources = [r for r in evidence if "source_type" in r]
    kind = original["qtype"]
    row = {
        "qa_id": "atm-" + original["id"],
        "source_qa_id": original["id"],
        "benchmark_id": "atm-bench",
        "question": original["question"],
        "answer": original["answer"],
        "expected_action": "answer",
        "answerability": "answerable",
        "retrieval_media_refs": media_refs,
        "answer_evidence_media_refs": media_refs,
        "retrieval_source_refs": sources,
        "answer_evidence_source_refs": sources,
        "source_protocol": "source-citations-v1",
        "source_qa": original,
    }
    definition: dict[str, Any] = {
        "version": VERSION,
        "scorer": {"number": "atm_number", "open_end": "atm_open", "list_recall": "atm_list"}[kind],
        "answer_format": "open",
        "max_score": 1,
        "applies_to_turn": 0,
        "source_qtype": kind,
    }
    if kind == "list_recall":
        identifiers = re.findall(r"email\d+|\d{8}_\d{6}", original["answer"])
        targets = [ref(identifier) for identifier in dict.fromkeys(identifiers)]
        target_media = [r for r in targets if "media_type" in r]
        target_sources = [r for r in targets if "source_type" in r]
        if target_media and target_sources:
            raise ValueError("列表答案同时包含文本与媒体，需显式定义联合列表评分")
        definition.update(
            answer_format="source_list" if target_sources else "media_list",
            retrieval_requirement="all",
            official_answer=original["answer"],
        )
        definition["target_source_refs" if target_sources else "target_media_refs"] = (
            target_sources or target_media
        )
    definition["content_sha256"] = content_fingerprint(row)
    row["evaluation"] = definition
    validate_evaluation(row)
    return row


def build(
    raw: Path,
    qa_files: list[Path],
    output: Path,
    *,
    embed_addresses: bool = False,
    selected: set[str] | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    emails_path = raw / "email/emails.json"
    emails = parse_document_package(emails_path.read_bytes(), "emails.json", "atm_email")
    email_by_id = {record.external_source_id: record for record in emails}
    media = {
        path.stem: {"media_type": kind, "media_id": path.name if kind == "image" else path.stem}
        for collection, kind in (("image", "image"), ("video", "video"))
        for path in (raw / collection).iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".mp4", ".mov"}
    }
    suites = {
        path.stem: [
            adapt_qa(row, media, set(email_by_id))
            for row in json.loads(path.read_text())
            if selected is None or row["id"] in selected
        ]
        for path in qa_files
    }
    if selected and sum(len(rows) for rows in suites.values()) != len(selected):
        raise ValueError("部分所选题ID不存在或跨题集重复")
    selected_refs = [
        r
        for rows in suites.values()
        for row in rows
        for r in row["retrieval_media_refs"] + row["evaluation"].get("target_media_refs", [])
    ]
    wanted_media = (
        {(r["media_type"], Path(r["media_id"]).stem) for r in selected_refs}
        if selected
        else {(r["media_type"], stem) for stem, r in media.items()}
    )
    wanted_emails = (
        {r["source_id"] for rows in suites.values() for row in rows for r in row["retrieval_source_refs"]}
        if selected
        else set(email_by_id)
    )
    output.mkdir(parents=True)
    for folder in ("photos", "videos", "documents", "qa", "audit", "originals"):
        (output / folder).mkdir()
    shutil.copy2(emails_path, output / "originals/emails.json")
    manifest: dict[str, Any] = {
        "album_id": output.name,
        "album_name": "ATM-Bench 主集 / Hard · 图视频与邮件" + (" smoke" if selected else ""),
        "photos": [],
        "videos": [],
        "documents": [],
        "document_source_refs": [],
        "faces": [],
        "qa_sets": {},
        "qa_set_versions": {},
        "benchmark_id": "atm-bench",
        "source_protocol": "source-citations-v1",
    }
    metadata_rows: dict[str, list[dict[str, Any]]] = {"photos": [], "videos": [], "documents": []}
    tag_rows, checks = [], []
    for folder, kind, collection in (("photos", "image", "image"), ("videos", "video", "video")):
        for source in sorted((raw / collection).iterdir()):
            if (kind, source.stem) not in wanted_media or source.stem not in media:
                continue
            cache = raw / f"geocoding_cache/{collection}/{source.stem}_location_name.json"
            address = json.loads(cache.read_text())["location_name"]
            if not isinstance(address, str) or not address.strip():
                raise ValueError(f"缺少地址: {cache}")
            target = output / folder / source.name
            shutil.copy2(source, target)
            metadata = {
                "file_name": source.name,
                "precise_address": address,
                "address_source": "atm_geocoding_cache",
                "address_source_sha256": sha(cache),
                "metadata": {"cache_path": str(cache), "original_sha256": sha(source)},
            }
            metadata_rows[folder].append(metadata)
            manifest[folder].append(folder + "/" + source.name)
            tag_rows.append(
                {
                    "SourceFile": str(target.resolve()),
                    "XMP-sentrix:PreciseAddress": address,
                    "XMP-sentrix:AddressSource": "atm_geocoding_cache",
                    "XMP-sentrix:AddressSourceSHA256": sha(cache),
                }
            )
            checks.append(
                {
                    "path": str(target.relative_to(output)),
                    "source_sha256": sha(source),
                    "cache_sha256": sha(cache),
                }
            )
    for identifier in sorted(wanted_emails):
        record = email_by_id[identifier]
        name = identifier + ".txt"
        (output / "documents" / name).write_bytes(record.text.encode("utf-8"))
        metadata_rows["documents"].append(
            {
                "file_name": name,
                "external_source_id": identifier,
                "source_timestamp": record.source_timestamp,
                **record.import_request("unused").source_metadata.to_dict(),
            }
        )
        manifest["documents"].append("documents/" + name)
        manifest["document_source_refs"].append({"source_type": "email", "source_id": identifier})
    for folder, rows in metadata_rows.items():
        write_jsonl(output / folder / "source-metadata.jsonl", rows)
    write_jsonl(output / "media-metadata.jsonl", metadata_rows["photos"] + metadata_rows["videos"])
    if embed_addresses and tag_rows:
        write_json(output / "audit/address-tags.json", tag_rows)
        config = Path(__file__).with_name("sentrix-xmp.config")
        subprocess.run(
            [
                "exiftool",
                "-config",
                str(config),
                "-overwrite_original",
                "-P",
                "-json=" + str(output / "audit/address-tags.json"),
                *[str(output / folder) for folder in ("photos", "videos")],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    for check in checks:
        check["output_sha256"] = sha(output / check["path"])
    for name, rows in list(suites.items()):
        suites[name + "-email"] = [row for row in rows if row["retrieval_source_refs"]]
    reports = write_suites(output, suites, manifest)
    write_json(output / "manifest.json", manifest)
    write_json(output / "audit/preflight.json", reports)
    write_json(output / "audit/media-checksums.json", checks)
    write_json(
        output / "audit/provenance.json",
        {
            "emails_sha256": sha(emails_path),
            "qa": [{"path": str(path), "sha256": sha(path)} for path in qa_files],
            "embedded_addresses": embed_addresses,
            "media_count": len(checks),
            "email_count": len(wanted_emails),
        },
    )
    return {
        "output": str(output),
        "media": len(checks),
        "documents": len(wanted_emails),
        "qa": {
            name: {"count": len(rows), "errors": reports.get(name, {}).get("error_count")}
            for name, rows in suites.items()
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--raw-memory", type=Path, required=True)
    parser.add_argument("--qa", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embed-addresses", action="store_true")
    parser.add_argument("--qa-id", action="append")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.raw_memory,
                args.qa,
                args.output,
                embed_addresses=args.embed_addresses,
                selected=set(args.qa_id) if args.qa_id else None,
            ),
            ensure_ascii=False,
        )
    )
