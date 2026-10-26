"""Celery application. Start with `make worker`.

Reliability settings, because a job may run for minutes (model on CPU) and must never be lost
or run twice in parallel:
- acks_late + reject_on_worker_lost: a job is acknowledged only after it finishes, so a crashed
  worker's job is delivered again. Tasks must therefore be safe to re-run (CLAUDE.md).
- prefetch 1: a worker holds only the job it is running, so a long job never blocks others.
- visibility timeout > time limit (enforced in Settings): Redis never re-delivers a job that
  is still running.
- no result backend: document status lives in Postgres, the single source of truth.
- queue order (decision 31): a worker always takes a waiting quick job (read, verify, posting)
  before the next model extraction, which waits in its own queue. So each document reaches
  review as soon as its extraction is done, not after the whole run. (`redis_transport.py` keeps
  that order: kombu alone does not.) In production the
  extract queue can have its own workers, sized to the model server.

Pool: Linux (production) uses the default prefork pool, which enforces the hard time limit.
macOS dev uses `--pool=solo` (see Makefile): prefork children start without Celery's
per-process setup on macOS and every job fails. Solo cannot enforce the hard limit; the model
call has its own timeout.
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging
from kombu import Exchange, Queue

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

PIPELINE_QUEUE = "pipeline"  # quick steps: scan, ingest, read, verify
POSTING_QUEUE = "posting"  # accounting-system calls; never delayed by slow model work
EXTRACT_QUEUE = "extract"  # model extraction: minutes per document on CPU
# Workers take from the first non-empty queue in this order (Redis "priority" strategy).
QUEUE_ORDER = (PIPELINE_QUEUE, POSTING_QUEUE, EXTRACT_QUEUE)


def _queue(name: str) -> Queue:
    # Explicit exchange + key per queue; implicit declarations bound "posting" to the
    # pipeline exchange (found by the end-to-end worker check in task 2.2).
    return Queue(name, Exchange(name, type="direct"), routing_key=name)


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("clearbook", broker=settings.redis_url, include=["app.workers.tasks"])
    app.conf.update(
        # kombu's own Redis transport loses QUEUE_ORDER (see redis_transport.py)
        broker_transport="app.workers.redis_transport:Transport",
        task_queues=tuple(_queue(name) for name in QUEUE_ORDER),
        task_default_queue=PIPELINE_QUEUE,
        task_routes={
            "posting.*": {"queue": POSTING_QUEUE},
            "pipeline.extract_document": {"queue": EXTRACT_QUEUE},
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=settings.job_time_limit_seconds,
        broker_transport_options={
            "visibility_timeout": settings.job_visibility_timeout_seconds,
            "queue_order_strategy": "priority",  # always QUEUE_ORDER, never round robin
        },
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
    )
    return app


@setup_logging.connect
def _use_app_logging(**_: Any) -> None:
    """Keep our formatter (with TRN/IBAN masking) instead of Celery's own logging setup."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env == "prod")


celery_app = create_celery_app()
