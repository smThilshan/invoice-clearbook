"""Celery tasks. Thin: they call pipeline/services and are safe to re-run.

Names are explicit ("<area>.<action>") so they stay stable when code moves; "posting.*" tasks
are routed to the posting queue, everything else to the pipeline queue. Arguments are plain
strings (JSON). Jobs are enqueued only after the transaction that created their rows commits.

Chain: scan_run -> ingest_file -> read_document -> extract_document (services/pipeline.py).
A step that fails unexpectedly marks the document FAILED (reason = step + error type, never
document content; the traceback goes to the log). If another worker moved the document first,
the step stops quietly ("superseded") instead of failing it.
"""

import logging
import uuid
from contextlib import closing

from celery import Task
from sqlalchemy.exc import IntegrityError

from app.adapters.llm import ExtractionError, get_extraction_client
from app.adapters.ocr import get_ocr_engine
from app.adapters.storage import get_storage
from app.core.config import get_settings
from app.core.db import session_scope
from app.domain.enums import DocumentStatus
from app.domain.state_machine import StaleStatusError
from app.models import Document, Run
from app.pipeline import extract, ingest, read
from app.services.pipeline import NEXT_STEP, Step, fail_document
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

NOT_READY_RETRY_SECONDS = 30
NOT_READY_MAX_RETRIES = 20  # ~10 minutes for a slow copy into the inbox
MODEL_RETRY_SECONDS = 60
MODEL_MAX_RETRIES = 3  # model server down or timed out

# Another worker changed the document between our read and write: not an error.
SUPERSEDED = (StaleStatusError, IntegrityError)


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
    if result.outcome is ingest.Outcome.CREATED and result.document_id is not None:
        enqueue_next(str(result.document_id), DocumentStatus.CAPTURED)
    return result.outcome.value


@celery_app.task(name="pipeline.read_document")
def read_document(document_id: str) -> str:
    """Page images + text for a CAPTURED document; skips documents already past that."""
    settings = get_settings()
    try:
        with session_scope() as session:
            document = session.get(Document, uuid.UUID(document_id))
            if document is None:
                logger.error("read_document: document %s not found", document_id)
                return "document_not_found"
            result = read.read_document(
                session,
                get_storage(settings),
                get_ocr_engine,
                document,
                max_pages=settings.max_pages,
                max_side=settings.page_image_max_side,
                min_text_chars=settings.text_layer_min_chars,
            )
            status = document.status
    except SUPERSEDED:
        logger.info("document %s: read superseded by another worker", document_id)
        return "superseded"
    except Exception as exc:
        logger.exception("document %s: read crashed", document_id)
        _fail(document_id, Step.READ, _unexpected(exc))
        return "failed"

    logger.info(
        "document %s: read %s pages=%d ocr_pages=%d seconds=%.1f",
        document_id,
        result.outcome,
        result.pages,
        result.ocr_pages,
        result.seconds,
    )
    enqueue_next(document_id, status)
    return result.outcome.value


@celery_app.task(name="pipeline.extract_document", bind=True, max_retries=MODEL_MAX_RETRIES)
def extract_document(self: "Task[[str], str]", document_id: str) -> str:
    """Model extraction for a READ document; retried if the model server fails."""
    settings = get_settings()
    try:
        with session_scope() as session, closing(get_extraction_client(settings)) as client:
            document = session.get(Document, uuid.UUID(document_id))
            if document is None:
                logger.error("extract_document: document %s not found", document_id)
                return "document_not_found"
            result = extract.extract_document(
                session,
                get_storage(settings),
                client,
                document,
                max_images=settings.extract_max_images,
            )
            status = document.status
    except ExtractionError as exc:
        if self.request.retries < MODEL_MAX_RETRIES:
            logger.warning("document %s: extraction failed (%s), will retry", document_id, exc)
            raise self.retry(countdown=MODEL_RETRY_SECONDS, exc=exc) from exc
        reason = f"model unavailable after {MODEL_MAX_RETRIES + 1} attempts ({exc})"
        _fail(document_id, Step.EXTRACT, reason)
        return "failed"
    except SUPERSEDED:
        logger.info("document %s: extract superseded by another worker", document_id)
        return "superseded"
    except Exception as exc:
        logger.exception("document %s: extract crashed", document_id)
        _fail(document_id, Step.EXTRACT, _unexpected(exc))
        return "failed"

    logger.info("document %s: extract %s attempt=%d", document_id, result.outcome, result.attempt)
    enqueue_next(document_id, status)
    return result.outcome.value


def enqueue_next(document_id: str, status: DocumentStatus) -> bool:
    """Queue the step a document in `status` needs. False = nothing to queue (yet)."""
    match NEXT_STEP.get(status):
        case Step.READ:
            read_document.delay(document_id)
        case Step.EXTRACT:
            extract_document.delay(document_id)
        case None:
            return False  # EXTRACTED waits for Verify (Phase 3); end states need nothing
    return True


def _fail(document_id: str, step: Step, reason: str) -> None:
    with session_scope() as session:
        document = session.get(Document, uuid.UUID(document_id))
        if document is not None and fail_document(session, document, step=step, reason=reason):
            logger.warning("document %s: FAILED at %s: %s", document_id, step, reason)


def _unexpected(exc: Exception) -> str:
    # Type only: exception messages can echo document content. The traceback is logged.
    return f"unexpected error ({type(exc).__name__})"
