"""MLX Whisper HTTP sidecar：视频/音频 → 带时间戳分段，契约与 FunASR sidecar 相同。

示例（权重目录不进入 Git）：
    audio_eval/.venv/bin/python scripts/maintenance/whisper_sidecar.py \
      --model audio_eval/models/whisper-large-v3-turbo-8bit-local --port 8104
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import math
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Sentrix Whisper sidecar", version="1")


class TranscribeRequest(BaseModel):
    path: str


class Service:
    def __init__(self, *, model_name: str, language: str = "") -> None:
        import mlx.core as mx  # type: ignore[import-not-found]
        import mlx_whisper  # type: ignore[import-not-found]
        from mlx_whisper.transcribe import ModelHolder  # type: ignore[import-not-found]

        self.model_name = model_name
        self.language = language
        self.model_version = _package_version("mlx-whisper")
        self._mx = mx
        self._transcribe = mlx_whisper.transcribe
        # 部分手动下载器把 MLX 权重保存成 Hugging Face 常见的 model.safetensors，
        # 而 mlx-whisper 0.4.x 固定寻找 weights.safetensors。用临时只读视图兼容，
        # 不重命名、不复制、更不修改用户的权重目录。
        self._model_view: tempfile.TemporaryDirectory[str] | None = None
        model_path = self._compatible_model_path(model_name)
        self._model_path = model_path
        try:
            from opencc import OpenCC  # type: ignore[import-not-found]

            self._simplify: Callable[[str], str] = OpenCC("t2s").convert
        except ImportError:
            self._simplify = lambda value: value
        signature = f"{model_name}:{language or 'auto'}:mlx-fp16:temperature-0:previous-false:wav-16k-mono-v1"
        self.config_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        ModelHolder.get_model(model_path, mx.float16)
        mx.synchronize()

    def _compatible_model_path(self, model_name: str) -> str:
        source = Path(model_name).expanduser()
        if not source.is_dir():
            return model_name
        expected = source / "weights.safetensors"
        alternate = source / "model.safetensors"
        if expected.is_file() or not alternate.is_file():
            return str(source)
        self._model_view = tempfile.TemporaryDirectory(prefix="sentrix-whisper-model-")
        view = Path(self._model_view.name)
        for item in source.iterdir():
            if item.name == alternate.name:
                continue
            (view / item.name).symlink_to(item.resolve())
        (view / expected.name).symlink_to(alternate.resolve())
        return str(view)

    def transcribe(self, source: Path) -> dict[str, object]:
        with tempfile.NamedTemporaryFile(prefix="sentrix-whisper-", suffix=".wav", delete=False) as temp:
            normalized = Path(temp.name)
        try:
            _normalize_audio(source, normalized)
            options: dict[str, object] = {
                "path_or_hf_repo": self._model_path,
                "task": "transcribe",
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "verbose": None,
            }
            if self.language:
                options["language"] = self.language
            result = self._transcribe(str(normalized), **options)
            self._mx.synchronize()
            if not isinstance(result, dict):
                raise RuntimeError("MLX Whisper 未返回字典结果")
            raw_text = self._simplify(str(result.get("text") or "")).strip()
            return {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "config_hash": self.config_hash,
                "language": str(result.get("language") or self.language),
                "raw_text": raw_text,
                "segments": _segments(
                    result,
                    _wav_duration_ms(normalized),
                    simplify=self._simplify,
                ),
            }
        finally:
            normalized.unlink(missing_ok=True)


SERVICE: Service | None = None
LOAD_ERROR = "sidecar not initialized"


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "available": SERVICE is not None,
        "load_error": "" if SERVICE is not None else LOAD_ERROR,
        "model_name": SERVICE.model_name if SERVICE is not None else "",
        "model_version": SERVICE.model_version if SERVICE is not None else "",
        "config_hash": SERVICE.config_hash if SERVICE is not None else "",
        "language": SERVICE.language if SERVICE is not None else "",
    }


@app.post("/transcribe")
def transcribe(request: TranscribeRequest) -> dict[str, object]:
    if SERVICE is None:
        raise HTTPException(status_code=503, detail=LOAD_ERROR)
    source = Path(request.path).expanduser().resolve()
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"media not found: {source}")
    try:
        return SERVICE.transcribe(source)
    except Exception as error:  # noqa: BLE001 - HTTP 边界统一返回可诊断错误
        raise HTTPException(status_code=422, detail=f"{type(error).__name__}: {error}") from error


def _segments(
    result: dict[str, Any],
    duration_ms: int,
    *,
    simplify: Callable[[str], str] | None = None,
) -> list[dict[str, object]]:
    convert = simplify or (lambda value: value)
    rows: list[dict[str, object]] = []
    values = result.get("segments") or []
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            text = convert(str(item.get("text") or "")).strip()
            if not text:
                continue
            start_ms = max(0, round(_float(item.get("start"), 0.0) * 1000))
            end_ms = max(start_ms + 1, round(_float(item.get("end"), duration_ms / 1000) * 1000))
            rows.append(
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "confidence": _log_probability(item.get("avg_logprob")),
                    "speech_probability": _speech_probability(item.get("no_speech_prob")),
                }
            )
    if not rows:
        text = convert(str(result.get("text") or "")).strip()
        if text:
            rows.append({"start_ms": 0, "end_ms": max(1, duration_ms), "text": text})
    return rows


def _normalize_audio(source: Path, target: Path) -> None:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError((completed.stderr or "ffmpeg normalization failed").strip())


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return round(audio.getnframes() / max(audio.getframerate(), 1) * 1000)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _float(value: object, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _log_probability(value: object) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else min(1.0, max(0.0, math.exp(parsed)))


def _speech_probability(value: object) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else min(1.0, max(0.0, 1.0 - parsed))


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentrix MLX Whisper sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo-8bit")
    parser.add_argument("--language", default="", help="留空为自动识别；可设 zh/en")
    args = parser.parse_args()

    global SERVICE, LOAD_ERROR
    try:
        SERVICE = Service(model_name=args.model, language=args.language)
        LOAD_ERROR = ""
    except Exception as error:  # noqa: BLE001 - 服务仍启动，由 /health 报告加载失败
        LOAD_ERROR = f"{type(error).__name__}: {error}"

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
