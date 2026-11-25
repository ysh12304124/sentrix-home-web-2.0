"""Verify both complete HTTP exports against a pinned run, without saving their bodies."""

from __future__ import annotations

import hashlib
import json
import resource
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from inspect_terminal import BATCH, KEEP_FAILED, ROOT, RUN
from score_four import api

from photobench.application.runs.reports import RunReports
from photobench.domain.evaluation.profile_view import primary_profile_state, profile_state
from photobench.domain.evaluation.profiles import result_key, valid_result
from photobench.domain.runs import exports
from photobench.infrastructure.results.split_store import SplitRunReader
from photobench.infrastructure.results.store import FileRunStore

HERE = Path(__file__).resolve().parent


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def service_memory():
    pid = subprocess.check_output(
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
    ).strip()
    status = Path("/proc/" + pid + "/status").read_text().splitlines()
    return {
        "pid": int(pid),
        **{s.split(":")[0]: s.split(":")[1].strip() for s in status if s.startswith(("VmRSS:", "VmHWM:"))},
    }


def main():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    assert not (HERE / "exports-acceptance.json").exists()
    accepted = json.loads((HERE / "scoring-acceptance.json").read_text())
    assert accepted["valid_atm_scores"] == 1012 and accepted["all_answers_unchanged"]
    assert not api("active-work")["work"]
    reader = SplitRunReader(ROOT, ROOT / RUN)
    revision = reader.revision
    assert revision == accepted["read_revision"]
    state = reader.state()
    selected = primary_profile_state(profile_state(state, "atm_qs_v1", BATCH))
    all_ids = {i["qa_id"] for i in state["items"]}
    assert len(all_ids) == len(state["items"]) == 1013
    projected_scores = 0
    samples = steps = 0
    before = service_memory()
    # Check the complete record contract independently before hashing its serialization.
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
        projected_scores += int(valid_result(score))
        samples += sft["count"]
        steps += sum(len(t["steps"]) for t in row["turns"])
        if qa == KEEP_FAILED:
            assert row["termination_reason"] == "model_error"
            assert row["profile_scores"]["atm_qs_v1"]["score"] is None
    assert projected_scores == 1012
    result = {
        "run_id": RUN,
        "read_revision": revision,
        "batch_id": BATCH,
        "qa_count": 1013,
        "valid_scores": 1012,
        "retained_failure": KEEP_FAILED,
        "sft_samples": samples,
        "sft_steps": steps,
        "exports": {},
        "service_memory_before": before,
        "export_bodies_saved": False,
    }
    reports = RunReports(FileRunStore(ROOT), None, lambda: "")
    for kind in ("trace", "sft"):
        started = time.monotonic()
        expected_hash, expected_bytes = hashlib.sha256(), 0
        save("export-progress.json", {**result, "phase": "expected_" + kind})
        print(json.dumps({"phase": "expected_" + kind, "qa_count": 1013}), flush=True)
        for fragment in reports.stream_export(
            RUN, sft=kind == "sft", scoring_profile="atm_qs_v1", scoring_batch=BATCH
        ):
            raw = fragment.encode("utf-8")
            expected_hash.update(raw)
            expected_bytes += len(raw)
        expected_seconds = time.monotonic() - started
        assert SplitRunReader(ROOT, ROOT / RUN).revision == revision
        save("export-progress.json", {**result, "phase": "http_" + kind, "expected_bytes": expected_bytes})
        print(
            json.dumps(
                {
                    "phase": "http_" + kind,
                    "expected_bytes": expected_bytes,
                    "expected_seconds": expected_seconds,
                }
            ),
            flush=True,
        )
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
        result["exports"][kind] = {
            "bytes": actual_bytes,
            "sha256": actual_hash.hexdigest(),
            "expected_sha256": expected_hash.hexdigest(),
            "http_seconds": time.monotonic() - started,
            "expected_seconds": expected_seconds,
            "verified": True,
        }
        save("export-progress.json", result)
        print(json.dumps({kind: result["exports"][kind]}), flush=True)
    assert not api("active-work")["work"]
    result.update(
        status="verified",
        finished_at=datetime.now().astimezone().isoformat(),
        service_memory_after=service_memory(),
        validator_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    assert result["service_memory_after"]["pid"] == before["pid"]
    with (HERE / "exports-acceptance.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
