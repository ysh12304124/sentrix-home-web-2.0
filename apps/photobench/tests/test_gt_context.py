"""Readable GT context stays frozen, typed and confined to answer-quality requests."""

import hashlib
import json

import pytest
from test_scoring_profiles import media, setup, state, task

from photobench.domain.datasets.qa_taxonomy import qa_fingerprint
from photobench.domain.evaluation.gt_context import (
    CAPTURE_INSTRUCTIONS,
    LEGACY_INSTRUCTIONS,
    capture_context,
    context_index,
    empty_context,
)
from photobench.domain.evaluation.profiles import ATM, OPEN, profile_configs, scoring_targets, valid_result
from photobench.infrastructure.datasets.albums import JsonAlbumSource


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("photobench.application.run_execution.judge.JUDGE_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("photobench.application.run_execution.judge.JUDGE_RETRY_ATTEMPTS", 1)


def mapping():
    return {
        **empty_context("album-x"),
        "annotation_version": "review-v12",
        "sha256": "mapping-content-sha",
        "entries": [
            {
                **media("album3/a.jpg"),
                "aliases": ["image:photos/a.jpg"],
                "metadata": {
                    "captured_at": "2018-12-30T13:48:50+08:00",
                    "readable_location": "河北省石家庄市赵县赵州桥",
                    "gps": {"latitude": 37.7201694},
                },
            },
            {
                **media("a", "video"),
                "metadata": {
                    "location_note": "来源照片锚点不代表全部视频帧场所",
                    "source_captures": [
                        {
                            "media_id": "source.jpg",
                            "captured_at": "2018-12-30",
                            "readable_location": "河北省石家庄市",
                            "gps": "raw-coordinate",
                        }
                    ],
                },
            },
        ],
    }


def test_typed_identity_explicit_alias_and_nested_prompt_whitelist():
    audit, attachment = capture_context(
        [media("photos/a.jpg"), media("a.mp4", "video"), media("a.jpg")], mapping(), "qa.answer_evidence"
    )
    assert audit["mapping_sha256"] == "mapping-content-sha"
    assert audit["materials"][0]["status"] == "available"
    assert audit["materials"][1]["status"] == "partial"
    assert audit["materials"][2]["status"] == "not_recorded"
    assert audit["materials"][2]["missing"] == ["拍摄时间未记录", "可读地点未记录"]
    assert "赵州桥" in attachment["text"] and "source.jpg" in attachment["text"]
    assert "37.7201694" not in attachment["text"] and "raw-coordinate" not in attachment["text"]
    assert "entry_sha256" not in attachment["text"]
    assert audit["materials"][0]["entry_sha256"]


@pytest.mark.parametrize("change", ["schema", "entry", "aliases", "collision", "empty_alias", "metadata"])
def test_invalid_mappings_fail_explicitly(change):
    value = mapping()
    if change == "schema":
        value["schema_version"] = "other"
    elif change == "entry":
        value["entries"].append(7)
    elif change == "aliases":
        value["entries"][0]["aliases"] = "image:bad"
    elif change == "collision":
        value["entries"].append({**media("other"), "aliases": ["image:photos/a.jpg"], "metadata": {}})
    elif change == "empty_alias":
        value["entries"][0]["aliases"] = ["image:"]
    else:
        value["entries"][0]["metadata"] = []
    with pytest.raises(ValueError):
        context_index(value)


def test_loader_freezes_raw_content_hash_and_rejects_broken_config(tmp_path):
    root = tmp_path / "context"
    source = JsonAlbumSource(tmp_path, tmp_path / "policy.json", root)
    assert source.gt_metadata_snapshot("album-x")["status"] == "not_recorded"
    root.mkdir()
    path = root / "album-x.json"
    raw = json.dumps(mapping(), ensure_ascii=False).encode()
    path.write_bytes(raw)
    assert source.gt_metadata_snapshot("album-x")["sha256"] == hashlib.sha256(raw).hexdigest()
    path.write_text("{ broken")
    with pytest.raises(ValueError, match="GT拍摄资料映射读取失败"):
        source.gt_metadata_snapshot("album-x")
    path.write_text(json.dumps({**empty_context("another-album")}))
    with pytest.raises(ValueError):
        source.gt_metadata_snapshot("album-x")


def test_image_bundle_default_and_legacy_flags_have_separate_protocols():
    config = profile_configs([OPEN])[OPEN]
    assert config["gt_metadata_mode"] == "image_metadata_v1"
    assert config["use_gt_metadata"] is False
    enabled = profile_configs([OPEN], {OPEN: {"use_gt_images": True}})[OPEN]
    assert enabled["use_gt_metadata"] is True
    for flag in (False, True):
        explicit = profile_configs(
            [OPEN],
            {
                OPEN: {
                    "use_gt_images": flag,
                    "use_gt_metadata": not flag,
                    "gt_metadata_mode": "image_metadata_v1",
                }
            },
        )[OPEN]
        assert explicit["use_gt_metadata"] is flag
    for flag in (False, True):
        old = profile_configs([OPEN], {OPEN: {"use_gt_metadata": flag}})[OPEN]
        assert old["use_gt_metadata"] is flag and "gt_metadata_mode" not in old
    new = profile_configs([OPEN], {OPEN: {"use_gt_metadata": False, "gt_metadata_mode": "readable_v1"}})[OPEN]
    assert new["use_gt_metadata"] is True


def readable_setup(saved, profiles, replies=(), configs=None):
    return setup(
        saved,
        profiles,
        replies,
        configs=configs or {profile: {"gt_metadata_mode": "readable_v1"} for profile in profiles},
    )


def with_evidence(scorer="open_judge", **definition):
    saved = state(scorer, **definition)
    saved["qa_snapshot"]["rows"][0]["answer_evidence_media_refs"] = [media("album3/a.jpg")]
    saved["qa_snapshot"]["definition_sha256"] = qa_fingerprint(saved["qa_snapshot"]["rows"])
    saved["gt_metadata_snapshot"] = mapping()
    return saved


def test_actual_judge_request_contains_frozen_readable_context_without_image_or_gps():
    saved = with_evidence()
    svc, batches, http, receipts = readable_setup(saved, [OPEN], [task(), {"score": 2, "reason": "正确"}])
    saved["gt_metadata_snapshot"]["entries"][0]["metadata"]["readable_location"] = "later-edit"
    result = svc.score(batches[0], scoring_targets(saved, batches[0], "all")[0])
    assert valid_result(result)
    assert "赵州桥" not in json.dumps(http.calls[0], ensure_ascii=False)  # shared behavior diagnostic
    actual = json.dumps(http.calls[1], ensure_ascii=False)
    assert "赵州桥" in actual and "2018-12-30T13:48:50+08:00" in actual
    assert "later-edit" not in actual and "37.7201694" not in actual and "image_url" not in actual
    frozen = batches[0]["materials"]["q:0"]["metadata"]
    assert frozen["annotation_version"] == "review-v12"
    assert receipts[-1]["scoring_results"][result["key"]]["requests"][-1]["input"] == http.calls[1]


def test_changed_mapping_creates_new_batch_and_legacy_batch_id_stays_unchanged():
    from photobench.application.run_execution.profile_scoring import prepare_batches

    saved = with_evidence()
    _svc, first, _http, _ = readable_setup(saved, [OPEN])
    saved["gt_metadata_snapshot"]["entries"][0]["metadata"]["readable_location"] = "另一个审核地点"
    second = prepare_batches(
        saved,
        profile_configs([OPEN], {OPEN: {"gt_metadata_mode": "readable_v1"}}),
        {"url": "http://judge/v1", "model": "j"},
    )
    assert first[0]["batch_id"] != second[0]["batch_id"]
    legacy = {OPEN: {"use_gt_metadata": False}}
    _, a, _, _ = readable_setup(saved, [OPEN], configs=legacy)
    saved["gt_metadata_snapshot"] = empty_context("album-x")
    _, b, _, _ = readable_setup(saved, [OPEN], configs=legacy)
    assert a[0]["batch_id"] == b[0]["batch_id"] and "gt_metadata_snapshot" not in a[0]


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_capture_fields_keep_the_frozen_instruction_protocol(legacy):
    saved = with_evidence()
    saved["gt_metadata_snapshot"] = empty_context("album-x")
    svc, batches, http, _ = readable_setup(saved, [OPEN], [task(), {"score": 1, "reason": "核对答案"}])
    batch = batches[0]
    if legacy:
        batch.pop("gt_metadata_instructions")
        batch["rule_version"] = "scoring-2026.11.06-v2"
    result = svc.score(batch, scoring_targets(saved, batch, "all")[0])
    assert valid_result(result)
    context = http.calls[1]["messages"][1]["content"][-1]["text"]
    expected = LEGACY_INSTRUCTIONS if legacy else CAPTURE_INSTRUCTIONS
    assert context.startswith(expected + "[")
    assert result["gt_metadata"]["materials"][0]["status"] == "not_recorded"
    if not legacy:
        assert "不能据此认定媒体不存在" in context
        assert batch["gt_metadata_instructions"] == CAPTURE_INSTRUCTIONS


def test_changed_capture_instructions_create_a_batch_without_changing_existing_inputs(monkeypatch):
    from photobench.application.run_execution.profile_scoring import prepare_batches

    saved = with_evidence()
    _, first, _, _ = readable_setup(saved, [OPEN])
    monkeypatch.setattr("photobench.domain.evaluation.gt_context.CAPTURE_INSTRUCTIONS", "future instructions")
    second = prepare_batches(
        saved,
        profile_configs([OPEN], {OPEN: {"gt_metadata_mode": "readable_v1"}}),
        {"url": "http://judge/v1", "model": "j"},
    )
    assert first[0]["gt_metadata_instructions"] == CAPTURE_INSTRUCTIONS
    assert second[0]["gt_metadata_instructions"] == "future instructions"
    assert first[0]["batch_id"] != second[0]["batch_id"]


@pytest.mark.parametrize("mode", ["readable_v1", "image_metadata_v1"])
def test_resume_batch_does_not_read_current_mapping(tmp_path, monkeypatch, mode):
    from test_suites import FakeRunStore, _suites

    saved = with_evidence()
    suites = _suites(tmp_path, store=FakeRunStore({"r": saved}))
    monkeypatch.setattr(suites._albums, "gt_metadata_snapshot", lambda album: mapping())
    saved["sentrix_url"] = suites._config.sentrix_url
    batches, _, _, _ = suites._profile_request(
        saved,
        {
            "scoring_profiles": [OPEN],
            "scoring_configs": {OPEN: {"gt_metadata_mode": mode, "use_gt_images": True}},
        },
    )

    def forbidden(album):
        raise AssertionError("Resume must use frozen mapping")

    monkeypatch.setattr(suites._albums, "gt_metadata_snapshot", forbidden)
    resumed, _, _, _ = suites._profile_request(saved, {"resume_batch_ids": [batches[0]["batch_id"]]})
    assert resumed[0]["gt_metadata_snapshot"] == batches[0]["gt_metadata_snapshot"]


@pytest.mark.parametrize("mode", ["readable_v1", "image_metadata_v1"])
def test_new_run_freezes_mapping_and_agent_resume_keeps_original(monkeypatch, mode):
    import copy
    from dataclasses import replace

    from test_run_execution import _execution, _manifest, _request

    execution, *_ = _execution()
    current = mapping()
    monkeypatch.setattr(execution.albums, "gt_metadata_snapshot", lambda album: copy.deepcopy(current))
    request = replace(
        _request(),
        scoring_configs=profile_configs([OPEN], {OPEN: {"gt_metadata_mode": mode, "use_gt_images": True}}),
    )
    original = execution._initial_state("r", request, _manifest(), "scope", "album-x")
    current["entries"][0]["metadata"]["readable_location"] = "later change"
    frozen = original["gt_metadata_snapshot"]
    assert frozen["entries"][0]["metadata"]["readable_location"] == "河北省石家庄市赵县赵州桥"

    def forbidden(album):
        raise AssertionError("Agent resume must retain its original context")

    monkeypatch.setattr(execution.albums, "gt_metadata_snapshot", forbidden)
    resumed = execution._initial_state(
        "r2",
        replace(
            request,
            frozen_qa_snapshot=original["qa_snapshot"],
            prior_scoring={"gt_metadata_snapshot": frozen},
        ),
        _manifest(),
        "scope",
        "album-x",
    )
    assert resumed["gt_metadata_snapshot"] == frozen


@pytest.mark.parametrize("mode", ["readable_v1", "image_metadata_v1"])
def test_atm_choice_is_blind_and_list_does_not_add_a_judge_call(mode):
    saved = with_evidence(
        "choice_judge",
        options=[{"id": "A", "text": "2000米"}, {"id": "B", "text": "3000米"}],
        correct_option="A",
    )
    row = saved["qa_snapshot"]["rows"][0]
    reply = {
        "status": "matched",
        "matched_option": "A",
        "reason": "matched",
        "comparisons": [{"option_id": i, "matches": i == "A", "reason": "对照"} for i in ("A", "B")],
    }
    svc, batches, http, _ = setup(
        saved, [ATM], [task(), reply], configs={ATM: {"use_gt_images": True, "gt_metadata_mode": mode}}
    )
    result = svc.score(batches[0], scoring_targets(saved, batches[0], "all")[0])
    assert valid_result(result)
    assert all(
        "赵州桥" not in json.dumps([m for m in call["messages"] if m["role"] == "user"], ensure_ascii=False)
        for call in http.calls
    )
    row["evaluation"].update(scorer="atm_list", target_media_refs=[media("album3/a.jpg")])
    row["retrieval_media_refs"] = [media("album3/a.jpg")]
    svc, batches, http, _ = setup(
        saved, [ATM], [], configs={ATM: {"use_gt_images": True, "gt_metadata_mode": mode}}
    )
    result = svc.score(batches[0], scoring_targets(saved, batches[0], "all")[0])
    assert valid_result(result)
    assert http.calls == []  # same shared diagnostic already cached


def bundle_fixture(profile, scorer="open_judge", *, enabled=True, replies=None):
    saved = with_evidence(scorer)
    row = saved["qa_snapshot"]["rows"][0]
    row["answer_evidence_media_refs"] += [media("b.jpg"), media("a", "video")]
    saved["gt_metadata_snapshot"]["entries"].append(
        {
            **media("b.jpg"),
            "metadata": {"captured_at": "2020-01-02T10:00:00+08:00", "readable_location": "北京市海淀区"},
        }
    )
    svc, batches, http, receipts = setup(
        saved,
        [profile],
        replies or [],
        configs={
            profile: {"use_gt_images": enabled, "gt_metadata_mode": "image_metadata_v1"},
        },
    )
    svc.assets = {
        name: [{"id": name, "file_name": name, "media_type": "image"}] for name in ("a.jpg", "b.jpg")
    }
    svc.runner.inline_images = lambda url, refs, assets: [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + refs[0]["asset_id"]},
        }
    ]
    return saved, svc, batches[0], http, receipts


@pytest.mark.parametrize(
    "profile,scorer,reply",
    [
        (OPEN, "open_judge", {"score": 2, "reason": "正确"}),
        (ATM, "open_judge", {"accuracy": True, "reason": "正确"}),
        (ATM, "atm_number", {"score": 1, "reason": "等价"}),
    ],
)
def test_one_switch_pairs_each_image_with_its_own_frozen_capture_fields(profile, scorer, reply):
    saved, svc, batch, http, receipts = bundle_fixture(profile, scorer, replies=[task(), reply])
    # A later annotation edit must not change the request or a retry's materials.
    saved["gt_metadata_snapshot"]["entries"][0]["metadata"]["readable_location"] = "later edit"
    result = svc.score(batch, scoring_targets(saved, batch, "all")[0])
    assert valid_result(result), result
    assert len(http.calls) == 2
    content = http.calls[-1]["messages"][1]["content"]
    pairs = [(content[i - 1], part) for i, part in enumerate(content) if part.get("type") == "image_url"]
    assert len(pairs) == 2
    for (label, image), name, date, place in zip(
        pairs,
        ["album3/a.jpg", "b.jpg"],
        ["2018-12-30T13:48:50+08:00", "2020-01-02T10:00:00+08:00"],
        ["河北省石家庄市赵县赵州桥", "北京市海淀区"],
        strict=True,
    ):
        data = json.loads(label["text"].split("：", 1)[1])
        assert data["reference"] == media(name)
        assert data["metadata"] == {"captured_at": date, "readable_location": place}
        audit = next(ref for ref in result["gt_images"]["references"] if ref["media_id"] == name)
        assert (
            audit["attached_data_url_sha256"]
            == hashlib.sha256(image["image_url"]["url"].encode()).hexdigest()
        )
    wire = json.dumps(http.calls[-1], ensure_ascii=False)
    assert "later edit" not in wire and "37.7201694" not in wire and "source.jpg" not in wire
    assert result["gt_metadata"]["mode"] == "image_metadata_v1"
    assert "赵州桥" not in json.dumps(http.calls[0], ensure_ascii=False)
    assert receipts[-1]["scoring_results"][result["key"]]["requests"][-1]["input"] == http.calls[-1]
    # Simulate a failed score after the materials were durably saved; recover using the same batch.
    saved["scoring_results"][result["key"]].update(status="failed", score=None)
    batch["gt_metadata_snapshot"] = {"broken": True}
    svc.runner.inline_images = lambda *_: pytest.fail("retry must use saved image materials")
    http.replies = iter([reply])
    retried = svc.score(batch, scoring_targets(saved, batch, "missing")[0])
    assert valid_result(retried)
    assert http.calls[-1] == http.calls[-2]


@pytest.mark.parametrize("enabled", [False, True])
def test_bundle_switch_and_missing_fields(enabled):
    saved, svc, batch, http, _ = bundle_fixture(OPEN, enabled=enabled, replies=[task(), {"score": 1}])
    if enabled:
        batch["gt_metadata_snapshot"] = empty_context("album-x")
    else:
        assert "gt_metadata_snapshot" not in batch
        saved["gt_metadata_snapshot"] = {"broken": True}
        svc.runner.inline_images = lambda *_: pytest.fail("disabled GT must not read images")
    result = svc.score(batch, scoring_targets(saved, batch, "all")[0])
    assert valid_result(result), result
    wire = json.dumps(http.calls[-1], ensure_ascii=False)
    if enabled:
        assert "拍摄时间未记录" in wire and "可读地点未记录" in wire
        assert len(result["gt_metadata"]["materials"]) == 2
    else:
        assert result["gt_metadata"] is None
        assert "image_url" not in wire and "赵州桥" not in wire and "2018-12-30" not in wire


@pytest.mark.parametrize("problem", ["mapping", "image"])
def test_required_bundle_failure_is_unscored(problem):
    saved, svc, batch, http, _ = bundle_fixture(OPEN, replies=[task()])
    if problem == "mapping":
        batch["gt_metadata_snapshot"] = {"broken": True}
    else:
        svc.runner.inline_images = lambda *_: []
    result = svc.score(batch, scoring_targets(saved, batch, "all")[0])
    assert result["status"] == "failed" and result["score"] is None
    assert len(http.calls) == 1  # Only the shared diagnostic, no answer-quality request.


def test_disabled_new_run_and_rejudge_do_not_load_capture_mapping(monkeypatch, tmp_path):
    from dataclasses import replace

    from test_run_execution import _execution, _manifest, _request
    from test_suites import FakeRunStore, _suites

    def forbidden(album):
        raise AssertionError("Disabled GT must not load metadata")

    execution, *_ = _execution()
    monkeypatch.setattr(execution.albums, "gt_metadata_snapshot", forbidden)
    request = replace(_request(), scoring_configs=profile_configs([OPEN]))
    created = execution._initial_state("r", request, _manifest(), "scope", "album-x")
    assert "gt_metadata_snapshot" not in created
    saved = with_evidence()
    suites = _suites(tmp_path, store=FakeRunStore({"r": saved}))
    monkeypatch.setattr(suites._albums, "gt_metadata_snapshot", forbidden)
    batches, _, _, _ = suites._profile_request(saved, {"scoring_profiles": [OPEN]})
    assert "gt_metadata_snapshot" not in batches[0]
