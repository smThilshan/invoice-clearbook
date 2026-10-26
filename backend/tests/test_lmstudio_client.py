import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters.llm import ExtractionError, ExtractionRequest, get_extraction_client
from app.adapters.llm.lmstudio import LMStudioExtractionClient
from app.core.config import Settings

SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}
OK_BODY = {
    "choices": [
        {"message": {"role": "assistant", "content": '{"total": 1050.10}'}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40},
    "stats": {"time_to_first_token": 30.0, "generation_time": 8.5},
    "model_info": {"context_length": 16384},
}
MODEL_INFO = {"type": "vlm", "state": "loaded", "loaded_context_length": 16384, "capabilities": []}


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> LMStudioExtractionClient:
    http = httpx.Client(base_url="http://lmstudio.test", transport=httpx.MockTransport(handler))
    return LMStudioExtractionClient(
        base_url="http://lmstudio.test",
        model="qwen/qwen3-vl-4b",
        context_tokens=16384,
        timeout_seconds=600,
        http_client=http,
    )


def capture(sent: list[dict[str, Any]], body: dict[str, Any] = OK_BODY) -> Callable[..., Any]:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"path": request.url.path, "json": json.loads(request.content)})
        return httpx.Response(200, json=body)

    return handler


def test_request_sends_images_prompt_schema_and_zero_temperature() -> None:
    sent: list[dict[str, Any]] = []
    client = make_client(capture(sent))

    client.extract(
        ExtractionRequest(
            prompt="Extract.",
            json_schema=SCHEMA,
            images=[b"\x89PNG", b"\xff\xd8\xff"],
            system_prompt="Rules.",
        )
    )

    [call] = sent
    payload = call["json"]
    assert call["path"] == "/api/v0/chat/completions"
    assert payload["model"] == "qwen/qwen3-vl-4b"
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "extraction", "strict": True, "schema": SCHEMA},
    }
    assert (payload["temperature"], payload["stream"]) == (0, False)
    assert payload["messages"][0] == {"role": "system", "content": "Rules."}
    png, jpeg, text = payload["messages"][1]["content"]
    assert (
        png["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    )
    assert jpeg["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert text == {"type": "text", "text": "Extract."}


def test_text_only_request_has_no_images_or_system_message() -> None:
    sent: list[dict[str, Any]] = []
    make_client(capture(sent)).extract(ExtractionRequest(prompt="Hi", json_schema=SCHEMA))

    assert sent[0]["json"]["messages"] == [{"role": "user", "content": "Hi"}]


def test_result_carries_raw_output_and_metrics() -> None:
    result = make_client(capture([])).extract(ExtractionRequest(prompt="x", json_schema=SCHEMA))

    assert result.raw_output == '{"total": 1050.10}'  # untouched: parsed later as exact Decimal
    assert result.model == "qwen/qwen3-vl-4b"
    assert (result.prompt_tokens, result.output_tokens) == (1200, 40)
    assert (result.reading_ms, result.writing_ms) == (30_000, 8_500)
    assert result.truncated is False


def test_output_cut_at_token_limit_is_flagged() -> None:
    body = {
        **OK_BODY,
        "choices": [{"message": {"content": '{"total": 10'}, "finish_reason": "length"}],
    }

    result = make_client(capture([], body)).extract(ExtractionRequest(prompt="x", json_schema={}))

    assert result.truncated is True


def test_answer_from_a_model_loaded_with_too_small_a_context_is_refused() -> None:
    body = {**OK_BODY, "model_info": {"context_length": 4096}}  # e.g. a just-in-time load

    with pytest.raises(ExtractionError, match="4096-token context, needs 16384: run make llm-load"):
        make_client(capture([], body)).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_timeout_raises_extraction_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ExtractionError, match="timed out after 600s"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_http_error_raises_extraction_error_with_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Model with identifier 'x' not found"})

    with pytest.raises(ExtractionError, match=r"HTTP 400.*not found"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


@pytest.mark.parametrize("body", [{"unexpected": True}, {"choices": []}, {"choices": [{}]}])
def test_malformed_response_raises_extraction_error(body: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ExtractionError, match="unexpected response"):
        make_client(handler).extract(ExtractionRequest(prompt="x", json_schema={}))


def test_factory_builds_the_lm_studio_client_by_default() -> None:
    client = get_extraction_client(Settings(_env_file=None))

    assert isinstance(client, LMStudioExtractionClient)
    assert client.model == "qwen/qwen3-vl-4b"
    client.close()


# --- check(): is the configured model ready? ------------------------------------------------


def checked(response: httpx.Response | Exception, sent: list[str] | None = None) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(f"{request.method} {request.url.path}")
        if isinstance(response, Exception):
            raise response
        return response

    return make_client(handler).check().value


def test_check_ok_for_a_vision_model_loaded_with_enough_context() -> None:
    sent: list[str] = []

    assert checked(httpx.Response(200, json=MODEL_INFO), sent) == "ok"
    assert sent == ["GET /api/v0/models/qwen/qwen3-vl-4b"]  # no loading, no generation


@pytest.mark.parametrize(
    "info",
    [
        {**MODEL_INFO, "state": "not-loaded", "loaded_context_length": None},
        {**MODEL_INFO, "loaded_context_length": 4096},
    ],
)
def test_check_flags_a_model_not_loaded_or_loaded_with_too_small_a_context(
    info: dict[str, Any],
) -> None:
    assert checked(httpx.Response(200, json=info)) == "not_loaded"


@pytest.mark.parametrize(
    "info", [{**MODEL_INFO, "type": "llm"}, {**MODEL_INFO, "capabilities": ["reasoning"]}]
)
def test_check_flags_text_only_or_thinking_models(info: dict[str, Any]) -> None:
    assert checked(httpx.Response(200, json=info)) == "incompatible"


def test_check_missing_model() -> None:
    response = httpx.Response(400, json={"error": "Model with identifier 'x' not found"})

    assert checked(response) == "missing"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("refused"),
    ],
)
def test_check_unreachable(response: httpx.Response | Exception) -> None:
    assert checked(response) == "unreachable"
