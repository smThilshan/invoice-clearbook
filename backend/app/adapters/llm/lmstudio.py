"""ExtractionClient for LM Studio (MLX on Apple Silicon, decision 46), using its REST API
`/api/v0/chat/completions`: an OpenAI-style request whose answer also carries timings
(`stats`) and the context the model is loaded with (`model_info`).

LM Studio sets the context window when the model is loaded, not per request. Loaded with a
smaller one (LM Studio's default, or a just-in-time load) a page image + OCR text + schema
would be cut, so `check()` reports NOT_LOADED and `extract()` refuses the answer. Load it with
`make llm-load`. `response_format` makes LM Studio enforce the JSON schema while decoding.

Connections, errors and force stop (task 4.20) are the Ollama client's; only the request and
the answer differ.
"""

import base64
from typing import Any

import httpx

from app.adapters.llm.base import ExtractionError, ExtractionRequest, ExtractionResult, ModelStatus
from app.adapters.llm.ollama import OllamaExtractionClient

_CHECK_TIMEOUT_SECONDS = 5.0


class LMStudioExtractionClient(OllamaExtractionClient):
    _chat_path = "/api/v0/chat/completions"

    def check(self) -> ModelStatus:
        """Installed, a vision model, not a Thinking one, and loaded with enough context?
        `/api/v0/models/<id>` answers from metadata, without loading or generating."""
        try:
            response = self._http.get(
                f"/api/v0/models/{self._model}", timeout=_CHECK_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            return ModelStatus.UNREACHABLE
        if response.is_error:
            # An unknown model is HTTP 400 "Model with identifier '…' not found".
            return ModelStatus.MISSING if "not found" in response.text else ModelStatus.UNREACHABLE
        try:
            info: dict[str, Any] = response.json()
        except ValueError:
            return ModelStatus.UNREACHABLE
        thinking = "thinking" in self._model.lower() or "reasoning" in info.get("capabilities", ())
        if info.get("type") != "vlm" or thinking:
            return ModelStatus.INCOMPATIBLE
        loaded_context = info.get("loaded_context_length") or 0
        if info.get("state") != "loaded" or loaded_context < self._context_tokens:
            return ModelStatus.NOT_LOADED
        return ModelStatus.OK

    def _payload(self, request: ExtractionRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        if request.images:
            images = [
                {"type": "image_url", "image_url": {"url": _data_url(image)}}
                for image in request.images
            ]
            messages.append(
                {"role": "user", "content": [*images, {"type": "text", "text": request.prompt}]}
            )
        else:
            messages.append({"role": "user", "content": request.prompt})
        return {
            "model": self._model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": dict(request.json_schema),
                },
            },
            "temperature": 0,
            "stream": False,
        }

    def _answer(self, body: dict[str, Any]) -> str:
        content: str = body["choices"][0]["message"]["content"]
        return content

    def _read(self, body: dict[str, Any], duration_ms: int) -> ExtractionResult:
        loaded_context = (body.get("model_info") or {}).get("context_length")
        if isinstance(loaded_context, int) and loaded_context < self._context_tokens:
            raise ExtractionError(
                f"model loaded with a {loaded_context}-token context, needs "
                f"{self._context_tokens}: run make llm-load"
            )
        usage = body.get("usage") or {}
        stats = body.get("stats") or {}
        return ExtractionResult(
            model=self._model,
            raw_output=self._answer(body),
            truncated=body["choices"][0].get("finish_reason") == "length",
            duration_ms=duration_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            reading_ms=_ms(stats.get("time_to_first_token")),
            writing_ms=_ms(stats.get("generation_time")),
        )


def _data_url(image: bytes) -> str:
    mime = "image/jpeg" if image.startswith(b"\xff\xd8") else "image/png"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


def _ms(seconds: object) -> int | None:
    return round(seconds * 1000) if isinstance(seconds, int | float) else None
