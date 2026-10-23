"""Application settings, loaded from environment variables and the repo-root `.env`."""

from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from app.domain.enums import AccountingProvider

# backend/app/core/config.py -> repo root. `make` runs from backend/, so a relative path misses it.
REPO_ROOT = Path(__file__).resolve().parents[3]

_PLACEHOLDER_SECRETS = frozenset({"", "change-me"})

AppEnv = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LlmProvider = Literal["ollama"]  # "vllm" arrives with its client (Phase 6)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds values for docker compose and later tasks
    )

    app_env: AppEnv = "dev"
    log_level: LogLevel = "INFO"
    default_client_id: str = "missan"

    postgres_user: str = "clearbook"
    postgres_password: SecretStr = SecretStr("change-me")
    postgres_db: str = "clearbook"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Background jobs (Celery + Redis).
    redis_url: str = "redis://localhost:6379/0"
    # A job running longer than this is killed (the model call has its own, shorter timeout).
    job_time_limit_seconds: int = Field(default=1800, gt=0)
    # Redis re-delivers a job that is not acknowledged within this time. Must exceed the time
    # limit, or a slow job would be handed to a second worker while the first still runs it.
    job_visibility_timeout_seconds: int = Field(default=3600, gt=0)

    # Charges outside the item lines (task 4.13): words that mark one when it is not printed
    # between the subtotal and the total. Configuration, so a new kind needs no code change.
    charge_hint_words: list[str] = Field(
        default_factory=lambda: [
            "delivery",
            "service",
            "fee",
            "charge",
            "packaging",
            "packing",
            "handling",
            "freight",
            "shipping",
            "tip",
            "rounding",
            "round off",
            "discount",
            "promo",
            "voucher",
            "coupon",
            "توصيل",
            "خدمة",
            "رسوم",
            "خصم",
        ]
    )

    # Places the Run screen may browse and run from (decision 35): only folders inside these
    # are ever read. A client's own folder is offered too. Missing places are left out.
    folder_places: list[Path] = Field(default_factory=lambda: [Path.home(), Path("/Volumes")])

    # Our own file store (via the Storage interface). Relative paths are resolved against the
    # repo root.
    store_dir: Path = Field(default=Path("data/store"), validate_default=True)
    max_file_mb: int = Field(default=25, gt=0)  # larger files become FAILED documents
    # A file is ingested only if its size and mtime are unchanged over this interval, so a
    # file still being copied into the inbox is never read half-written.
    stable_file_seconds: float = Field(default=2.0, ge=0)

    # Read step (task 2.4): page images for the model and the Workbench, text per page.
    max_pages: int = Field(default=20, gt=0)  # longer files become FAILED documents
    page_image_max_side: int = Field(default=1500, ge=500)  # architecture §16
    # A PDF page with at least this many text-layer characters is digital (no OCR needed).
    text_layer_min_chars: int = Field(default=20, ge=1)
    # PaddleOCR (decision 22). Mobile models: ~12x faster than server models on CPU.
    ocr_models_dir: Path = Field(default=Path("data/models/paddlex"), validate_default=True)
    ocr_detection_model: str = "PP-OCRv5_mobile_det"
    ocr_recognition_model: str = "PP-OCRv5_mobile_rec"

    # Model server (decision 3): Ollama native API in dev, vLLM in prod; switching is config only.
    llm_provider: LlmProvider = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3-vl:4b-instruct"
    # Image + OCR text + schema must fit, or the server silently truncates the input.
    llm_context_tokens: int = Field(default=16384, ge=2048)
    llm_timeout_seconds: float = Field(default=600, gt=0)  # CPU inference on dev is slow
    # Page images sent per document (~1.5-2k tokens each); text is sent for every page.
    extract_max_images: int = Field(default=3, ge=1)

    # Validation tolerances (task 3.2). Shops round VAT per item, so allow this much per line.
    vat_tolerance_per_line: Decimal = Field(default=Decimal("0.01"), ge=0)
    # Warn if the invoice is older than this compared with its inbox folder date.
    period_max_days_before: int = Field(default=90, ge=0)
    # An invoice dated more than this many days after today is an error (misread date).
    future_date_slack_days: int = Field(default=1, ge=0)

    # Xero (task 5.3): the app registered in the Xero developer portal. Secrets only in .env.
    xero_client_id: str = ""
    xero_client_secret: SecretStr = SecretStr("")
    xero_redirect_uri: str = "http://localhost:8000/xero/callback"
    # Apps created after 2 March 2026 must ask for granular scopes (Xero, 2026).
    xero_scopes: str = (
        "offline_access accounting.settings accounting.contacts accounting.invoices "
        "accounting.payments accounting.banktransactions accounting.attachments"
    )
    # Encrypts the accounting system tokens stored in the database (`make secret-key`).
    token_key: SecretStr = SecretStr("")
    # Where the browser returns after connecting an accounting system.
    workbench_url: str = "http://127.0.0.1:5173"

    # Accounting system of the (single, for now) client: whose master data is matched against.
    # Moves to a per-client record when there are several clients (decision 20).
    accounting_provider: AccountingProvider = AccountingProvider.MANUAL
    # Supplier name match (pg_trgm similarity 0-1): match at or above, suggest at or above.
    vendor_match_min_similarity: float = Field(default=0.6, gt=0, le=1)
    vendor_suggest_min_similarity: float = Field(default=0.35, gt=0, le=1)
    # Coding (task 3.6): a supplier's learned account is used after this many consistent
    # approvals; before that the model suggests one (architecture §9).
    coding_min_consistent_approvals: int = Field(default=2, ge=1)
    # "Today" for date checks is the business date where Missan works, not the server's
    # (often UTC) clock: otherwise "in the future" is off by a day around midnight.
    business_timezone: str = "Asia/Dubai"

    @field_validator("business_timezone")
    @classmethod
    def _known_timezone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {name!r}") from exc
        return name

    def business_today(self) -> date:
        return datetime.now(ZoneInfo(self.business_timezone)).date()

    @field_validator("store_dir", "ocr_models_dir")
    @classmethod
    def _resolve_against_repo_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def database_url(self) -> URL:
        # URL escapes the password and masks it in repr().
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @model_validator(mode="after")
    def _visibility_timeout_exceeds_time_limit(self) -> Self:
        if self.job_visibility_timeout_seconds <= self.job_time_limit_seconds:
            raise ValueError(
                "JOB_VISIBILITY_TIMEOUT_SECONDS must be greater than JOB_TIME_LIMIT_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def _no_placeholder_secrets_in_prod(self) -> Self:
        if self.app_env == "prod" and (
            self.postgres_password.get_secret_value() in _PLACEHOLDER_SECRETS
        ):
            raise ValueError("POSTGRES_PASSWORD must be set to a real value in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
