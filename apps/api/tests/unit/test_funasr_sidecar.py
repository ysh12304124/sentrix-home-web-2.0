from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from sentrix.infrastructure.media.asr_sidecar import FunAsrSidecarTranscriber

SCRIPT = Path(__file__).resolve().parents[4] / "scripts/maintenance/funasr_sidecar.py"
SPEC = importlib.util.spec_from_file_location("sentrix_test_funasr_sidecar", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Service = MODULE.Service
_segments = MODULE._segments


def test_segments_preserve_zero_speaker_label_and_confidence() -> None:
    rows = _segments(
        {
            "sentence_info": [
                {
                    "start": 120,
                    "end": 980,
                    "text": "你好。",
                    "spk": 0,
                    "spk_confidence": 0.87,
                }
            ]
        },
        1000,
    )

    assert rows == [
        {
            "start_ms": 120,
            "end_ms": 980,
            "text": "你好。",
            "confidence": None,
            "alignment_score": None,
            "speech_probability": None,
            "language_probability": None,
            "speaker_label": "0",
            "speaker_confidence": 0.87,
        }
    ]


def test_service_wires_vad_punctuation_and_speaker_models(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAutoModel:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=FakeAutoModel))

    service = Service(
        model_name="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        spk_model="cam++",
        device="cpu",
    )

    assert captured == {
        "model": "paraformer-zh",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
        "spk_model": "cam++",
        "device": "cpu",
    }
    assert service.punc_model == "ct-punc"
    assert service.spk_model == "cam++"


def test_api_client_preserves_speaker_fields() -> None:
    result = FunAsrSidecarTranscriber._parse(
        {
            "model_name": "paraformer-zh",
            "model_version": "1",
            "config_hash": "cfg",
            "language": "zh",
            "raw_text": "你好。",
            "segments": [
                {
                    "start_ms": 120,
                    "end_ms": 980,
                    "text": "你好。",
                    "speaker_label": "0",
                    "speaker_confidence": 0.87,
                }
            ],
        }
    )

    assert result.segments[0].local_speaker_label == "0"
    assert result.segments[0].speaker_confidence == 0.87
