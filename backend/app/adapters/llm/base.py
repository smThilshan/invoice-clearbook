"""The ExtractionClient port: send page images + prompt + JSON schema, get the model's raw answer.

Transport only. The client does not interpret the answer: the pipeline validates `raw_output`
with `Invoice.model_validate_json` (exact Decimals) and turns schema errors into review issues.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    prompt: str
    json_schema: Mapping[str, Any]  # enforced as the output format by the model server
    images: Sequence[bytes] = ()  # PNG or JPEG page images, in page order
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    model: str
    raw_output: str
    truncated: bool  # output hit the token limit; raw_output is likely incomplete JSON
    duration_ms: int
    prompt_tokens: int | None
    output_tokens: int | None


class ExtractionError(Exception):
    """The model server was unreachable, timed out, or returned an unusable response.

    Safe to retry. Never carries document content.
    """


class ExtractionClient(Protocol):
    @property
    def model(self) -> str: ...

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...

    def close(self) -> None: ...
