"""Celery tasks. Thin: they call pipeline/services and are safe to re-run.

Names are explicit ("<area>.<action>") so they stay stable when code moves; "posting.*" tasks
are routed to the posting queue, model extraction to the extract queue (taken last, decision
31), everything else to the pipeline queue. Arguments are plain
strings (JSON). Jobs are enqueued only after the transaction that created their rows commits.

Chain: scan_run -> ingest_file -> read_document -> extract_document -> verify_document
(services/pipeline.py).
A step that fails unexpectedly marks the document FAILED (reason = step + error type, never
document content; the traceback goes to the log). If another worker moved the document first,
the step stops quietly ("superseded") instead of failing it.
"""

import logging
import uuid
from contextlib import closing
from pathlib import Path

from celery import Task
from celery.signals import worker_ready
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.adapters.llm import ExtractionError, ExtractionStopped, get_extraction_client
from app.adapters.ocr import get_ocr_engine
from app.adapters.storage import get_storage
from app.core.config import get_settings
from app.core.db import session_scope
from app.domain.enums import DocumentStatus
from app.domain.state_machine import StaleStatusError
from app.models import Document, Run, RunStatus
from app.pipeline import extract, ingest, read
from app.pipeline.folders import InboxError
from app.pipeline.verify import step as verify
from app.services import posting
from app.services.clients import get_client, run_root
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
    """List the run's files (sub-folders included) and enqueue one ingest job per file.
    Re-running re-enqueues every file; ingest recognises files it has already seen. If the
    client's shared folder cannot be read, the run is marked FAILED with the reason."""
    with session_scope() as session:
        run = session.get(Run, uuid.UUID(run_id))
        if run is None:
            logger.error("scan_run: run %s not found", run_id)
            return 0
        if run.status is RunStatus.STOPPED:
            return 0  # stopped by the user (task 4.19): Resume scans again
        try:
            relative_paths = ingest.scan_run(session, run, _client_root(session, run))
        except InboxError as exc:
            run.status, run.failure_reason, run.files = RunStatus.FAILED, str(exc), []
            logger.warning("run %s: FAILED: %s", run_id, exc)
            return 0

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
        if run.status is RunStatus.STOPPED:
            return STOPPED  # left for Resume
        try:
            result = ingest.ingest_file(
                session,
                get_storage(settings),
                run=run,
                client_root=_client_root(session, run),
                relative_path=relative_path,
                max_bytes=settings.max_file_mb * 1_048_576,
                stable_seconds=settings.stable_file_seconds,
            )
        except InboxError as exc:  # removed or unreachable since the scan listed it
            logger.warning("run %s: file not ingested: %s", run_id, exc)
            return "unreadable"

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
    if _run_stopped(document_id):
        return STOPPED  # the run was stopped (task 4.19): left as it is for Resume
    _mark_working(document_id, Step.READ)
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
    if _run_stopped(document_id):
        return STOPPED  # the run was stopped (task 4.19): left as it is for Resume
    _mark_working(document_id, Step.EXTRACT)
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
                should_stop=lambda: _force_stop_asked(document_id),
            )
            status = document.status
    except ExtractionStopped:
        # Force stop (task 4.20): nothing was saved; the document stays READ for Resume.
        logger.info("document %s: extract force-stopped by the user", document_id)
        _clear_working(document_id)
        return STOPPED
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


@celery_app.task(name="pipeline.verify_document")
def verify_document(document_id: str) -> str:
    """All Phase 3 checks for an EXTRACTED document, then the review queue. Coding may call
    the model; if it is down, coding degrades to a warning instead of failing verification."""
    settings = get_settings()
    if _run_stopped(document_id):
        return STOPPED  # the run was stopped (task 4.19): left as it is for Resume
    _mark_working(document_id, Step.VERIFY)
    try:
        with session_scope() as session, closing(get_extraction_client(settings)) as client:
            document = session.get(Document, uuid.UUID(document_id))
            if document is None:
                logger.error("verify_document: document %s not found", document_id)
                return "document_not_found"
            result = verify.verify_document(
                session,
                get_storage(settings),
                client,
                document,
                settings=settings,
                today=settings.business_today(),
                ocr=get_ocr_engine,  # loaded only for a total that does not add up (4.13)
            )
    except SUPERSEDED:
        logger.info("document %s: verify superseded by another worker", document_id)
        return "superseded"
    except Exception as exc:
        logger.exception("document %s: verify crashed", document_id)
        _fail(document_id, Step.VERIFY, _unexpected(exc))
        return "failed"

    logger.info(
        "document %s: verify %s queue=%s errors=%d warnings=%d",
        document_id,
        result.outcome,
        result.queue,
        result.errors,
        result.warnings,
    )
    return result.outcome.value


STOPPED = "stopped"


def _run_stopped(document_id: str) -> bool:
    """Has the user stopped this document's run? (task 4.19) Checked before every step."""
    with session_scope() as session:
        status = session.scalar(
            select(Run.status)
            .join(Document, Document.run_id == Run.id)
            .where(Document.id == uuid.UUID(document_id))
        )
    return status is RunStatus.STOPPED


def _force_stop_asked(document_id: str) -> bool:
    """Did the user force-stop this document's run? (task 4.20) Asked every few seconds."""
    with session_scope() as session:
        asked = session.scalar(
            select(Run.force_stop)
            .join(Document, Document.run_id == Run.id)
            .where(Document.id == uuid.UUID(document_id))
        )
    return asked is True


def _clear_working(document_id: str) -> None:
    """The step stopped: the document is no longer being worked on."""
    with session_scope() as session:
        session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(document_id))
            .values(working_step=None, working_since=None)
        )


def _mark_working(document_id: str, step: Step) -> None:
    """Live progress (task 4.14): this step starts now. Its own short transaction, so the run
    page sees it while the step runs. Never blocks the step: a failure here is only logged."""
    try:
        with session_scope() as session:
            session.execute(
                update(Document)
                .where(Document.id == uuid.UUID(document_id))
                .values(working_step=step.value, working_since=func.now())
            )
    except Exception:
        logger.warning("document %s: could not mark step %s as started", document_id, step)


MAX_POSTS_PER_JOB = 50  # a sweep posts this many, then queues itself again


@celery_app.task(name="posting.send")
def send_posting(outbox_id: str | None = None, client_id: str | None = None) -> int:
    """Post one approved document (`outbox_id`), or every due one (of `client_id`, or of all
    clients). Safe to re-run: rows are claimed with SKIP LOCKED and resume from saved progress.
    A temporary failure queues the row again after its wait."""
    settings = get_settings()
    posted = 0
    for _ in range(MAX_POSTS_PER_JOB):
        outcome = posting.send_next(
            session_scope,
            settings,
            XeroAuth(settings),
            XeroAccountingSystem,
            get_storage(settings),
            outbox_id=outbox_id,
            client_id=client_id,
        )
        if outcome is None:
            return posted
        logger.info("outbox %s: %s", outcome.outbox_id, outcome.result)
        posted += outcome.result == "posted"
        if outcome.retry_in is not None:
            send_posting.apply_async(args=(outcome.outbox_id,), countdown=outcome.retry_in)
        if outbox_id:
            return posted
    send_posting.delay(None, client_id)  # more are due: continue in a new job
    return posted


@worker_ready.connect
def _post_waiting_on_start(**_: object) -> None:
    """Approved documents left waiting while no worker ran (or before a retry was due)."""
    send_posting.delay()


def enqueue_next(document_id: str, status: DocumentStatus) -> bool:
    """Queue the step a document in `status` needs. False = nothing to queue (yet)."""
    match NEXT_STEP.get(status):
        case Step.READ:
            read_document.delay(document_id)
        case Step.EXTRACT:
            extract_document.delay(document_id)
        case Step.VERIFY:
            verify_document.delay(document_id)
        case None:
            return False  # in review, parked or an end state: nothing to queue
    return True


def _fail(document_id: str, step: Step, reason: str) -> None:
    with session_scope() as session:
        document = session.get(Document, uuid.UUID(document_id))
        if document is not None and fail_document(session, document, step=step, reason=reason):
            logger.warning("document %s: FAILED at %s: %s", document_id, step, reason)


def _unexpected(exc: Exception) -> str:
    # Type only: exception messages can echo document content. The traceback is logged.
    return f"unexpected error ({type(exc).__name__})"


def _client_root(session: Session, run: Run) -> Path:
    """The run's root (decision 35): where its folder and files are."""
    client = get_client(session, run.client_id)
    if client is None:
        raise InboxError(f"client {run.client_id!r} not found or inactive")
    return run_root(run, client)
