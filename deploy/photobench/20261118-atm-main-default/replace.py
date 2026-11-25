"""在现有 atm-bench 入口发布已审核的1013题，保留原名和其他题集。"""

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def replace(package, payload, expected_manifest_sha, revision):
    paths = ("qa/atm-bench.jsonl", "manifest.json", "audit/preflight.json")
    before = {name: (package / name).read_bytes() for name in paths}
    assert digest(before["manifest.json"]) == expected_manifest_sha
    with zipfile.ZipFile(payload) as archive:
        qa_bytes = archive.read("qa/atm-bench.jsonl")
        plan = json.loads(archive.read("plan.json"))
    assert digest(before["qa/atm-bench.jsonl"]) == plan["before_qa_sha256"]
    assert digest(qa_bytes) == plan["after_qa_sha256"]
    old_rows = [json.loads(line) for line in before["qa/atm-bench.jsonl"].splitlines()]
    rows = [json.loads(line) for line in qa_bytes.splitlines()]
    assert len(rows) == len(old_rows) == 1013
    changed = [new["qa_id"] for old, new in zip(old_rows, rows, strict=True) if old != new]
    assert changed == plan["changed_qa_ids"]
    manifest = json.loads(before["manifest.json"])
    assert manifest["qa_sets"]["atm-bench"] == "qa/atm-bench.jsonl"
    manifest["qa_sets"].pop("atm-bench-validated", None)
    manifest["qa_set_versions"].pop("atm-bench-validated", None)
    manifest["qa_set_versions"]["atm-bench"]["qa_sha256"] = plan["after_qa_sha256"]
    assert manifest["qa_set_versions"]["atm-bench"]["count"] == 1013
    assert next(iter(manifest["qa_sets"])) == "atm-bench"
    assert all("corrected" not in name for name in manifest["qa_sets"])
    report = json.loads(before["audit/preflight.json"])
    report["atm-bench"] = plan["preflight"]
    report.pop("atm-bench-validated", None)
    after = {
        "qa/atm-bench.jsonl": qa_bytes,
        "manifest.json": encode(manifest),
        "audit/preflight.json": encode(report),
    }
    backup = package / "audit/20261118-main-default-before.zip"
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in before.items():
            archive.writestr(name, data)
    for name in paths:
        target = package / name
        staged = target.with_name(target.name + ".20261118-new")
        with staged.open("xb") as handle:
            handle.write(after[name])
        os.replace(staged, target)
    assert all((package / name).read_bytes() == data for name, data in after.items())
    receipt = {
        "revision": revision,
        "package": str(package),
        "qa_set": "atm-bench",
        "qa_count": len(rows),
        "changed_qa_ids": changed,
        "other_qa_unchanged": 1012,
        "qa_sets": list(manifest["qa_sets"]),
        "before_sha256": {name: digest(data) for name, data in before.items()},
        "after_sha256": {name: digest(data) for name, data in after.items()},
        "backup": str(backup),
        "payload_sha256": digest(payload.read_bytes()),
    }
    (package / "audit/20261118-main-default-receipt.json").write_bytes(encode(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            replace(args.package, args.payload, args.expected_manifest_sha, args.revision), ensure_ascii=False
        )
    )
