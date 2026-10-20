"""ExtractionClient for Ollama, using its native /api/chat.

Native rather than the OpenAI-compatible /v1 endpoint because only the native API lets us set
the context window per request (`num_ctx`); with the small default, a page image + OCR text +
schema is silently truncated. `format` makes Ollama enforce the JSON schema while decoding.
"""

import base64
import logging
import time
from typing import Any, Self

import httpx

from app.adapters.llm.base import ExtractionError, ExtractionRequest, ExtractionResult

logger = logging.getLogger(__name__)


class OllamaExtractionClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        context_tokens: int,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._context_tokens = context_tokens
        self._timeout_seconds = timeout_seconds
        self._http = http_client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout_seconds, connect=5.0)
        )

    @property
    def model(self) -> str:
        return self._model

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        try:
            response = self._http.post("/api/chat", json=self._payload(request))
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            raw_output = body["message"]["content"]
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

        result = ExtractionResult(
            model=self._model,
            raw_output=raw_output,
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
