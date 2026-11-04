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
LlmProvider = Literal["lmstudio", "ollama"]  # "vllm" arrives with its client (Phase 6)


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

    # Decision 44: when a line printed only its total ("2 x Water 7.98"), the unit price is
    # worked out from it, only when the document proves it. False turns this off.
    repair_unit_prices: bool = True

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
    # Live (decision 77): the built Workbench (`workbench/dist`). Set, one address serves it at
    # / and the API under /api, the paths the Workbench calls; empty in development (Vite).
    web_dist_dir: Path | None = None
    # Folders chosen on an accountant's own computer (decision 78) are saved here, one folder per
    # upload, and run like any folder; the invoices are then copied into the store as usual.
    upload_dir: Path = Field(default=Path("data/uploads"), validate_default=True)
    # One upload at most (Starlette refuses more than 1000 files in one form anyway).
    max_upload_files: int = Field(default=1000, ge=1, le=1000)
    max_upload_mb: int = Field(default=1024, gt=0)
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

    # Model server (decisions 3, 46): LM Studio (MLX) in dev, vLLM in prod; switch by config only.
    llm_provider: LlmProvider = "lmstudio"
    llm_base_url: str = "http://localhost:1234"
    llm_model: str = "qwen/qwen3-vl-4b"
    # Image + OCR text + schema must fit, or the server silently truncates the input.
    llm_context_tokens: int = Field(default=16384, ge=2048)
    # Decision 80: one file never holds the queue for long. On the M4 a read takes 15 s (slowest
    # 39 s, longest answer 994 tokens); a model stuck repeating itself is stopped at the cap,
    # which marks the answer cut off (flagged in review, not retried), and anything else that
    # hangs ends at the deadline. Raise both for a slow CPU-only model server.
    llm_timeout_seconds: float = Field(default=180, gt=0)
    llm_max_output_tokens: int = Field(default=3000, ge=256)
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
    # Zoho Books (decision 74): a server-based client in the Zoho API console, Multi-DC on with
    # the same credentials for every data centre. Secrets only in .env.
    zoho_client_id: str = ""
    zoho_client_secret: SecretStr = SecretStr("")
    zoho_redirect_uri: str = "http://localhost:8000/zoho/callback"
    zoho_scopes: str = (
        "ZohoBooks.bills.ALL,ZohoBooks.contacts.ALL,ZohoBooks.settings.READ,"
        "ZohoBooks.accountants.READ"
    )
    # Encrypts the accounting system tokens stored in the database (`make secret-key`).
    token_key: SecretStr = SecretStr("")
    # Where the browser returns after connecting an accounting system.
    workbench_url: str = "http://127.0.0.1:5173"

    # Login (decision 71): signs the session cookie (`make jwt-secret`). Empty, the placeholder or
    # too short: nobody can log in, rather than sign with a guessable secret.
    jwt_secret: SecretStr = SecretStr("")
    session_hours: int = Field(default=24, ge=1, le=24 * 7)
    # The cookie only travels over HTTPS. Off for http://127.0.0.1 in development only.
    cookie_secure: bool = False
    login_max_failures: int = Field(default=5, ge=1)  # then locked for login_lock_minutes
    login_lock_minutes: int = Field(default=15, ge=1)

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

    @field_validator("store_dir", "ocr_models_dir", "web_dist_dir", "upload_dir")
    @classmethod
    def _resolve_against_repo_root(cls, path: Path | None) -> Path | None:
        return path if path is None or path.is_absolute() else REPO_ROOT / path

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
