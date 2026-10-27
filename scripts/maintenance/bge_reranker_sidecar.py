"""BGE Reranker sidecar.

The API process deliberately does not import torch.  This process owns the
cross-encoder (BAAI/bge-reranker-v2-m3) and exposes:

- ``GET /health``  → readiness + model + device
- ``GET /stats``   → same facts, always 200
- ``POST /rerank`` → ``{"query": "...", "documents": ["...", ...]}``
  → ``{"scores": [...], "model": "..."}``

Scores are sigmoid logits: larger is more relevant.  The endpoint always
returns exactly one score per document; the adapter rejects anything else.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "models" / "bge-reranker-v2-m3"
MODEL_ID = "BAAI/bge-reranker-v2-m3"

_model = None
_tokenizer = None
_device = "cpu"
_dtype = "float32"
_error = ""
_lock = threading.Lock()


def _model_path() -> Path:
    return Path(os.getenv("BGE_RERANKER_MODEL_PATH", str(DEFAULT_MODEL_PATH))).expanduser().resolve()


def _pick_device(torch_module: Any) -> str:
    requested = os.getenv("SENTRIX_BGE_RERANK_DEVICE", "auto").strip().lower()
    if requested not in ("cpu", "cuda", "mps"):
        requested = "auto"
    if requested != "auto":
        return requested
    if torch_module.cuda.is_available():
        return "cuda"
    if torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load() -> None:
    global _model, _tokenizer, _device, _dtype, _error
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        threads = int(os.getenv("SENTRIX_BGE_RERANK_THREADS", "6"))
        if threads > 0:
            torch.set_num_threads(threads)
        _device = _pick_device(torch)
        requested_dtype = os.getenv("SENTRIX_BGE_RERANK_DTYPE", "float32").strip().lower()
        if requested_dtype not in ("float32", "fp32", "float16", "fp16"):
            requested_dtype = "float32"
        torch_dtype = torch.float16 if requested_dtype in ("float16", "fp16") else torch.float32
        _dtype = "float16" if torch_dtype is torch.float16 else "float32"
        path = _model_path()
        _tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            str(path), local_files_only=True
        )
        _model = AutoModelForSequenceClassification.from_pretrained(
            str(path),
            local_files_only=True,
            dtype=torch_dtype,
        )
        _model.to(_device)
        _model.eval()
        _error = ""
    except Exception as error:  # noqa: BLE001 - health endpoint reports startup failure
        _error = f"{type(error).__name__}: {error}"


def _rerank(query: str, documents: list[str]) -> list[float]:
    import torch

    if _model is None or _tokenizer is None:
        raise RuntimeError(_error or "model not loaded")
    max_length = int(os.getenv("SENTRIX_BGE_RERANK_MAX_LENGTH", "512"))
    batch_size = max(1, int(os.getenv("SENTRIX_BGE_RERANK_BATCH_SIZE", "8")))
    scores: list[float] = []
    with _lock, torch.inference_mode():
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            inputs = _tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            inputs = {key: value.to(_device) for key, value in inputs.items()}
            logits = _model(**inputs).logits.view(-1)
            scores.extend(float(value) for value in torch.sigmoid(logits).cpu().tolist())
    return scores


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, code: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return cast(dict[str, object], json.loads(self.rfile.read(length) or b"{}"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        loaded = _model is not None and _tokenizer is not None
        facts: dict[str, object] = {
            "model": MODEL_ID,
            "device": _device,
            "dtype": _dtype,
            "error": _error,
        }
        if self.path == "/health":
            self._send(
                200 if loaded else 503,
                {"status": "ok" if loaded else "error", **facts},
            )
        elif self.path == "/stats":
            self._send(200, {"loaded": loaded, **facts})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            body = self._body()
            if self.path != "/rerank":
                self._send(404, {"error": "not found"})
                return
            query = str(body.get("query") or "").strip()
            raw_documents = body.get("documents")
            if not query:
                self._send(400, {"error": "query 不能为空"})
                return
            if not isinstance(raw_documents, list) or not raw_documents:
                self._send(400, {"error": "documents 必须是非空数组"})
                return
            documents = [str(item) for item in raw_documents]
            scores = _rerank(query, documents)
            if len(scores) != len(documents):
                raise RuntimeError(f"scored {len(scores)} of {len(documents)} documents")
            self._send(200, {"scores": scores, "model": MODEL_ID})
        except Exception as error:  # noqa: BLE001 - adapter expects HTTP failure semantics
            self._send(500, {"error": f"{type(error).__name__}: {error}"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("SENTRIX_BGE_RERANK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SENTRIX_BGE_RERANK_PORT", "8104")))
    args = parser.parse_args()
    _load()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"BGE reranker sidecar ready on {args.host}:{args.port} "
        f"device={_device} dtype={_dtype} error={_error!r}",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
