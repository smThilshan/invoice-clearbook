import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters.llm import ExtractionError, ExtractionRequest, get_extraction_client
from app.adapters.llm.ollama import OllamaExtractionClient
from app.core.config import Settings

SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}
OK_BODY = {
    "message": {"role": "assistant", "content": '{"total": 1050.10}'},
    "done_reason": "stop",
    "prompt_eval_count": 1200,
    "eval_count": 40,
}


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OllamaExtractionClient:
    http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaExtractionClient(
        base_url="http://ollama.test",
        model="qwen3-vl:4b-instruct",
        context_tokens=16384,
        timeout_seconds=600,
        http_client=http,
    )


def capture(sent: list[dict[str, Any]], body: dict[str, Any] = OK_BODY) -> Callable[..., Any]:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"path": request.url.path, "json": json.loads(request.content)})
        return httpx.Response(200, json=body)

    return handler


def test_request_uses_native_chat_with_schema_context_and_zero_temperature() -> None:
    sent: list[dict[str, Any]] = []
    client = make_client(capture(sent))

    client.extract(
        ExtractionRequest(
            prompt="Extract.", json_schema=SCHEMA, images=[b"\x89PNG"], system_prompt="Rules."
        )
    )

    [call] = sent
    payload = call["json"]
    assert call["path"] == "/api/chat"
    assert payload["model"] == "qwen3-vl:4b-instruct"
    assert payload["format"] == SCHEMA
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0, "num_ctx": 16384}
    assert payload["messages"][0] == {"role": "system", "content": "Rules."}
    user = payload["messages"][1]
    assert user["content"] == "Extract."
    assert user["images"] == [base64.b64encode(b"\x89PNG").decode()]


def test_text_only_request_has_no_images_or_system_message() -> None:
    sent: list[dict[str, Any]] = []
    make_client(capture(sent)).extract(ExtractionRequest(prompt="Hi", json_schema=SCHEMA))

    assert sent[0]["json"]["messages"] == [{"role": "user", "content": "Hi"}]


def test_result_carries_raw_output_and_metrics() -> None:
    result = make_client(capture([])).extract(ExtractionRequest(prompt="x", json_schema=SCHEMA))

    assert result.raw_output == '{"total": 1050.10}'  # untouched: parsed later as exact Decimal
    assert result.model == "qwen3-vl:4b-instruct"
    assert (result.prompt_tokens, result.output_tokens) == (1200, 40)
    assert result.truncated is False
    assert result.duration_ms >= 0


def test_output_cut_at_token_limit_is_flagged() -> None:
    body = {**OK_BODY, "message": {"content": '{"total": 10'}, "done_reason": "length"}

    result = make_client(capture([], body)).extract(ExtractionRequest(prompt="x", json_schema={}))

    assert result.truncated is True


def test_timeout_raises_extraction_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ExtractionError, match="timed out after 600s"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_server_down_raises_extraction_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ExtractionError, match="cannot reach model server"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_http_error_raises_extraction_error_with_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    with pytest.raises(ExtractionError, match=r"HTTP 404.*not found"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


@pytest.mark.parametrize("body", [{"unexpected": True}, {"message": None}])
def test_malformed_response_raises_extraction_error(body: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ExtractionError, match="unexpected response"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_factory_takes_model_and_url_from_settings() -> None:
    settings = Settings(
        _env_file=None, llm_model="custom-model:1b", llm_base_url="http://gpu-box:11434"
    )

    client = get_extraction_client(settings)

    assert isinstance(client, OllamaExtractionClient)
    assert client.model == "custom-model:1b"
    client.close()
