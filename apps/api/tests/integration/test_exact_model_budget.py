"""HTTP protocol checks only; these are not real model quality or load tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sentrix.domain.assistant.models import ChatMessage, ChatRole
from sentrix.domain.assistant.ports import ModelServiceError, PromptTooLongError
from sentrix.infrastructure.assistant.chat_client import OpenAIChatModel
from sentrix.infrastructure.assistant.model_endpoint import ServedModel


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        count = 3000
        window = 32768
        status = 200
        received = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_json({"data": [{"id": "qwen-test"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.received.append((self.path, body))
            if self.path == "/tokenize":
                self.send_json({"count": self.count, "max_model_len": self.window})
            else:
                self.send_json({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

        def send_json(self, value):
            raw = json.dumps(value).encode()
            self.send_response(self.status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = ServedModel(
        f"http://127.0.0.1:{server.server_port}/v1",
        generation_profile="qwen35_nonthinking",
        exact_output_budget=True,
        context_tokens=32768,
    )
    try:
        yield model, Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_tools_images_and_template_are_counted_without_mutating_input(endpoint):
    model, handler = endpoint
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
        }
    ]
    payload = {
        "model": "qwen-test",
        "messages": messages,
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        **model.generation_parameters(),
    }
    before = json.dumps(messages)
    facts = model.apply_output_budget(payload, timeout_s=2)
    assert payload["max_tokens"] == 29768
    assert facts["prompt_tokens_exact"] == 3000
    assert handler.received[0][1]["messages"] == messages
    assert handler.received[0][1]["tools"] == payload["tools"]
    assert handler.received[0][1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert json.dumps(messages) == before


def test_exact_budget_bypasses_legacy_character_limit(endpoint):
    model, handler = endpoint
    client = OpenAIChatModel(model.base_url, model, context_tokens=32768)
    reply = client.complete(
        [
            ChatMessage(role=ChatRole.SYSTEM, content="test"),
            ChatMessage(role=ChatRole.USER, content="a" * 100_000),
        ],
        (),
    )
    assert reply.content == "ok"
    request = handler.received[-1][1]
    assert request["max_tokens"] == 29768
    assert len(request["messages"][1]["content"]) == 100_000
    assert request["temperature"] == 0.7
    assert request["presence_penalty"] == 1.5


@pytest.mark.parametrize("count,window", [(32768, 32768), (32769, 32768), (100, 16384)])
def test_bad_or_exhausted_window_never_dispatches_generation(endpoint, count, window):
    model, handler = endpoint
    handler.count, handler.window = count, window
    client = OpenAIChatModel(model.base_url, model, context_tokens=32768)
    with pytest.raises((PromptTooLongError, ModelServiceError)):
        client.complete(
            [
                ChatMessage(role=ChatRole.SYSTEM, content="test"),
                ChatMessage(role=ChatRole.USER, content="hello"),
            ],
            (),
        )
    assert all(path == "/tokenize" for path, _ in handler.received)


def test_tokenizer_failure_never_dispatches_generation(endpoint):
    model, handler = endpoint
    model.current()
    handler.status = 500
    with pytest.raises(ModelServiceError):
        OpenAIChatModel(model.base_url, model).complete_structured(
            [
                ChatMessage(role=ChatRole.SYSTEM, content="test"),
                ChatMessage(role=ChatRole.USER, content="hello"),
            ],
            (),
            response_format={"type": "json_object"},
        )
    assert len(handler.received) == 1
    assert handler.received[0][0] == "/tokenize"


def test_structured_output_instruction_is_included_in_tokenization(endpoint):
    model, handler = endpoint
    schema = {"type": "json_object"}
    OpenAIChatModel(model.base_url, model).complete_structured(
        [ChatMessage(role=ChatRole.SYSTEM, content="test"), ChatMessage(role=ChatRole.USER, content="hi")],
        (),
        response_format=schema,
    )
    assert handler.received[0][1]["response_format"] == schema
    assert handler.received[-1][1]["response_format"] == schema
