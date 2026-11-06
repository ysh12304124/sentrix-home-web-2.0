"""Batch strict grammar masks while keeping RNG and grammar request-local.

The model forward and MRoPE routing follow vllm-mlx 0.6.0 (Apache-2.0).
Filters retain mlx-lm 0.31.3 order; random draws retain each request's key and
single-row shape. No grammar state or random generator is shared across rows.
"""

from __future__ import annotations

import importlib
from typing import Any

COUNTERS: dict[str, int] = {
    "grammar_batches": 0,
    "grammar_rows": 0,
    "grammar_batch_peak": 0,
    "strided_kv_calls": 0,
    "strided_kv_fallbacks": 0,
}


def install_attention_bridge(language: Any) -> None:
    """Resolve the renamed 0.7.6 helper before vllm-mlx captures its legacy name.

    Without this bridge, the compatibility call clears the left-padding marker
    and falls back to unmasked attention. Keep the native helper and its fallback.
    """
    if getattr(language.Qwen3_5Attention, "_batch_patched", False):
        raise RuntimeError("attention bridge must precede the vllm-mlx compatibility patch")
    helper = language._qwen3_5_left_padded_attention
    existing = getattr(language, "_target_verify_left_padded_attention", None)
    if existing is not None and existing is not helper:
        raise RuntimeError("unexpected legacy Qwen attention helper")
    language._target_verify_left_padded_attention = helper


def install_kv_observer(language: Any) -> None:
    original = language._qwen3_5_ragged_decode_attention

    def attention(*args: Any, **kwargs: Any) -> Any:
        output = original(*args, **kwargs)
        key = "strided_kv_fallbacks" if output is None else "strided_kv_calls"
        COUNTERS[key] += 1
        return output

    language._qwen3_5_ragged_decode_attention = attention


class GrammarBatch:
    def __init__(self) -> None:
        self.module: Any = importlib.import_module("vllm_mlx.constrained.llguidance_schema_processor")
        ll: Any = importlib.import_module("llguidance")
        self.numpy: Any = importlib.import_module("llguidance.numpy")
        self.mlx: Any = importlib.import_module("llguidance.mlx")
        self.executor = ll.LLExecutor(4)

    def apply(self, processors: list[Any], histories: list[list[int]], logits: Any) -> Any:
        if len({id(processor._matcher) for processor in processors}) != len(processors):
            raise ValueError("duplicate request grammar state in one decoding batch")
        COUNTERS["grammar_batches"] += 1
        COUNTERS["grammar_rows"] += len(processors)
        COUNTERS["grammar_batch_peak"] = max(COUNTERS["grammar_batch_peak"], len(processors))
        vocab = processors[0]._vocab_size
        if any(processor._vocab_size != vocab for processor in processors):
            raise ValueError("different grammar vocabularies in one decoding batch")
        # A fresh CPU buffer is retained by the MLX copy until evaluation. Never
        # reuse a mask while a prior lazy Metal operation can still reference it.
        mask = self.numpy.allocate_token_bitmask(len(processors), vocab)
        active = []
        suffixes = []
        for row, (processor, tokens) in enumerate(zip(processors, histories, strict=True)):
            if processor._prompt_len is None:
                processor._prompt_len = len(tokens)
            suffix = tokens[processor._prompt_len :]
            processor._consume_suffix(suffix)
            suffixes.append(suffix)
            if processor._terminal:
                mask[row, :] = 0
                for token in processor._eos_tokens:
                    if token < vocab:
                        word, bit = divmod(token, 32)
                        # Unsigned view avoids signed-int overflow for bit 31.
                        mask.view("uint32")[row, word] |= 1 << bit
            else:
                active.append((processor._matcher, row))
        try:
            if active:
                self.numpy.fill_next_token_bitmask_par(self.executor, active, mask)
            for row, (processor, suffix) in enumerate(zip(processors, suffixes, strict=True)):
                if processor._terminal:
                    continue
                decoded = processor._tokenizer.decode(
                    suffix, skip_special_tokens=True, clean_up_tokenization_spaces=False
                )
                if (
                    self.module._trailing_json_whitespace(decoded)
                    >= self.module._MAX_NONPROGRESS_WHITESPACE_CHARS
                ):
                    for token in processor._whitespace_tokens:
                        self.module._clear_mask_token(mask[row : row + 1], token)
            return self.mlx.apply_token_bitmask(logits, mask)
        except Exception as error:
            raise self.module.ConstrainedDecodingError(
                f"failed to construct batched strict JSON Schema token mask: {error}"
            ) from error


def process_rows(
    logits: Any, processors: list[Any], histories: list[list[int]], grammar: GrammarBatch
) -> Any:
    """Preserve each row's processor order, including interleaved custom processors."""
    mx: Any = importlib.import_module("mlx.core")
    rows = [logits[row : row + 1] for row in range(int(logits.shape[0]))]
    chains = [chain or [] for chain in processors]
    for position in range(max(map(len, chains), default=0)):
        grammar_rows = []
        for row, chain in enumerate(chains):
            if position >= len(chain):
                continue
            processor = chain[position]
            if type(processor) is grammar.module.LLGuidanceJSONSchemaLogitsProcessor:
                grammar_rows.append(row)
            else:
                rows[row] = processor(mx.array(histories[row]), rows[row])
        if grammar_rows:
            masked = grammar.apply(
                [chains[row][position] for row in grammar_rows],
                [histories[row] for row in grammar_rows],
                mx.concatenate([rows[row] for row in grammar_rows], axis=0),
            )
            for index, row in enumerate(grammar_rows):
                rows[row] = masked[index : index + 1]
    return mx.concatenate(rows, axis=0)


def install_batched_decode() -> None:
    module: Any = importlib.import_module("vllm_mlx.mllm_batch_generator")
    mx = module.mx
    grammar = GrammarBatch()

    def step(
        instance: Any,
        input_tokens: Any,
        cache: Any,
        logits_processors: Any = None,
        output_tokens: Any = None,
        samplers: Any = None,
        rope_deltas: Any = None,
    ) -> Any:
        if input_tokens.ndim == 1:
            input_tokens = input_tokens[:, None]
        kwargs = {}
        if rope_deltas is not None:
            kwargs["rope_deltas"] = rope_deltas
        elif (
            instance.active_batch is not None
            and instance.active_batch.decode_rope_deltas is not None
            and len(instance.active_batch) == int(input_tokens.shape[0])
        ):
            kwargs["rope_deltas"] = instance.active_batch.decode_rope_deltas
        output = instance.language_model(input_tokens, cache=cache, **kwargs)
        logits = output.logits if hasattr(output, "logits") else output
        logits = logits[:, -1, :]
        if logits_processors and output_tokens and any(logits_processors):
            logits = process_rows(logits, logits_processors, output_tokens, grammar)
        logprobs = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        if samplers and any(samplers):
            # Whole-batch filters regressed on M4 in the differential benchmark.
            # Retain the original per-request sampler and its independent key.
            sampled = mx.concatenate(
                [
                    (samplers[row] or instance.sampler)(logprobs[row : row + 1])
                    for row in range(int(logprobs.shape[0]))
                ],
                axis=0,
            )
        else:
            sampled = instance.sampler(logprobs)
        return sampled, list(logprobs)

    module.MLLMBatchGenerator._step = step
