"""新建隔离 smoke 相册与五题测评；保留原相册、答卷及模型配置。单次执行。"""

from pathlib import Path
import json
import sys
import time
import httpx

BASE = Path(__file__).resolve().parent
PACKAGE = Path("/var/tmp/sentrix-atm-sources-20261117")
ALBUM = "2026.11.17-atm-sources-addresses"
SMOKE = "2026.11.17-atm-sources-smoke"
HTTP = httpx.Client(timeout=180, trust_env=False)


def request(port, path, method="GET", **kwargs):
    response = HTTP.request(method, f"http://127.0.0.1:{port}/api/v1/{path}", **kwargs)
    response.raise_for_status()
    return response.json()


def save(name, value):
    (BASE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def wait_run(identifier):
    prior = None
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        run = request(8874, f"runs/{identifier}?include_history=false")
        progress = json.dumps(
            [run.get("status"), run.get("current_phase"), run.get("qa_completed"), run.get("qa_total")],
            sort_keys=True,
        )
        if progress != prior:
            print(identifier, progress, flush=True)
            prior = progress
        if run.get("status") not in ["created", "queued", "running", "pending", "initializing"]:
            return run
        time.sleep(5)
    raise TimeoutError(identifier)


def start_run(qa_set, scope=""):
    payload = {
        "album_id": SMOKE,
        "qa_set": qa_set,
        "models": ["__current__"],
        "reasoning": True,
        "scoring_profiles": ["atm_qs_v1"],
        "qa_concurrency": 1,
        "graph_enabled": False,
        "mode": "reuse" if scope else "full",
    }
    if scope:
        payload["existing_scope_id"] = scope
    result = request(8874, "runs", method="POST", json=payload)
    identifier = result["run_ids"][0]
    save("smoke-" + qa_set + "-intent.json", {"request": payload, "response": result})
    print("Started", identifier, flush=True)
    run = wait_run(identifier)
    details = request(8874, f"runs/{identifier}/items?page_size=100")["items"]
    items = []
    for row in details:
        item = request(8874, f"runs/{identifier}/items/{row['index']}")["item"]
        items.append(item)
    save("smoke-" + qa_set + "-result.json", {"run": run, "items": items})
    assert run["status"] in ["completed", "completed_with_errors"], run.get("error") or run["status"]
    assert len(items) == (2 if qa_set == "atm-bench-email" else 3)
    assert all(item.get("source_mapping_complete") for item in items), "Source mapping missing"
    # API 和两种正式导出必须包含已冻结的来源信息。
    for export in ["export-trace", "export-sft"]:
        response = HTTP.get(f"http://127.0.0.1:8874/api/v1/runs/{identifier}/{export}")
        response.raise_for_status()
        save("smoke-" + qa_set + "-" + export + ".json", response.json())
        assert "source_metrics" in response.text
    return run, items


def main():
    assert not (BASE / "smoke-intent.json").exists(), "Smoke already started; use its saved run IDs"
    manifest = json.loads((BASE / "manifest.json").read_text())
    save("smoke-intent.json", {"source_revision": manifest["source_revision"], "started_at": time.time()})
    report = {"catalogs": {}, "products": {}}
    for port in [8772, 8872, 8874]:
        manifests = request(port, "manifests")
        rows = (
            manifests.get("items", manifests.get("manifests", []))
            if isinstance(manifests, dict)
            else manifests
        )
        assert any(row["album_id"] == ALBUM for row in rows), str(port)
        hard = request(port, f"qa-dataset?album_id={ALBUM}&qa_set=atm-bench-hard")
        assert len(hard["items"]) == 31
        assert sum(bool(row.get("retrieval_source_refs")) for row in hard["items"]) == 8
        # GT 邮件通过题包路由打开，避免只在 Python 内可见。
        raw = HTTP.get(f"http://127.0.0.1:{port}/api/v1/albums/{ALBUM}/documents/email202502110016.txt")
        raw.raise_for_status()
        assert "Subject:" in raw.text
        report["catalogs"][str(port)] = {"hard_qa": 31, "hard_email_qa": 8, "gt_email_accessible": True}
    raw = (PACKAGE / ALBUM / "originals/emails.json").read_bytes()
    all_emails = json.loads(raw)
    identifiers = {"email202407260006", "email202407260012", "email202407260014", "email202410070004"}
    selected = [row for row in all_emails if row["id"] in identifiers]
    photo_manifest = json.loads((PACKAGE / SMOKE / "manifest.json").read_text())
    photo = PACKAGE / SMOKE / photo_manifest["photos"][0]
    addresses = [
        json.loads(line)
        for line in (PACKAGE / SMOKE / "photos/source-metadata.jsonl").read_text().splitlines()
    ]
    address = next(row["precise_address"] for row in addresses if row["file_name"] == photo.name)
    for port in [8191, 8291, 8294]:
        scope = f"smoke-atm-docs-{manifest['source_revision'][:8]}-{port}"
        request(
            port,
            "memory-spaces",
            method="POST",
            json={"id": scope, "name": "ATM 文档导入 smoke", "kind": "benchmark"},
        )
        validation = request(
            port,
            "documents/import",
            method="POST",
            files={"file": ("emails.json", raw, "application/json")},
            data={"scope_id": scope, "validate_only": "true"},
        )
        assert validation["total"] == 6742
        body = json.dumps(selected).encode()
        first = request(
            port,
            "documents/import",
            method="POST",
            files={"file": ("emails.json", body, "application/json")},
            data={"scope_id": scope},
        )
        assert first["failed"] == 0 and all(item["indexed"] for item in first["items"]), first
        again = request(
            port,
            "documents/import",
            method="POST",
            files={"file": ("emails.json", body, "application/json")},
            data={"scope_id": scope},
        )
        assert all(item["status"] == "reused" for item in again["items"]), again
        asset = request(port, "assets/" + first["items"][0]["asset_id"])
        assert asset["source_metadata"]["document_type"] == "email"
        assert asset["captured_at"] == next(
            row["timestamp"].replace(" ", "T") for row in selected if row["id"] == asset["external_source_id"]
        )
        search = request(port, f"search?q=Cairo&scope_id={scope}&media_type=text")
        assert search["items"] and any(
            row["channel"] == "text_ann" and row["candidate_count"] for row in search["channel_trace"]
        )
        image = request(
            port,
            "media/import",
            method="POST",
            files={"file": (photo.name, photo.read_bytes(), "image/jpeg")},
            data={"scope_id": scope},
        )
        loaded = request(port, "assets/" + image["asset_id"])
        assert loaded["source_metadata"]["precise_address"] == address
        assert loaded["place"] == address
        report["products"][str(port)] = {
            "scope_id": scope,
            "validation_total": validation["total"],
            "imported": len(first["items"]),
            "idempotent": True,
            "text_ann_nonempty": True,
            "provided_address": True,
        }
        save("smoke-api.json", report)
        print(port, "document/address APIs passed", flush=True)
    first_run, email_items = start_run("atm-bench-email")
    second_run, hard_items = start_run("atm-bench-hard", first_run["scope_id"])
    assert all(item.get("predicted_source_refs") for item in email_items), "Email final citations missing"
    report["runs"] = [
        {
            "run_id": run["run_id"],
            "status": run["status"],
            "questions": len(items),
            "answers": [
                {
                    "qa_id": item["qa_id"],
                    "answer": item.get("answer"),
                    "citations": item.get("predicted_source_refs"),
                    "source_metrics": item.get("source_metrics"),
                }
                for item in items
            ],
        }
        for run, items in [(first_run, email_items), (second_run, hard_items)]
    ]
    report["status"] = "passed"
    save("smoke-acceptance.json", report)
    print(
        json.dumps({"status": "passed", "runs": [run["run_id"] for run in [first_run, second_run]]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
