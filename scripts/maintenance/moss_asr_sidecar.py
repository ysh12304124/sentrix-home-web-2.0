"""OpenAI-compatible ASR sidecar backed by local MOSS-Transcribe-Diarize.

The web API's ``QwenAudioTranscriber`` posts 15-second mono 16 kHz WAV chunks to
``/v1/audio/transcriptions``. This sidecar runs the local MOSS-Transcribe-Diarize
model and returns both the raw transcript and timestamped speaker segments, so
the existing ASR -> scene -> bidirectional alignment path needs no changes.

Run it under the ``sentrix-vllm`` conda env: that env has the transformers/torch
combination the model's remote code needs. The main API venv intentionally stays
on its pinned transformers version.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

PROMPT = (
    "请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）"
    "开头，正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。"
)

_SEGMENT = re.compile(
    r"\[(\d+(?:\.\d+)?)\]\[([A-Za-z]\d+)\](.*?)\[(\d+(?:\.\d+)?)\]",
    re.DOTALL,
)


class AsrRuntime:
    def __init__(self, model_path: str, device: str, dtype: str) -> None:
        self.model_path = model_path
        self.device = device
        self.dtype = dtype
        self.processor: Any = None
        self.model: Any = None
        self.lock = threading.Lock()

    def load(self) -> None:
        # torch/transformers are imported lazily so the parsing helpers stay
        # importable in the lightweight Mac test environment.
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        torch_dtype = torch.bfloat16 if self.dtype == "bf16" else torch.float32
        self.processor = AutoProcessor.from_pretrained(self.model_path, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            trust_remote_code=True,
            dtype=torch_dtype,
        )
        if self.device == "cuda" and torch.cuda.is_available():
            model = model.to("cuda")
        elif self.device == "auto" and torch.cuda.is_available():
            model = model.to("cuda")
            self.device = "cuda"
        else:
            model = model.to("cpu")
            self.device = "cpu"
        self.model = model.eval()

    def transcribe(self, audio: np.ndarray, *, max_new_tokens: int) -> str:
        if self.model is None or self.processor is None:
            raise RuntimeError("ASR model is not loaded")
        import torch

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": audio},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ]
        # The processor keeps tokenizer/feature-extractor state; serialize the
        # whole encode+generate section across concurrent video jobs.
        with self.lock, torch.inference_mode():
            chat = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.processor(text=chat, audio=audio, return_tensors="pt").to(self.device)
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        generated = output[:, inputs["input_ids"].shape[1] :]
        return self.processor.tokenizer.batch_decode(generated, skip_special_tokens=True)[0]


def _decode_audio(data: bytes, filename: str) -> np.ndarray:
    """WAV (the API's chunks) fast path; anything else goes through ffmpeg."""
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            frames = source.readframes(source.getnframes())
        if channels != 1 or width != 2 or rate != 16000:
            raise ValueError("unsupported wav format")
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        with tempfile.TemporaryDirectory(prefix="moss-asr-") as temporary:
            source = Path(temporary) / (filename or "audio.bin")
            target = Path(temporary) / "audio.wav"
            source.write_bytes(data)
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(target),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            with wave.open(str(target), "rb") as audio:
                return (
                    np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32)
                    / 32768.0
                )


def _clean_text(value: str) -> str:
    return " ".join(value.replace("<|im_end|>", "").split()).strip()


def _segments(text: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for match in _SEGMENT.finditer(text):
        start, speaker, body, end = match.groups()
        body = _clean_text(body)
        if not body:
            continue
        result.append(
            {
                "start": float(start),
                "end": float(end),
                "text": body,
                "speaker": speaker,
            }
        )
    return result


runtime: AsrRuntime | None = None
app = FastAPI(title="MOSS Transcribe Diarize ASR")


@app.on_event("startup")
def _startup() -> None:
    assert runtime is not None
    runtime.load()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": runtime is not None and runtime.model is not None,
        "model": runtime.model_path if runtime else "",
        "device": runtime.device if runtime else "",
    }


@app.get("/v1/models")
def models() -> dict[str, object]:
    name = Path(runtime.model_path).name if runtime else "MOSS-Transcribe-Diarize"
    return {
        "object": "list",
        "data": [{"id": name, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(""),
    response_format: str = Form("json"),
) -> JSONResponse:
    if runtime is None or runtime.model is None:
        raise HTTPException(status_code=503, detail="ASR model is not loaded")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        audio = _decode_audio(data, file.filename or "audio.wav")
        duration = len(audio) / 16000.0
        max_new_tokens = max(512, min(4096, int(duration * 24) + 256))
        raw = runtime.transcribe(audio, max_new_tokens=max_new_tokens)
    except Exception as error:  # noqa: BLE001 - surface a useful 500 to the caller
        raise HTTPException(status_code=500, detail=f"{type(error).__name__}: {error}") from error
    text = _clean_text(raw)
    return JSONResponse(
        {
            "text": text,
            "segments": _segments(raw),
            "language": "auto",
            "duration": round(duration, 3),
            "model": model or Path(runtime.model_path).name,
            "response_format": response_format,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", default="/home/asus/models/MOSS-Transcribe-Diarize")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8106)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--dtype", choices=("bf16", "fp32"), default="bf16")
    args = parser.parse_args()

    global runtime
    runtime = AsrRuntime(args.model_path, args.device, args.dtype)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
