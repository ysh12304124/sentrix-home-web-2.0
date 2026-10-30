"""轻量 FunASR HTTP sidecar：媒体音轨 → 16 kHz 单声道 → VAD/ASR/标点 → 分段 JSON。

不包含摘要、情绪识别、说话人识别。模型只在 sidecar 启动时加载一次。

示例：
    python scripts/maintenance/funasr_sidecar.py --port 8104
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException  # type: ignore[import-not-found]
from pydantic import BaseModel

app = FastAPI(title="Sentrix FunASR sidecar", version="1")


class TranscribeRequest(BaseModel):
    path: str


class Service:
    def __init__(
        self,
        *,
        model_name: str,
        vad_model: str,
        punc_model: str,
        device: str,
        spk_model: str = "",
    ) -> None:
        from funasr import AutoModel  # type: ignore[import-not-found]

        self.model_name = model_name
        self.vad_model = vad_model
        self.punc_model = punc_model
        self.spk_model = spk_model
        self.device = device
        self.model_version = _package_version("funasr")
        signature = (
            f"{model_name}:{vad_model}:{punc_model}:{spk_model}:{device}:wav-16k-mono-v1"
        )
        self.config_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        model_kwargs = {"model": model_name, "device": device}
        if vad_model:
            model_kwargs["vad_model"] = vad_model
        if punc_model:
            model_kwargs["punc_model"] = punc_model
        if spk_model:
            model_kwargs["spk_model"] = spk_model
        self.model = AutoModel(**model_kwargs)

    def transcribe(self, source: Path) -> dict[str, object]:
        with tempfile.NamedTemporaryFile(prefix="sentrix-asr-", suffix=".wav", delete=False) as temporary:
            normalized = Path(temporary.name)
        try:
            _normalize_audio(source, normalized)
            result = self.model.generate(input=str(normalized), batch_size_s=60)
            if not result or not isinstance(result[0], dict):
                raise RuntimeError("FunASR 未返回识别结果")
            first = result[0]
            raw_text = str(first.get("text") or "")
            segments = _segments(first, _wav_duration_ms(normalized))
            return {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "config_hash": self.config_hash,
                "language": str(first.get("language") or "zh"),
                "raw_text": raw_text,
                "segments": segments,
            }
        finally:
            normalized.unlink(missing_ok=True)


SERVICE: Service | None = None
LOAD_ERROR = "sidecar not initialized"


@app.get("/health")  # type: ignore[misc]
def health() -> dict[str, object]:
    return {
        "available": SERVICE is not None,
        "load_error": "" if SERVICE is not None else LOAD_ERROR,
        "model_name": SERVICE.model_name if SERVICE is not None else "",
        "model_version": SERVICE.model_version if SERVICE is not None else "",
        "config_hash": SERVICE.config_hash if SERVICE is not None else "",
        "vad_model": SERVICE.vad_model if SERVICE is not None else "",
        "punc_model": SERVICE.punc_model if SERVICE is not None else "",
        "spk_model": SERVICE.spk_model if SERVICE is not None else "",
    }


@app.post("/transcribe")  # type: ignore[misc]
def transcribe(request: TranscribeRequest) -> dict[str, object]:
    if SERVICE is None:
        raise HTTPException(status_code=503, detail=LOAD_ERROR)
    source = Path(request.path).expanduser().resolve()
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"media not found: {source}")
    try:
        return SERVICE.transcribe(source)
    except Exception as error:  # noqa: BLE001 - HTTP 边界统一转成可诊断错误
        raise HTTPException(status_code=422, detail=f"{type(error).__name__}: {error}") from error


def _normalize_audio(source: Path, target: Path) -> None:
    """提取默认音轨并统一格式；``-vn`` 同时覆盖纯音频和带音轨的视频。"""
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


def _segments(result: dict[str, Any], duration_ms: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    sentence_info = result.get("sentence_info") or []
    if isinstance(sentence_info, list):
        for item in sentence_info:
            if not isinstance(item, dict):
                continue
            text = _clean_text(str(item.get("text") or item.get("sentence") or ""))
            speaker = item.get("spk")
            rows.append(
                {
                    "start_ms": int(item.get("start") or 0),
                    "end_ms": int(item.get("end") or 0),
                    "text": text,
                    "confidence": _first_float(item, "confidence", "score"),
                    "alignment_score": _first_float(item, "alignment_score"),
                    "speech_probability": _first_float(item, "speech_probability"),
                    "language_probability": _first_float(item, "language_probability"),
                    # CAM++ 的标签只在当前媒体内有效；0 是合法标签，不能用 ``or`` 吞掉。
                    "speaker_label": "" if speaker is None else str(speaker).strip(),
                    "speaker_confidence": _first_float(
                        item, "speaker_confidence", "spk_confidence", "spk_score"
                    ),
                }
            )
    if not rows:
        text = _clean_text(str(result.get("text") or ""))
        if text:
            rows.append({"start_ms": 0, "end_ms": duration_ms, "text": text})
    return rows


_CONTROL_TAG_RE = re.compile(r"<\s*\|[^<>]*?\|\s*>")


def _clean_text(text: str) -> str:
    """只移除模型控制标签；不提取或保留情绪标签。"""
    return re.sub(r"\s+", " ", _CONTROL_TAG_RE.sub(" ", text)).strip()


def _first_float(item: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return round(audio.getnframes() / max(audio.getframerate(), 1) * 1000)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentrix FunASR sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--model", default="paraformer-zh")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument("--spk-model", default="cam++")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    global SERVICE, LOAD_ERROR
    try:
        SERVICE = Service(
            model_name=args.model,
            vad_model=args.vad_model,
            punc_model=args.punc_model,
            device=args.device,
            spk_model=args.spk_model,
        )
        LOAD_ERROR = ""
    except Exception as error:  # noqa: BLE001 - 服务仍启动，/health 明确报告加载失败
        LOAD_ERROR = f"{type(error).__name__}: {error}"

    import uvicorn  # type: ignore[import-not-found]

    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
