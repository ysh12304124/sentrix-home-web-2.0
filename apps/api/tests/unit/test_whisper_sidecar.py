from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts/maintenance/whisper_sidecar.py"
SPEC = importlib.util.spec_from_file_location("sentrix_test_whisper_sidecar", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_segments_preserve_timestamps_and_convert_whisper_scores() -> None:
    rows = MODULE._segments(
        {
            "segments": [
                {
                    "start": 1.25,
                    "end": 3.5,
                    "text": " Classic tonkotsu ramen ",
                    "avg_logprob": -0.2,
                    "no_speech_prob": 0.1,
                }
            ]
        },
        5_000,
    )

    assert rows[0]["start_ms"] == 1_250
    assert rows[0]["end_ms"] == 3_500
    assert rows[0]["text"] == "Classic tonkotsu ramen"
    assert 0.81 < rows[0]["confidence"] < 0.82
    assert rows[0]["speech_probability"] == 0.9


def test_segments_fall_back_to_whole_audio_when_model_has_no_segment_rows() -> None:
    rows = MODULE._segments({"text": "整段结果"}, 4_200)

    assert rows == [{"start_ms": 0, "end_ms": 4_200, "text": "整段结果"}]


def test_local_model_safetensors_name_gets_a_read_only_mlx_view(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}")
    alternate = tmp_path / "model.safetensors"
    alternate.write_bytes(b"weights")
    service = object.__new__(MODULE.Service)
    service._model_view = None

    compatible = Path(service._compatible_model_path(str(tmp_path)))

    assert compatible != tmp_path
    assert (compatible / "weights.safetensors").resolve() == alternate.resolve()
    assert (compatible / "config.json").resolve() == (tmp_path / "config.json").resolve()
