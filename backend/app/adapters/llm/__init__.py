"""ExtractionClient: the only way the app calls the model (Ollama in dev, vLLM in prod)."""

from typing import assert_never

from app.adapters.llm.base import (
    ExtractionClient,
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
)
from app.adapters.llm.ollama import OllamaExtractionClient
from app.core.config import Settings, get_settings

__all__ = [
    "ExtractionClient",
    "ExtractionError",
    "ExtractionRequest",
    "ExtractionResult",
    "get_extraction_client",
]


def get_extraction_client(settings: Settings | None = None) -> ExtractionClient:
    """Build the client from settings; model name and URL never come from anywhere else."""
    settings = settings or get_settings()
    match settings.llm_provider:
        case "ollama":
            return OllamaExtractionClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                context_tokens=settings.llm_context_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        case _:
            assert_never(settings.llm_provider)
