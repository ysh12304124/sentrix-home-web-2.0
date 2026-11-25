from __future__ import annotations

import json
from pathlib import Path

from photobench.infrastructure.results.split_store import SplitResultWriter, SplitRunReader


def state() -> dict:
    return {
        "run_id": "split-run",
        "status": "running",
        "qa_count": 1,
        "started_at": "2026-11-19T00:00:00+08:00",
        "summary": {},
        "items": [
            {
                "qa_id": "q1",
                "question": "问题",
                "answer": "回答",
                "execution_status": "completed",
                "model_call_metrics": [
                    {"step_id": "s1", "prompt_tokens": 4, "messages": [{"content": "输入"}]}
                ],
                "tool_trace": [{"tool": "search_memories", "status": "ok", "result": {"text": "结果"}}],
                "runtime_turns": [
                    {
                        "index": 0,
                        "message": "问题",
                        "answer": "回答",
                        "trace_bundle": {
                            "debug_calls": [
                                {
                                    "call_type": "agent",
                                    "step_id": "s1",
                                    "messages": [{"role": "user", "content": "输入"}],
                                    "response": {"content": "输出"},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }


def test_loop_timing_is_lazy_and_binds_only_same_turn_successful_attempt(tmp_path, monkeypatch):
    fixture = state()
    item = fixture["items"][0]
    debug = [
        {"call_id": "failed", "step_id": "s1", "attempt": 1, "status": "error", "duration_ms": 300},
        {
            "call_id": "ok",
            "step_id": "s1",
            "attempt": 2,
            "status": "complete",
            "duration_ms": 1000,
            "messages": [{"role": "user", "content": "DO_NOT_LOAD" * 10000}],
        },
    ]
    item["runtime_turns"][0]["trace_bundle"]["debug_calls"] = debug
    item["runtime_turns"].append(
        {"index": 1, "trace_bundle": {"debug_calls": [{**debug[1], "call_id": "other-turn"}]}}
    )
    item["model_call_metrics"] = [
        {"call_id": "ok", "conversation_turn": 0, "ttft_ms": 100, "completion_tokens": 20}
    ]
    item["tool_trace"] = [
        {
            "step_id": f"tool{i}",
            "parent_call_id": "ok",
            "parent_step_id": "s1",
            "duration_ms": 10,
            "tool": "search_memories",
            "result": {
                "text": "DO_NOT_LOAD" * 10000,
                "retrieval_channels": [{"channel": "text", "latency_ms": 5}],
            },
        }
        for i in range(55)
    ] + [
        {
            "step_id": "legacy",
            "parent_step_id": "s1",
            "duration_ms": 50,
            "tool": "inspect_photo",
            "result": {
                "model_call_observations": [
                    {"duration_ms": 40, "prompt_tokens": 12, "messages": ["DO_NOT_LOAD"]}
                ]
            },
        },
        {"step_id": "foreign", "parent_call_id": "ok", "conversation_turn": 1, "duration_ms": 9999},
    ]
    directory = tmp_path / "run"
    SplitResultWriter(tmp_path).write(directory, fixture)
    reader = SplitRunReader(tmp_path, directory)
    nodes = reader.nodes(0)["nodes"]
    raw = reader.reader.raw

    def no_io(ref):
        value = raw(ref)
        assert b"DO_NOT_LOAD" not in value
        return value

    monkeypatch.setattr(reader.reader, "raw", no_io)
    success = next(n for n in nodes if n["metadata"].get("call_id") == "ok")
    timing = reader.timing(0, success["node_id"])
    assert timing["node"]["duration_ms"] == 1000 and timing["node"]["ttft_ms"] == 100
    assert len(timing["tools"]) == 56
    assert sum(t["duration_ms"] for t in timing["tools"]) == 600
    assert timing["tools"][0]["retrieval_timing"]["channels"]["text"]["latency_ms"] == 5
    assert timing["tools"][-1]["model_calls"] == [{"duration_ms": 40, "prompt_tokens": 12}]
    failed = next(n for n in nodes if n["metadata"].get("call_id") == "failed")
    assert reader.timing(0, failed["node_id"])["tools"] == []
    tool_timing = reader.timing(0, timing["tools"][0]["node_id"])
    assert tool_timing["node"]["duration_ms"] == 10 and tool_timing["tools"] == []


def test_split_checkpoint_has_light_views_and_lazy_node_content(tmp_path: Path) -> None:
    root = tmp_path / "results"
    run_dir = root / "split-run"
    writer = SplitResultWriter(root)
    fixture = state()
    fixture["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"] = [
        {
            "call_type": "agent",
            "step_id": f"s{index}",
            "messages": [{"role": "user", "content": f"输入 {index}"}],
            "response": {"content": f"输出 {index}"},
        }
        for index in range(123)
    ]
    first = writer.write(run_dir, fixture)
    reader = SplitRunReader(root, run_dir)

    overview = reader.overview()
    assert overview["item_count"] == 1
    assert overview["storage_format"] == 3
    index = reader.index()
    assert index[0]["index"] == 0
    assert "runtime_turns" not in index[0]
    core = reader.core(0)
    assert core["index"] == 0
    assert core["answer"] == "回答"
    assert "model_call_metrics" not in core
    full_item = reader.item_record(0)
    assert full_item["runtime_turns"][0]["trace_bundle"]["debug_calls"][-1]["step_id"] == "s122"
    nodes = reader.nodes(0)
    # The call group includes 123 model calls and the recorded search tool.
    assert nodes["total"] == 124
    assert len(nodes["nodes"]) == 50
    assert nodes["next_offset"] == 50
    assert "record" not in nodes["nodes"][0]

    second_page = reader.nodes(0, offset=50)
    assert len(second_page["nodes"]) == 50
    assert second_page["next_offset"] == 100
    final_page = reader.nodes(0, offset=100)
    assert len(final_page["nodes"]) == 24
    assert final_page["next_offset"] is None
    content = reader.content(0, second_page["nodes"][0]["node_id"], limit=80)
    assert "输出" in content["text"] or "输入" in content["text"]

    second = writer.write(run_dir, fixture)
    assert first["inserted_objects"] > 0
    assert second["inserted_objects"] == 0
    assert second["inserted_bytes"] == 0


def test_split_reader_keeps_complete_state_for_export_and_recovery(tmp_path: Path) -> None:
    root = tmp_path / "results"
    run_dir = root / "split-run"
    writer = SplitResultWriter(root)
    writer.write(run_dir, state())
    loaded = SplitRunReader(root, run_dir).state()
    item = loaded["items"][0]
    assert item["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["response"]["content"] == "输出"
    assert item["tool_trace"][0]["result"]["text"] == "结果"


def test_split_run_item_diagnostics_use_only_selected_question(tmp_path: Path, monkeypatch):
    from photobench.application.runs.index import RunIndex
    from photobench.application.runs.read_runs import ReadRuns
    from photobench.infrastructure.results.store import FileRunStore

    root = tmp_path / "results"
    run_dir = root / "split-run"
    fixture = state()
    fixture["qa_count"] = 1
    fixture["items"] = [fixture["items"][0]]
    fixture["items"][0]["retrieved_candidate_media"] = []
    fixture["qa_snapshot"] = {
        "rows": [
            {
                "qa_id": "q1",
                "question": "问题",
                "answer": "参考答案",
                "evaluation": {"scorer": "answer_quality"},
                "conversation": [],
            }
        ]
    }
    fixture["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["response"]["content"] = (
        "长轨迹" * 20000
    )
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(fixture, ensure_ascii=False))
    from photobench.domain.error_analysis.process import process_for_qa

    assert process_for_qa(fixture, "q1")["qa_id"] == "q1"
    SplitResultWriter(root).write(run_dir, fixture)

    class NoMetadata:
        def metadata_by_qa_id(self):
            return {}

    store = FileRunStore(root)
    index = RunIndex()
    index.refresh(store)
    reads = ReadRuns(
        store,
        index,
        NoMetadata(),
        type("Http", (), {"request_json": lambda *args, **kwargs: {}})(),
        lambda: "http://sentrix",
    )
    from photobench.infrastructure.results import split_json

    original_value = split_json.ObjectReader.value

    def reject_large_string(self, value):
        if split_json.is_reference(value) and value["$photobench_object_v3"][0] == "s":
            raise AssertionError("single-item diagnostics materialized a large string")
        return original_value(self, value)

    monkeypatch.setattr(reads, "_state", lambda *args: (_ for _ in ()).throw(AssertionError("full run read")))
    monkeypatch.setattr(split_json.ObjectReader, "value", reject_large_string)
    detail = reads.get_run_item("split-run", 0)["item"]
    assert detail["reference_answer"] == "参考答案"
    assert "process_diagnostics" not in detail
    assert "model_call_metrics" not in detail
    assert "tool_trace" not in detail
    assert detail["trace_read_summary"]["model_call_count"] == 1
    assert len(detail["diagnostic_results"]) == 0


def test_score_only_publication_pins_old_revision_and_keeps_raw_values(tmp_path, monkeypatch):
    import copy
    import json

    import pytest

    from photobench.infrastructure.results import split_store
    from photobench.infrastructure.results.split_json import MANIFEST

    root = tmp_path / "results"
    directory = root / "split-run"
    writer = SplitResultWriter(root)
    fixture = state()
    fixture["items"][0]["extra_raw"] = {"$photobench_object_v3": ["d", "user text"]}
    fixture["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["response"]["content"] = (
        '原文"\\\n🙂' * 30000
    )
    first = writer.write(directory, fixture)
    pinned = SplitRunReader(root, directory)
    assert json.loads("".join(pinned.reader.iter_json(pinned.manifest["root"]))) == fixture

    changed = copy.deepcopy(fixture)
    changed["items"][0]["answer"] = "已评分的回答"
    changed["items"][0]["judge_status"] = "completed"
    second = writer.write(directory, changed)
    assert second["inserted_bytes"] < first["inserted_bytes"]
    assert pinned.state()["items"][0]["answer"] == "回答"
    assert SplitRunReader(root, directory).state()["items"][0]["answer"] == "已评分的回答"

    pointer = (directory / MANIFEST).read_bytes()
    monkeypatch.setattr(
        split_store, "publish", lambda *args: (_ for _ in ()).throw(OSError("before pointer"))
    )
    changed["items"][0]["answer"] = "未提交的回答"
    with pytest.raises(OSError, match="before pointer"):
        writer.write(directory, changed)
    assert (directory / MANIFEST).read_bytes() == pointer
    assert SplitRunReader(root, directory).state()["items"][0]["answer"] == "已评分的回答"


def test_loaded_trace_metrics_never_read_excluded_message_content(tmp_path, monkeypatch):
    from photobench.infrastructure.results.split_json import ObjectReader

    root = tmp_path / "results"
    directory = root / "split-run"
    fixture = state()
    fixture["items"][0]["model_call_metrics"][0]["messages"][0]["content"] = "heavy" * 20000
    writer = SplitResultWriter(root)
    writer.write(directory, fixture)
    loaded = SplitRunReader(root, directory).state()
    original = ObjectReader.value

    def no_text_load(self, value):
        if isinstance(value, dict) and value.get("$photobench_object_v3", [""])[0] == "s":
            raise AssertionError("metric projection materialized large input")
        return original(self, value)

    monkeypatch.setattr(ObjectReader, "value", no_text_load)
    projection = writer.metric_item(loaded["items"][0])
    assert projection["model_call_metrics"][0]["prompt_tokens"] == 4
    assert "messages" not in projection["model_call_metrics"][0]


def test_running_state_releases_committed_trace_and_keeps_judge_updates(tmp_path, monkeypatch):
    from photobench.application.run_execution.state import RunState
    from photobench.infrastructure.results.split_json import StoredDict, StoredList
    from photobench.infrastructure.results.store import FileResultWriter, FileRunStore

    monkeypatch.setenv("PHOTOBENCH_SPLIT_RESULTS", "1")
    fixture = state()
    writer = FileResultWriter(tmp_path)
    live = RunState(fixture, run_id="split-run", writer=writer)
    live.persist(wait=True)
    item = live.data["items"][0]
    assert isinstance(item["tool_trace"], StoredList)
    assert isinstance(item["runtime_turns"][0]["trace_bundle"], StoredDict)
    item["judge"] = {"score": 1}
    live.persist(wait=True)
    assert FileRunStore(tmp_path).load_state("split-run")["items"][0]["judge"] == {"score": 1}
    assert item["tool_trace"][0]["result"]["text"] == "结果"
    assert not writer._split_writer._objects.identities


def test_indexed_core_reads_never_stream_original_trace_again(tmp_path, monkeypatch):
    from photobench.infrastructure.results.split_json import ObjectReader

    root = tmp_path / "results"
    fixture = state()
    SplitResultWriter(root).write(root / "split-run", fixture)
    monkeypatch.setattr(
        ObjectReader, "fingerprint", lambda *args: (_ for _ in ()).throw(AssertionError("trace scan"))
    )
    core = SplitRunReader(root, root / "split-run").core(0)
    assert core["process_diagnostics"]["qa_id"] == "q1"


def test_explicit_legacy_index_streams_original_without_modifying_it(tmp_path):
    from photobench.infrastructure.results.index_legacy import build_index

    root = tmp_path / "results"
    directory = root / "split-run"
    directory.mkdir(parents=True)
    fixture = state()
    fixture["nested_empty"] = {"a": [], "b": {}, "unicode": "🙂\u0000" * 30000}
    source = directory / "run.json"
    raw = json.dumps(fixture, ensure_ascii=False).encode()
    source.write_bytes(raw)
    build_index(root, "split-run")
    reader = SplitRunReader(root, directory)
    assert json.loads("".join(reader.reader.iter_json(reader.manifest["root"]))) == fixture
    assert source.read_bytes() == raw
    offset, text = 0, ""
    while True:
        chunk = reader.item_content(0, ["runtime_turns", 0, "trace_bundle"], offset, 65536)
        assert len(json.dumps(chunk, ensure_ascii=False).encode()) <= 256 * 1024
        text += chunk["text"]
        if chunk["next_offset"] is None:
            break
        offset = chunk["next_offset"]
    assert json.loads(text) == fixture["items"][0]["runtime_turns"][0]["trace_bundle"]


def test_long_content_chunks_roundtrip_with_escaped_unicode(tmp_path):
    fixture = state()
    raw = '\u0000\n🙂"\\' * 50000
    fixture["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["raw_full"] = raw
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    reader = SplitRunReader(tmp_path, tmp_path / "split-run")
    offset, chunks = 0, []
    while True:
        chunk = reader.content(0, reader.nodes(0)["nodes"][0]["node_id"], field="raw_full", offset=offset)
        assert len(json.dumps(chunk, ensure_ascii=False).encode()) < 256 * 1024
        chunks.append(chunk["text"])
        if chunk["next_offset"] is None:
            break
        assert chunk["next_offset"] > offset
        offset = chunk["next_offset"]
    assert len(chunks) > 1
    assert json.loads("".join(chunks)) == raw


def test_split_exports_stream_long_call_fields_without_materializing_them(tmp_path, monkeypatch):
    from photobench.domain.runs import exports
    from photobench.domain.runs.stream_exports import sft_json, trace_json
    from photobench.infrastructure.results.split_json import ObjectReader, is_reference

    fixture = state()
    fixture["items"][0]["runtime_turns"][0]["trace_bundle"]["debug_calls"][0]["raw_full"] = "历史原文" * 30000
    expected_trace = exports.export_traces(fixture)
    expected_sft = exports.export_sft(fixture)
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    reader = SplitRunReader(tmp_path, tmp_path / "split-run")
    loaded = reader.state()
    loaded["_process_diagnostics"] = {"q1": reader.core(0)["process_diagnostics"]}
    original = ObjectReader.value

    def no_string(self, value):
        if is_reference(value) and value["$photobench_object_v3"][0] == "s":
            raise AssertionError("export joined a complete long string")
        return original(self, value)

    monkeypatch.setattr(ObjectReader, "value", no_string)
    assert json.loads("".join(trace_json(loaded))) == expected_trace
    assert json.loads("".join(sft_json(loaded))) == expected_sft


def test_restart_reuses_committed_node_and_metric_derivations(tmp_path, monkeypatch):
    from photobench.infrastructure.results import split_store
    from photobench.infrastructure.results.split_json import ObjectReader

    fixture = state()
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    loaded = SplitRunReader(tmp_path, tmp_path / "split-run").state()
    for name in ("build_nodes", "metric_record", "process_for_qa"):
        monkeypatch.setattr(
            split_store,
            name,
            lambda *args: (_ for _ in ()).throw(AssertionError("rescanned unchanged trace")),
        )
    monkeypatch.setattr(
        ObjectReader, "fingerprint", lambda *args: (_ for _ in ()).throw(AssertionError("rehash"))
    )
    result = SplitResultWriter(tmp_path).write(tmp_path / "split-run", loaded)
    assert result["inserted_bytes"] < 4096
    reader = SplitRunReader(tmp_path, tmp_path / "split-run")
    assert reader.state() == loaded
    assert reader.nodes(0)["total"] == 2
    assert reader.content(0, reader.nodes(0)["nodes"][0]["node_id"])["text"]


def test_split_mode_never_parses_legacy_history_during_reads(tmp_path, monkeypatch):
    import pytest

    from photobench.infrastructure.results import store
    from photobench.infrastructure.results.split_store import ReadIndexRequired

    directory = tmp_path / "legacy"
    directory.mkdir()
    (directory / "run.json").write_text('{"run_id":"legacy","items":[]}')
    monkeypatch.setenv("PHOTOBENCH_SPLIT_RESULTS", "1")
    with pytest.raises(ReadIndexRequired):
        store.FileRunStore(tmp_path).load_state("legacy")
    with pytest.raises(ReadIndexRequired):
        store.read_entry(directory)
    with pytest.raises(KeyError):
        store.FileRunStore(tmp_path).load_state("missing")


def test_indexed_report_export_pins_diagnostics_without_full_run_reads(tmp_path, monkeypatch):
    from photobench.application.runs.reports import RunReports
    from photobench.domain.runs import exports
    from photobench.infrastructure.results.store import FileRunStore

    fixture = state()
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    reports = RunReports(FileRunStore(tmp_path), None, lambda: "unused")
    monkeypatch.setattr(reports, "_state", lambda *args: (_ for _ in ()).throw(AssertionError("full read")))
    stream = reports.stream_export("split-run", sft=True)
    changed = state()
    changed["items"][0]["answer"] = "later answer"
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", changed)
    assert json.loads("".join(stream)) == exports.export_sft(fixture)


def test_process_findings_and_fingerprint_keep_unprojected_tool_result(tmp_path):
    from photobench.domain.error_analysis.process import process_for_qa

    fixture = state()
    fixture["items"][0]["tool_trace"] = [
        {
            "tool": "read_photo_text",
            "status": "denied",
            "arguments": {},
            "result": {"failure_reason": "no_text_detected"},
        }
    ]
    expected = process_for_qa(fixture, "q1")
    assert expected["tool_events"]
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    assert SplitRunReader(tmp_path, tmp_path / "split-run").core(0)["process_diagnostics"] == expected


def test_qa_view_excludes_diagnostics_without_losing_full_record(tmp_path):
    fixture = state()
    fixture["items"][0]["tool_trace"] = [
        {"tool": "read_photo_text", "status": "denied", "result": {"failure_reason": "no_text_detected"}}
        for _ in range(240)
    ]
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    reader = SplitRunReader(tmp_path, tmp_path / "split-run")
    full = reader.core(0)["process_diagnostics"]
    preview = reader.core(0, preview=True)
    assert "process_diagnostics" not in preview
    assert all(field["path"] != "process_diagnostics" for field in preview.get("content_fields", []))
    assert preview["trace_read_summary"]["tool_call_count"] == 240
    assert len(json.dumps(preview).encode()) < 256 * 1024
    pieces, offset = [], 0
    while True:
        chunk = reader.item_content(0, ["process_diagnostics"], offset, 1024)
        pieces.append(chunk["text"])
        if chunk["next_offset"] is None:
            break
        offset = chunk["next_offset"]
    assert json.loads("".join(pieces)) == full
    assert len(reader.core(0)["process_diagnostics"]["tool_events"]) == 240


def test_legacy_stream_preserves_answer_fingerprints_and_numeric_types(tmp_path):
    import gzip
    import math

    from photobench.domain.evaluation.profiles import answer_fingerprint
    from photobench.infrastructure.results.index_legacy import ingest
    from photobench.infrastructure.results.split_json import ObjectReader, ObjectWriter, database, thaw

    record = {
        "message": "题目",
        "answer": '\\"' * 100000,
        "predicted_media": [
            {
                "media_type": "video",
                "start_s": 0.0,
                "end_s": 30.0,
                "offset": -0.0,
                "big": 18446744073709551617,
            }
        ],
        "empty": [{}, []],
        "finite": 1.2345678901234567,
        "tiny": 1e-100,
    }
    path = tmp_path / "original.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    writer = ObjectWriter(tmp_path)
    ref = ingest(path, writer)
    writer.db.commit()
    writer.close()
    loaded = thaw(ObjectReader(database(tmp_path)).value(ref))
    assert answer_fingerprint(loaded) == answer_fingerprint(record)
    assert type(loaded["predicted_media"][0]["start_s"]) is float
    assert math.copysign(1, loaded["predicted_media"][0]["offset"]) == -1
    assert json.dumps(loaded, sort_keys=True) == json.dumps(record, sort_keys=True)


def test_viewer_stats_order_and_individual_messages_do_not_read_trace_bodies(tmp_path, monkeypatch):
    from photobench.infrastructure.results import split_json

    fixture = state()
    item = fixture["items"][0]
    item["metrics_contract_version"] = 2
    item["wall_clock_ms"] = 1000
    item["agent_stability"] = {"structured_json_total": 4, "structured_json_success": 3}
    item["model_call_metrics"] = [
        {"call_type": "planner", "step_id": "p1", "duration_ms": 100},
        {"call_type": "agent", "step_id": "s1", "duration_ms": 200},
        {"call_type": "tool_internal", "duration_ms": 500},
    ]
    item["runtime_turns"][0]["trace_bundle"]["debug_calls"] = [
        {
            "call_type": "planner",
            "step_id": "p1",
            "seq": 0,
            "duration_ms": 100,
            "messages": [{"role": "user", "content": "题目"}],
        },
        {
            "call_type": "agent",
            "step_id": "s1",
            "seq": 1,
            "duration_ms": 200,
            "messages": [
                {"role": "system", "content": "大消息" * 50000},
                {"role": "user", "content": "题目"},
            ],
        },
        {"call_type": "agent", "step_id": "s2", "seq": 2, "duration_ms": 100},
    ]
    item["tool_trace"] = [
        {
            "tool": "search_memories",
            "parent_step_id": "s1",
            "duration_ms": 600,
            "result": {"text": "大输出" * 50000},
        }
    ]
    SplitResultWriter(tmp_path).write(tmp_path / "split-run", fixture)
    reader = SplitRunReader(tmp_path, tmp_path / "split-run")
    original_getitem = split_json.StoredDict.__getitem__

    def without_bodies(self, key):
        if key in {"process_diagnostics", "content", "response", "result"}:
            raise AssertionError(f"viewer read body: {key}")
        return original_getitem(self, key)

    monkeypatch.setattr(split_json.StoredDict, "__getitem__", without_bodies)
    core = reader.core(0, preview=True)
    assert core["trace_read_summary"] == {
        "model_call_count": 3,
        "tool_call_count": 1,
        "model_ms": 300,
        "tool_ms": 600,
        "timing_complete": True,
    }
    assert "process_diagnostics" not in core
    nodes = reader.nodes(0)["nodes"]
    assert [node["kind"] for node in nodes] == ["planner", "model", "tool", "model"]
    assert nodes[1]["message_roles"] == [{"index": 0, "role": "system"}, {"index": 1, "role": "user"}]
    # Walking one message uses its descriptor, independently of the large earlier message.
    content = reader.content(0, nodes[1]["node_id"], field="messages.1", limit=1024)
    assert json.loads(content["text"]) == {"role": "user", "content": "题目"}
    assert content["next_offset"] is None


def test_reused_stored_trace_keeps_node_records_as_references(tmp_path):
    SplitResultWriter(tmp_path).write(tmp_path / "source", state())
    inherited = SplitRunReader(tmp_path, tmp_path / "source").state()
    inherited["run_id"] = "resumed"
    SplitResultWriter(tmp_path).write(tmp_path / "resumed", inherited)
    reader = SplitRunReader(tmp_path, tmp_path / "resumed")
    node = reader.nodes(0)["nodes"][0]
    value = json.loads(reader.content(0, node["node_id"])["text"])
    assert value["messages"] == [{"role": "user", "content": "输入"}]
    assert "$photobench_object_v3" not in value
    assert json.loads(reader.content(0, node["node_id"], field="messages.0")["text"]) == value["messages"][0]


def test_old_descriptor_wrapped_nodes_read_without_rewriting_manifest(tmp_path, monkeypatch):
    from photobench.infrastructure.results.split_json import ObjectWriter
    from photobench.infrastructure.results.split_store import build_node_index, build_nodes

    fixture = state()
    SplitResultWriter(tmp_path).write(tmp_path / "source", fixture)
    reader = SplitRunReader(tmp_path, tmp_path / "source")
    original = reader.item_ref(0)
    objects = ObjectWriter(tmp_path)
    nodes = build_nodes(fixture["items"][0], objects)
    for node in nodes:
        node["record"] = dict(node["record"])
        node.pop("message_roles", None)
        node.pop("message_count", None)
    directory = build_node_index(nodes, objects)
    directory.pop("read_summary")
    directory.pop("order_version")
    ref = objects.put(directory)
    objects.db.commit()
    monkeypatch.setattr(reader, "item_ref", lambda index: {"item": original["item"], "nodes": ref})
    before = (tmp_path / "source" / "split-commit.json").read_bytes()
    node = reader.nodes(0)["nodes"][0]
    assert node["message_roles"] == [{"index": 0, "role": "user"}]
    assert json.loads(reader.content(0, node["node_id"], field="messages.0")["text"]) == {
        "role": "user",
        "content": "输入",
    }
    assert reader.trace_overview(0)["tool_call_count"] == 1
    assert (tmp_path / "source" / "split-commit.json").read_bytes() == before
    objects.close()
