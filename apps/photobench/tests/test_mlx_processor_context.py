"""The chunked decoder must include its pending token in each request's grammar state."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("serve_mlx", ROOT / "scripts/benchmarks/serve_mlx.py")
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_chunked_context_includes_current_token_without_mutating_request_history():
    requests = [SimpleNamespace(output_tokens=[]), SimpleNamespace(output_tokens=[7])]
    original = [request.output_tokens for request in requests]
    result = adapter.processor_context(requests, original, [[90], [12]])
    assert result == [[90], [7, 12]]
    assert original == [[], [7]]


def test_normal_decoder_context_is_not_duplicated_and_no_processors_stay_absent():
    requests = [SimpleNamespace(output_tokens=[90])]
    complete = [[90, 4754]]
    assert adapter.processor_context(requests, complete, [[4754]]) == complete
    assert adapter.processor_context(requests, None, [[4754]]) is None


def test_waiting_media_drains_existing_batch_before_admitting_new_text():
    image = SimpleNamespace(uid=0, images=["image"], videos=[], audio=[])
    text = SimpleNamespace(uid=1, images=[], videos=[], audio=[])
    generator = SimpleNamespace(
        unprocessed_requests=[image, text],
        active_batch=["decoding"],
        _partial=None,
        _allow_mid_batch_extend=True,
        prefill_batch_size=4,
        completion_batch_size=16,
    )
    calls = []

    def chunked():
        assert generator._allow_mid_batch_extend is False
        calls.append("drain")
        generator.active_batch = None

    def canonical():
        assert generator.unprocessed_requests == [image, text]
        calls.append("media_batch")
        generator.unprocessed_requests = []

    adapter.fair_generation_step(generator, chunked, canonical)
    adapter.fair_generation_step(generator, chunked, canonical)
    assert calls == ["drain", "media_batch"]
    assert generator._allow_mid_batch_extend is True


def test_media_canonical_admission_honors_prefill_limit_and_restores_decode_capacity():
    """The ordinary 0.6 path selects completion_batch_size even for vision prefill."""
    import pytest

    requests = [SimpleNamespace(uid=i, images=["frame"], videos=[], audio=[]) for i in range(14)]
    generator = SimpleNamespace(
        unprocessed_requests=requests,
        active_batch=None,
        _partial=None,
        _allow_mid_batch_extend=True,
        prefill_batch_size=4,
        completion_batch_size=16,
    )
    admitted = []

    def canonical():
        admitted.extend(generator.unprocessed_requests[: generator.completion_batch_size])
        raise RuntimeError("qualification failure")

    with pytest.raises(RuntimeError, match="qualification failure"):
        adapter.fair_generation_step(generator, lambda: pytest.fail("media bypassed"), canonical)
    assert admitted == requests[:4]
    assert generator.completion_batch_size == 16
    assert generator._allow_mid_batch_extend is True
    assert generator.unprocessed_requests == requests


def test_media_does_not_discard_partial_prefill_and_flags_restore_on_error():
    import pytest

    partial = {"remaining": 512}
    generator = SimpleNamespace(
        unprocessed_requests=[SimpleNamespace(images=["image"], videos=[], audio=[])],
        active_batch=None,
        _partial=partial,
        _allow_mid_batch_extend=True,
    )

    def chunked():
        assert generator._partial is partial
        raise RuntimeError("prefill failed")

    with pytest.raises(RuntimeError, match="prefill failed"):
        adapter.fair_generation_step(generator, chunked, lambda: pytest.fail("discarded partial"))
    assert generator._allow_mid_batch_extend is True


def test_text_only_keeps_continuous_batching_and_queue_counts_pending_media():
    generator = SimpleNamespace(
        unprocessed_requests=[SimpleNamespace(images=[], videos=[], audio=[])],
        active_batch=["decoding"],
        _partial=None,
        _allow_mid_batch_extend=True,
    )
    assert adapter.fair_generation_step(generator, lambda: "chunked", lambda: "wrong") == "chunked"
    assert generator._allow_mid_batch_extend is True
    scheduler = SimpleNamespace(waiting=["waiting"], batch_generator=generator)
    assert adapter.pending_requests(scheduler) == 2


def test_waiting_media_is_admitted_despite_continuously_replenished_text_queue():
    """0.6 insert() sorts text before media, even after the active batch drains."""
    older_text = SimpleNamespace(uid=0, images=[], videos=[], audio=[])
    image = SimpleNamespace(uid=1, images=["image"], videos=[], audio=[])
    generator = SimpleNamespace(
        unprocessed_requests=[older_text, image],
        active_batch=None,
        _partial=None,
        _allow_mid_batch_extend=True,
        prefill_batch_size=4,
        completion_batch_size=16,
    )
    admitted = []

    def canonical():
        selected = generator.unprocessed_requests[: generator.completion_batch_size]
        admitted.extend(request.uid for request in selected)
        generator.unprocessed_requests = generator.unprocessed_requests[len(selected) :]

    for cycle in range(4):
        new_text = [SimpleNamespace(uid=2 + cycle * 8 + j, images=[], videos=[], audio=[]) for j in range(8)]
        generator.unprocessed_requests.extend(new_text)
        # The upstream insert() modality sort is stable, but it is not FIFO.
        generator.unprocessed_requests.sort(key=lambda request: bool(request.images))
        adapter.fair_generation_step(generator, canonical, canonical)

    assert admitted[:4] == [0, 1, 2, 3]
    assert len(admitted) == len(set(admitted))
    assert sorted(admitted + [r.uid for r in generator.unprocessed_requests]) == list(range(34))
    assert generator.completion_batch_size == 16
    assert generator._allow_mid_batch_extend is True


def test_terminal_evidence_keeps_disagreeing_counters_instead_of_normalizing_them():
    request = SimpleNamespace(request_id="r", num_prompt_tokens=275, num_output_tokens=150)
    output = SimpleNamespace(request_id="r", prompt_tokens=275, completion_tokens=144, finished=True)
    facts = adapter.terminal_output_facts(request, [output])
    assert facts["output_counters_consistent"] is False
    assert facts["returned_completion_tokens"] == 144
    assert request.num_output_tokens == 150 and output.completion_tokens == 144
    output.completion_tokens = 150
    assert adapter.terminal_output_facts(request, [output])["output_counters_consistent"] is True
    assert adapter.terminal_output_facts(request, [output, output])["output_counters_consistent"] is False


def test_inline_prefill_admits_short_request_once_while_preserving_other_waiters():
    short = SimpleNamespace(uid=7, short=True)
    waiting = SimpleNamespace(uid=8, short=False)
    generator = SimpleNamespace(unprocessed_requests=[short, waiting])
    admissions = []
    for _ in range(3):
        selected = [request for request in generator.unprocessed_requests if request.short]
        if selected:
            result = adapter.process_queued_prompts(
                generator, selected, lambda selected=selected: admissions.extend(selected) or "batch"
            )
            assert result == "batch"
    assert admissions == [short]
    assert generator.unprocessed_requests == [waiting]


def test_prefill_consumes_explicit_failures_even_when_processor_mutates_input_list():
    failed, waiting = SimpleNamespace(uid=7), SimpleNamespace(uid=8)
    selected = [failed]
    generator = SimpleNamespace(unprocessed_requests=[failed, waiting])

    def fail_input():
        selected.remove(failed)
        return None

    assert adapter.process_queued_prompts(generator, selected, fail_input) is None
    assert generator.unprocessed_requests == [waiting]


def test_prefill_exception_keeps_original_caller_error_handling():
    import pytest

    request = SimpleNamespace(uid=7)
    generator = SimpleNamespace(unprocessed_requests=[request])

    def fail_batch():
        raise RuntimeError("prefill failed")

    with pytest.raises(RuntimeError, match="prefill failed"):
        adapter.process_queued_prompts(generator, [request], fail_batch)
    assert generator.unprocessed_requests == [request]


def test_canonical_text_extension_bounds_new_admissions_without_reducing_decode_batch(monkeypatch):
    """Reproduce ordinary-path selection: 13 waiters joining two active requests."""

    class Generator:
        def _next(self):
            selected = self.unprocessed_requests[: self.completion_batch_size]
            self.active_batch.extend(selected)
            self.unprocessed_requests = self.unprocessed_requests[len(selected) :]
            return selected, list(self.active_batch)

    monkeypatch.setattr(
        adapter.importlib, "import_module", lambda _: SimpleNamespace(MLLMBatchGenerator=Generator)
    )
    adapter.install_canonical_admission_fix()
    generator = Generator()
    generator.active_batch = ["long", "short"]
    generator.unprocessed_requests = list(range(13))
    generator.completion_batch_size = 16
    generator.prefill_batch_size = 4
    selected, decoded = generator._next()
    assert selected == [0, 1, 2, 3]
    assert decoded == ["long", "short", 0, 1, 2, 3]
    assert generator.unprocessed_requests == list(range(4, 13))
    assert generator.completion_batch_size == 16
    while generator.unprocessed_requests:
        selected, decoded = generator._next()
        assert len(selected) <= 4
    assert decoded == ["long", "short", *range(13)]
    generator.unprocessed_requests = ["last", "waiting"]
    selected, decoded = generator._next()
    assert selected == ["last"] and len(decoded) == 16
    selected, decoded = generator._next()
    assert selected == [] and len(decoded) == 16
    assert generator.unprocessed_requests == ["waiting"]


def test_canonical_idle_admission_restores_capacity_even_on_exception():
    import pytest

    generator = SimpleNamespace(active_batch=None, completion_batch_size=16, prefill_batch_size=4)

    def canonical():
        assert generator.completion_batch_size == 4
        raise RuntimeError("admission failed")

    with pytest.raises(RuntimeError, match="admission failed"):
        adapter.canonical_generation_step(generator, canonical)
    assert generator.completion_batch_size == 16


def test_json_instruction_matches_enforced_nonthinking_mode_even_with_reasoning_parser():
    calls = []
    schema = object()

    def builder(response_format, *, thinking_model=False):
        calls.append((response_format, thinking_model))
        return "strict JSON" if not thinking_model else "may reason first"

    server = SimpleNamespace(build_json_system_prompt=builder)
    adapter.install_nonthinking_json_fix(server)
    assert server.build_json_system_prompt(schema, thinking_model=True) == "strict JSON"
    assert server.build_json_system_prompt(schema) == "strict JSON"
    assert calls == [(schema, False), (schema, False)]
