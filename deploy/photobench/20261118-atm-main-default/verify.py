"""只读核验共用题包和三个测评API，保存发布前后回执。"""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses")
ALBUM = PACKAGE.name
PORTS = (8772, 8872, 8874)
QA_ID = "atm-500f1059-8d20-4bd7-be43-32ef39bee511"
SETS = ["atm-bench", "atm-bench-hard", "atm-bench-email", "atm-bench-hard-email"]
EXPECTED = {
    "qa/atm-bench.jsonl": "13d07b33d8e9c522347bf485539a9a71d8ce569385d1ecf26620218b7e3383c3",
    "manifest.json": "4a282e7f697179ab192604fabdf2a0932bf4aac3b5cceb88119948cd1ab7c1a4",
    "audit/preflight.json": "7e07df2504fc8549b44d04a7f2c944593e14d7aabd6ff4092200bec80023a2a9",
}


def get(port, path):
    with urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=60) as response:
        return json.load(response)


def verify(phase):
    files = [
        *PACKAGE.glob("qa/*.jsonl"),
        PACKAGE / "manifest.json",
        PACKAGE / "audit/preflight.json",
    ]
    hashes = {
        str(p.relative_to(PACKAGE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    }
    result = {"phase": phase, "sha256": hashes, "services": {}}
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    if phase == "after":
        before = json.loads(Path(__file__).with_name("before.json").read_text())
        assert all(hashes[k] == v for k, v in EXPECTED.items())
        assert all(
            hashes[k] == v for k, v in before["sha256"].items() if k not in EXPECTED
        )
    for port in PORTS:
        active = get(port, "active-work")
        assert active["work"] == []
        catalog = get(port, "manifests")["manifests"]
        assert catalog[0]["album_id"] == ALBUM
        entry = next(x for x in catalog if x["album_id"] == ALBUM)
        status = {"active": active, "catalog": entry}
        if phase == "after":
            assert entry["qa_sets"] == SETS
            detail = get(port, f"manifests/{ALBUM}")
            assert (
                detail["qa_set_versions"]["atm-bench"]["qa_sha256"]
                == EXPECTED["qa/atm-bench.jsonl"]
            )
            data = get(port, f"qa-dataset?album_id={ALBUM}&qa_set=atm-bench")
            assert len(data["items"]) == 1013
            assert data["validation"]["error_count"] == 0
            row = next(x for x in data["items"] if x["qa_id"] == QA_ID)
            assert row["answer"].startswith("20230905_144627,")
            targets = row["evaluation"]["target_media_refs"]
            expected_refs = [(x["media_type"], x["media_id"]) for x in targets]
            for field in ("retrieval_media_refs", "answer_evidence_media_refs"):
                assert [(x["media_type"], x["media_id"]) for x in row[field]] == expected_refs
            media_status = []
            for ref in targets:
                rel = next(
                    x for x in manifest["photos"] if Path(x).name == ref["media_id"]
                )
                url = f"http://127.0.0.1:{port}/api/v1/albums/{ALBUM}/{rel}"
                with urlopen(
                    Request(url, headers={"Range": "bytes=0-31"}), timeout=20
                ) as response:
                    assert response.status in (200, 206)
                    assert response.read(32)
                    media_status.append(
                        {"media_id": ref["media_id"], "status": response.status}
                    )
            status.update(
                count=len(data["items"]),
                validation=data["validation"],
                corrected_row=row,
                evidence_media=media_status,
                media_resolution=data["media_resolution"],
            )
        result["services"][str(port)] = status
    output = Path(__file__).with_name(f"{phase}.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "phase": phase,
                "ports": list(PORTS),
                "passed": True,
                "receipt": str(output),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["before", "after"])
    verify(parser.parse_args().phase)
