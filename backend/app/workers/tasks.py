"""Celery tasks. Thin: they call pipeline/services and are safe to re-run.

Names are explicit ("<area>.<action>") so they stay stable when code moves; "posting.*" tasks
are routed to the posting queue, everything else to the pipeline queue. Arguments are plain
strings (JSON). Jobs are enqueued only after the transaction that created their rows commits.
"""

import logging
import uuid

from celery import Task

from app.adapters.storage import get_storage
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Run
from app.pipeline import ingest
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

NOT_READY_RETRY_SECONDS = 30
NOT_READY_MAX_RETRIES = 20  # ~10 minutes for a slow copy into the inbox


@celery_app.task(name="system.ping")
def ping() -> str:
    """Proves a worker is consuming jobs end to end."""
    return "pong"


@celery_app.task(name="pipeline.scan_run")
def scan_run(run_id: str) -> int:
    """List the run's files and enqueue one ingest job per file. Re-running re-enqueues every
    file; ingest recognises files it has already seen."""
    settings = get_settings()
    with session_scope() as session:
        run = session.get(Run, uuid.UUID(run_id))
        if run is None:
            logger.error("scan_run: run %s not found", run_id)
            return 0
        relative_paths = ingest.scan_run(session, run, settings.inbox_dir)

    for relative_path in relative_paths:
        ingest_file.delay(run_id, relative_path)
    logger.info("run %s: %d file(s) queued for ingest", run_id, len(relative_paths))
    return len(relative_paths)


@celery_app.task(name="pipeline.ingest_file", bind=True, max_retries=NOT_READY_MAX_RETRIES)
def ingest_file(self: "Task[[str, str], str]", run_id: str, relative_path: str) -> str:
    settings = get_settings()
    with session_scope() as session:
        run = session.get(Run, uuid.UUID(run_id))
        if run is None:
            logger.error("ingest_file: run %s not found", run_id)
            return "run_not_found"
        result = ingest.ingest_file(
            session,
            get_storage(settings),
            run=run,
            inbox_dir=settings.inbox_dir,
            relative_path=relative_path,
            max_bytes=settings.max_file_mb * 1_048_576,
            stable_seconds=settings.stable_file_seconds,
        )

    if result.outcome is ingest.Outcome.NOT_READY:
        raise self.retry(countdown=NOT_READY_RETRY_SECONDS)
    # Document id only: file names can contain vendor names.
    logger.info("run %s: ingest %s document=%s", run_id, result.outcome, result.document_id)
    return result.outcome.value
