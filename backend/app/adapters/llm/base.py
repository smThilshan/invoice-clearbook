"""The ExtractionClient port: send page images + prompt + JSON schema, get the model's raw answer.

Transport only. The client does not interpret the answer: the pipeline validates `raw_output`
with `Invoice.model_validate_json` (exact Decimals) and turns schema errors into review issues.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ModelStatus(StrEnum):
    """Is the configured model ready to extract? (decision 2: Instruct, with vision)"""

    OK = "ok"
    UNREACHABLE = "unreachable"  # model server down or not answering
    MISSING = "missing"  # server up, configured model not installed
    INCOMPATIBLE = "incompatible"  # installed, but a Thinking variant or without vision


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
    reading_ms: int | None = None  # model processing the input (images + text)
    writing_ms: int | None = None  # model generating the answer


class ExtractionError(Exception):
    """The model server was unreachable, timed out, or returned an unusable response.

    Safe to retry. Never carries document content.
    """


class ExtractionStopped(Exception):  # noqa: N818 - a stop, deliberately not an error
    """The user force-stopped the run while the model was reading (task 4.20). Not an error:
    nothing of the answer is used, nothing is retried; Resume reads the document again."""


class ExtractionClient(Protocol):
    @property
    def model(self) -> str: ...

    def extract(
        self, request: ExtractionRequest, *, should_stop: Callable[[], bool] | None = None
    ) -> ExtractionResult:
        """`should_stop` (task 4.20) is asked every few seconds while waiting for the model;
        when it answers True the call is abandoned and ExtractionStopped is raised."""
        ...

    def check(self) -> ModelStatus:
        """Cheap readiness check: no generation, a few seconds at most."""
        ...

    def close(self) -> None: ...
