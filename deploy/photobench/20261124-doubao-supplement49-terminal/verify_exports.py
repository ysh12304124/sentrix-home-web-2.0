"""Validate complete online exports of the authorized Doubao 49-QA supplement."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ACCEPT_DIR = Path("/home/realmagic/xy/experiments/atm1013-20261118/doubao-supplement49-terminal-20261124-v1")
sys.path.insert(0, str(ACCEPT_DIR))
from inspect_terminal import APP, BATCH, ROOT, RUN, api, current, fingerprint, snapshot  # noqa: E402

from photobench.application.runs.reports import RunReports  # noqa: E402
from photobench.domain.evaluation.profile_view import primary_profile_state, profile_state  # noqa: E402
from photobench.domain.evaluation.profiles import result_key, valid_result  # noqa: E402
from photobench.domain.runs import exports  # noqa: E402
from photobench.infrastructure.results.split_store import SplitRunReader  # noqa: E402
from photobench.infrastructure.results.store import FileRunStore  # noqa: E402

HERE = Path(__file__).resolve().parent


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def service_memory():
    process = int(
        subprocess.check_output(
            [
                "systemctl",
                "--user",
                "show",
                "sentrix-atm100-doubao-photobench.service",
                "-p",
                "MainPID",
                "--value",
            ],
            text=True,
        )
    )
    fields = dict(
        line.split(":", 1) for line in Path(f"/proc/{process}/status").read_text().splitlines() if ":" in line
    )
    command = Path(f"/proc/{process}/cmdline").read_bytes().decode().split("\0")
    app = Path(command[command.index("--app-dir") + 1])
    deployed = app / "photobench/domain/runs/exports.py"
    return {
        "pid": process,
        "app_dir": str(app),
        "imported_exports_match_service": Path(exports.__file__).resolve() == deployed.resolve(),
        "exports_source_sha256": hashlib.sha256(deployed.read_bytes()).hexdigest(),
        **{k: fields[k].strip() for k in ("VmRSS", "VmHWM")},
    }


def pinned_service(before):
    after = service_memory()
    for key in ("pid", "app_dir", "exports_source_sha256"):
        assert after[key] == before[key], f"Evaluator {key} changed during export validation"
    assert after["imported_exports_match_service"]
    return after


def progress(result, phase):
    value = {**result, "phase": phase, "updated_at": datetime.now().astimezone().isoformat()}
    (HERE / "exports-progress.json").write_text(json.dumps(value, ensure_ascii=False, indent=2))
    print(json.dumps({"phase": phase, "exports": result.get("exports", {})}), flush=True)


def baseline():
    accepted = json.loads((ACCEPT_DIR / "terminal-acceptance.json").read_text())
    assert accepted["accepted"] and all(accepted["checks"].values())
    assert accepted["original_964_answer_content_unchanged"]
    assert accepted["original_964_valid_receipts_unchanged"]
    assert accepted["source_all_items_and_all_scores_unchanged"]
    assert accepted["seed_items_and_all_scores_unchanged"]
    assert not accepted["missing_targets"] and not accepted["judge_failures"]
    assert not api(8874, "active-work")["work"]
    reader, state, batch = current()
    assert reader.revision == accepted["read_revision"]
    assert fingerprint(state["items"]) == accepted["all_items_reference_sha256"]
    assert fingerprint(state["scoring_results"]) == accepted["all_scoring_receipts_sha256"]
    assert (
        fingerprint({k: state["scoring_results"][k] for k in accepted["valid_score_keys"]})
        == (accepted["valid_receipts_reference_sha256"])
    )
    assert batch["batch_id"] == BATCH
    return reader, state, accepted


def verify():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    assert not (HERE / "exports-acceptance.json").exists()
    launched = json.loads((HERE / "exports-intent.json").read_text())
    reader, state, accepted = baseline()
    revision = reader.revision
    assert revision == launched["read_revision"]
    selected = primary_profile_state(profile_state(state, "atm_qs_v1", BATCH))
    item_fingerprint = fingerprint(state["items"])
    scoring_fingerprint = fingerprint(state["scoring_results"])
    failures = accepted["execution_failures"]
    result = {
        "run_id": RUN,
        "worker_pid": os.getpid(),
        "read_revision": revision,
        "batch_id": BATCH,
        "qa_count": 1013,
        "valid_scores": accepted["valid_atm_scores"],
        "retained_execution_failures": sorted(failures),
        "retained_judge_failures": [],
        "exports": {},
        "service_memory_before": service_memory(),
        "export_bodies_saved": False,
        "all_items_fingerprint": item_fingerprint,
        "all_scoring_receipts_fingerprint": scoring_fingerprint,
    }
    assert result["service_memory_before"]["pid"] == launched["evaluation_pid"] == 1588
    assert result["service_memory_before"]["app_dir"] == launched["evaluation_app_dir"] == str(APP)
    assert result["service_memory_before"]["exports_source_sha256"] == launched["exports_source_sha256"]
    assert result["service_memory_before"]["imported_exports_match_service"]
    assert len({i["qa_id"] for i in selected["items"]}) == len(selected["items"]) == 1013
    projected_scores = samples = steps = quarantined = failed_items = 0
    progress(result, "contracts")
    for item in selected["items"]:
        one = {**selected, "items": [item]}
        trace = exports.export_traces(one)
        sft = exports.export_sft(one)
        assert len(trace) == len(sft["items"]) == 1
        row = sft["items"][0]
        qa = item["qa_id"]
        assert trace[0]["qa_id"] == row["qa_id"] == qa
        assert row["answer"] == item.get("answer")
        assert row["termination_reason"] == item.get("termination_reason")
        score = state["scoring_results"].get(result_key(BATCH, qa, 0)) or {}
        for output in (trace[0], row):
            actual = output["profile_scores"]["atm_qs_v1"]
            assert actual["batch_id"] == BATCH
            assert actual["score"] == (score["score"] if valid_result(score) else None)
        if qa in failures:
            assert row["termination_reason"] == failures[qa]["termination_reason"]
            assert item["execution_status"] == failures[qa]["execution_status"]
            assert row["profile_scores"]["atm_qs_v1"]["score"] is None
            failed_items += 1
        projected_scores += int(valid_result(score))
        quarantined += int(bool(item.get("trace_quarantine")))
        samples += sft["count"]
        steps += sum(len(t["steps"]) for t in row["turns"])
    assert projected_scores == accepted["valid_atm_scores"] and failed_items == len(failures)
    assert quarantined == sum(bool(i.get("trace_quarantine")) for i in state["items"])
    result.update(
        sft_samples=samples,
        sft_steps=steps,
        contract_checked_items=1013,
        retained_quarantine_markers=quarantined,
        retained_failed_item_count=failed_items,
    )
    reports = RunReports(FileRunStore(ROOT), None, lambda: "")
    for kind in ("trace", "sft"):
        pinned_service(result["service_memory_before"])
        started = time.monotonic()
        expected_hash, expected_bytes = hashlib.sha256(), 0
        progress(result, "expected_" + kind)
        for fragment in reports.stream_export(
            RUN, sft=kind == "sft", scoring_profile="atm_qs_v1", scoring_batch=BATCH
        ):
            raw = fragment.encode("utf-8")
            expected_hash.update(raw)
            expected_bytes += len(raw)
        expected_seconds = time.monotonic() - started
        assert SplitRunReader(ROOT, ROOT / RUN).revision == revision
        pinned_service(result["service_memory_before"])
        progress({**result, "expected_bytes": expected_bytes}, "http_" + kind)
        actual_hash, actual_bytes = hashlib.sha256(), 0
        started = time.monotonic()
        url = (
            f"http://127.0.0.1:8874/api/v1/runs/{RUN}/export-{kind}"
            f"?scoring_profile=atm_qs_v1&scoring_batch={BATCH}"
        )
        with urllib.request.urlopen(url, timeout=300) as response:
            assert response.status == 200
            assert "application/json" in response.headers["Content-Type"]
            assert "attachment" in response.headers["Content-Disposition"]
            while chunk := response.read(65536):
                actual_hash.update(chunk)
                actual_bytes += len(chunk)
        assert actual_bytes == expected_bytes
        assert actual_hash.hexdigest() == expected_hash.hexdigest()
        assert SplitRunReader(ROOT, ROOT / RUN).revision == revision
        pinned_service(result["service_memory_before"])
        result["exports"][kind] = {
            "bytes": actual_bytes,
            "sha256": actual_hash.hexdigest(),
            "expected_sha256": expected_hash.hexdigest(),
            "http_seconds": time.monotonic() - started,
            "expected_seconds": expected_seconds,
            "verified": True,
        }
        progress(result, "verified_" + kind)
    final_reader, final_state, _ = current()
    assert final_reader.revision == revision
    assert fingerprint(final_state["items"]) == item_fingerprint
    assert fingerprint(final_state["scoring_results"]) == scoring_fingerprint
    final_probe = snapshot()
    for key in (
        "exact_run_matches",
        "service_pids_match",
        "evaluation_app_matches",
        "source_manifest_unchanged",
        "prior_manifests_unchanged",
        "prior_source_manifest_unchanged",
        "seed_manifest_unchanged",
        "other_stage_mapping_unchanged",
        "writer_source_matches",
        "product_app_matches",
        "product_sources_match",
        "previous_supplement_manifests_unchanged",
        "model_matches",
        "product_limits_match",
        "product_database_match",
    ):
        assert final_probe[key], key
    assert not final_probe["active"] and not final_probe["live_turns"]
    result.update(
        status="verified_with_preserved_execution_failures",
        finished_at=datetime.now().astimezone().isoformat(),
        service_memory_after=pinned_service(result["service_memory_before"]),
        validator_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        answers_attempts_and_all_score_receipts_unchanged=True,
        source_manifests_and_product_runtime_unchanged=True,
    )
    save("exports-acceptance.json", result)
    progress(result, "completed")


def launch(wait_id):
    uuid.UUID(wait_id)
    assert not (HERE / "exports-intent.json").exists()
    assert not (HERE / "exports-acceptance.json").exists()
    reader, _, _ = baseline()
    service = service_memory()
    assert service["pid"] == 1588 and service["app_dir"] == str(APP)
    assert service["imported_exports_match_service"]
    probe = snapshot()
    for key in (
        "exact_run_matches",
        "service_pids_match",
        "evaluation_app_matches",
        "source_manifest_unchanged",
        "prior_manifests_unchanged",
        "prior_source_manifest_unchanged",
        "seed_manifest_unchanged",
        "other_stage_mapping_unchanged",
        "writer_source_matches",
        "product_app_matches",
        "product_sources_match",
        "previous_supplement_manifests_unchanged",
        "model_matches",
        "product_limits_match",
        "product_database_match",
    ):
        assert probe[key], key
    assert not probe["active"] and not probe["live_turns"]
    assert min(probe["disk_free"].values()) >= 1536 * 1024**2
    command = [sys.executable, str(Path(__file__).resolve()), "verify"]
    save(
        "exports-intent.json",
        {
            "wait_id": wait_id,
            "run_id": RUN,
            "command": command,
            "probe": probe,
            "read_revision": reader.revision,
            "evaluation_pid": service["pid"],
            "evaluation_app_dir": service["app_dir"],
            "exports_source_sha256": service["exports_source_sha256"],
        },
    )
    progress({"run_id": RUN, "read_revision": reader.revision, "exports": {}}, "starting")
    with (HERE / "exports-worker.log").open("xb") as output:
        worker = subprocess.Popen(
            command,
            cwd=HERE,
            env={**os.environ, "PYTHONPATH": str(APP)},
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    response = {
        "wait_id": wait_id,
        "run_id": RUN,
        "worker_pid": worker.pid,
        "started_at": datetime.now().astimezone().isoformat(),
        "read_revision": reader.revision,
        "evaluation_pid": service["pid"],
        "evaluation_app_dir": service["app_dir"],
        "exports_source_sha256": service["exports_source_sha256"],
    }
    save("exports-response.json", response)
    print(json.dumps(response), flush=True)


def probe(worker_pid):
    response = json.loads((HERE / "exports-response.json").read_text())
    assert worker_pid == response["worker_pid"]
    process = Path(f"/proc/{worker_pid}/cmdline")
    command = process.read_bytes().decode().split("\0") if process.exists() else []
    result = json.loads((HERE / "exports-progress.json").read_text())
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    service = service_memory()
    return {
        "run_id": RUN,
        "worker_pid": worker_pid,
        "worker_alive": str(Path(__file__).resolve()) in command and "verify" in command,
        "phase": result.get("phase"),
        "exports": result.get("exports", {}),
        "accepted": (HERE / "exports-acceptance.json").exists(),
        "evaluation_pid_matches": service["pid"] == response["evaluation_pid"],
        "evaluation_app_matches": service["app_dir"] == response["evaluation_app_dir"],
        "exports_source_matches": service["exports_source_sha256"] == response["exports_source_sha256"],
        "revision_matches": SplitRunReader(ROOT, ROOT / RUN).revision == response["read_revision"],
        "disk_free": {p: shutil.disk_usage(p).free for p in ("/home", "/var/tmp")},
        "memory_available_kib": int(memory["MemAvailable"].split()[0]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=("launch", "verify", "probe"))
    parser.add_argument("--wait-id")
    parser.add_argument("--worker-pid", type=int)
    args = parser.parse_args()
    if args.operation == "launch":
        launch(args.wait_id)
    elif args.operation == "verify":
        verify()
    else:
        print(json.dumps(probe(args.worker_pid)), flush=True)
