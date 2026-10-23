"""ExtractionClient for Ollama, using its native /api/chat.

Native rather than the OpenAI-compatible /v1 endpoint because only the native API lets us set
the context window per request (`num_ctx`); with the small default, a page image + OCR text +
schema is silently truncated. `format` makes Ollama enforce the JSON schema while decoding.

Force stop (task 4.20): with `should_stop`, the same request is sent on a connection of its
own from a background thread, while this thread asks `should_stop` every few seconds (also
during the minutes the model reads, when nothing arrives yet). On stop the connection is
closed: Ollama abandons a request whose caller hangs up, and nothing of the answer is kept.
The request and the answer handling are the same as without it, so answers do not change.
"""

import base64
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Self

import httpx

from app.adapters.llm.base import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
    ExtractionStopped,
    ModelStatus,
)

logger = logging.getLogger(__name__)

_CHECK_TIMEOUT_SECONDS = 5.0
STOP_CHECK_SECONDS = 2.0  # how often a stoppable call asks whether to stop


class OllamaExtractionClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        context_tokens: int,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._model = model
        self._context_tokens = context_tokens
        self._timeout_seconds = timeout_seconds
        # A stoppable call gets a connection of its own, so closing it stops only that call.
        self._new_client = client_factory or (
            lambda: httpx.Client(
                base_url=base_url, timeout=httpx.Timeout(timeout_seconds, connect=5.0)
            )
        )
        self._http = http_client or self._new_client()

    @property
    def model(self) -> str:
        return self._model

    def check(self) -> ModelStatus:
        """Installed, and the Instruct vision variant? `/api/show` reports capabilities
        without generating anything; a Thinking variant lists "thinking" (decision 2)."""
        try:
            response = self._http.post(
                "/api/show", json={"model": self._model}, timeout=_CHECK_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            return ModelStatus.UNREACHABLE
        if response.status_code == httpx.codes.NOT_FOUND:
            return ModelStatus.MISSING
        if response.is_error:
            return ModelStatus.UNREACHABLE
        try:
            capabilities = set(response.json().get("capabilities") or ())
        except ValueError:
            return ModelStatus.UNREACHABLE
        if "thinking" in capabilities or "vision" not in capabilities:
            return ModelStatus.INCOMPATIBLE
        return ModelStatus.OK

    def extract(
        self, request: ExtractionRequest, *, should_stop: Callable[[], bool] | None = None
    ) -> ExtractionResult:
        started = time.perf_counter()
        payload = self._payload(request)
        body = (
            self._send(self._http, payload)
            if should_stop is None
            else self._send_stoppable(payload, should_stop)
        )
        return self._result(body, started)

    def _send_stoppable(
        self, payload: dict[str, Any], should_stop: Callable[[], bool]
    ) -> dict[str, Any]:
        client = self._new_client()
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                outcome["body"] = self._send(client, payload)
            except BaseException as exc:  # handed to the waiting thread
                outcome["error"] = exc

        worker = threading.Thread(target=call, name="model-call", daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(STOP_CHECK_SECONDS)
                if worker.is_alive() and should_stop():
                    client.close()  # hang up: the model server abandons the request
                    worker.join(STOP_CHECK_SECONDS)
                    raise ExtractionStopped("stopped by the user")
        finally:
            client.close()
        if "error" in outcome:
            raise outcome["error"]
        body: dict[str, Any] = outcome["body"]
        return body

    def _send(self, client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.post("/api/chat", json=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            body["message"]["content"]
        except httpx.TimeoutException as exc:
            raise ExtractionError(f"model timed out after {self._timeout_seconds:.0f}s") from exc
        except httpx.HTTPStatusError as exc:
            # Ollama's error bodies are short ("model not found"); they never echo the prompt.
            detail = exc.response.text[:200]
            status = exc.response.status_code
            raise ExtractionError(f"model server HTTP {status}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ExtractionError(f"cannot reach model server: {type(exc).__name__}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ExtractionError("model server returned an unexpected response") from exc
        return body

    def _result(self, body: dict[str, Any], started: float) -> ExtractionResult:
        result = ExtractionResult(
            model=self._model,
            raw_output=body["message"]["content"],
            truncated=body.get("done_reason") == "length",
            duration_ms=round((time.perf_counter() - started) * 1000),
            prompt_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
            reading_ms=_ms(body.get("prompt_eval_duration")),
            writing_ms=_ms(body.get("eval_duration")),
        )
        # Metrics only: never prompt or output text (they contain invoice data).
        logger.info(
            "extraction done model=%s ms=%d reading_ms=%s writing_ms=%s prompt_tokens=%s "
            "output_tokens=%s truncated=%s",
            result.model,
            result.duration_ms,
            result.reading_ms,
            result.writing_ms,
            result.prompt_tokens,
            result.output_tokens,
            result.truncated,
        )
        return result

    def _payload(self, request: ExtractionRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        user: dict[str, Any] = {"role": "user", "content": request.prompt}
        if request.images:
            user["images"] = [base64.b64encode(image).decode("ascii") for image in request.images]
        messages.append(user)
        return {
            "model": self._model,
            "messages": messages,
            "format": dict(request.json_schema),
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self._context_tokens},
        }

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _ms(nanoseconds: object) -> int | None:
    return round(nanoseconds / 1_000_000) if isinstance(nanoseconds, int) else None
