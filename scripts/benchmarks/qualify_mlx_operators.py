"""Real MLX/llguidance differential checks; no model inference or QA scores.

Run in the engine environment with --tokenizer pointing at the frozen model.
The baseline is the unchanged row-wise sampler/grammar, not a mock.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from scripts.benchmarks.mlx_batch_decode import GrammarBatch, process_rows


def timed(mx: Any, operation: Any, repeats: int = 12) -> float:
    for _ in range(3):
        mx.eval(operation())
    measurements = []
    for _ in range(repeats):
        start = time.perf_counter()
        mx.eval(operation())
        measurements.append(time.perf_counter() - start)
    return statistics.median(measurements)


def qualify(tokenizer_path: Path) -> dict[str, Any]:
    mx: Any = importlib.import_module("mlx.core")
    transformers: Any = importlib.import_module("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(str(tokenizer_path), local_files_only=True)
    grammar = GrammarBatch()
    cls = grammar.module.LLGuidanceJSONSchemaLogitsProcessor
    result: dict[str, Any] = {"device": mx.device_info(), "grammar": []}
    mx.random.seed(1234)
    for size in (4, 8, 12, 16):
        schemas = [
            {
                "type": "object",
                "properties": {"marker": {"const": f"row_{i}"}, "count": {"const": i}},
                "required": ["marker", "count"],
                "additionalProperties": False,
            }
            for i in range(size)
        ]
        before = [cls(schema, tokenizer) for schema in schemas]
        after = [cls(schema, tokenizer) for schema in schemas]
        answers = [
            tokenizer.encode(json.dumps({"marker": f"row_{i}", "count": i}), add_special_tokens=False)
            for i in range(size)
        ]
        values = mx.random.normal((size, 248320))
        comparisons = 0
        for step in range(max(map(len, answers)) + 2):
            rows = list(reversed(range(size))) if step % 2 else list(range(size))
            if step % 3 == 0 and step:
                rows = rows[::2]
            histories = [
                [123] + answers[row][:step] + ([tokenizer.eos_token_id] if step > len(answers[row]) else [])
                for row in rows
            ]
            expected = mx.concatenate(
                [before[row](mx.array(histories[i]), values[row : row + 1]) for i, row in enumerate(rows)]
            )
            actual = grammar.apply(
                [after[row] for row in rows],
                histories,
                mx.concatenate([values[row : row + 1] for row in rows]),
            )
            if not mx.array_equal(expected, actual).item():
                raise AssertionError(f"grammar parity failed at B{size} step{step}")
            comparisons += len(rows)
        result["grammar"].append(
            {
                "batch": size,
                "mask_row_comparisons_bit_equal": comparisons,
                "reorder_and_removal": True,
                "eos": True,
            }
        )
        print({"grammar": result["grammar"][-1]}, flush=True)

    # Mixed custom/grammar chains must retain row-specific ordering.
    before = [cls(schemas[i], tokenizer) for i in range(2)]
    after = [cls(schemas[i], tokenizer) for i in range(2)]

    def penalty(tokens: Any, logits: Any) -> Any:
        return logits - len(tokens)

    chains = [[penalty, after[0]], [after[1], penalty], None]
    histories = [[123], [123], [123]]
    values = mx.random.normal((3, 248320))
    expected = mx.concatenate(
        [
            before[0](mx.array([123]), penalty([123], values[:1])),
            penalty([123], before[1](mx.array([123]), values[1:2])),
            values[2:3],
        ]
    )
    actual = process_rows(values, chains, histories, grammar)
    assert mx.array_equal(expected, actual).item()
    result["mixed_processor_order"] = True
    try:
        grammar.apply([after[0], after[0]], [[123], [123]], values[:2])
    except ValueError:
        result["duplicate_grammar_rejected"] = True
    else:
        raise AssertionError("shared grammar accepted")
    result["passed"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify(args.tokenizer)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)


if __name__ == "__main__":
    main()
