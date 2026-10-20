"""Pipeline admin commands (a worker must be running to process what they queue).

    make resume                      # re-queue documents stranded between steps
    make retry-document ID=<uuid>    # send a FAILED document back to the start

The Workbench gets these as buttons later (task 4.2, behind login).
"""

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.db import session_scope
from app.domain.state_machine import InvalidTransitionError
from app.models import Document
from app.services.pipeline import retry_document, stalled_documents
from app.workers.tasks import enqueue_next


def resume() -> int:
    idle_for = timedelta(seconds=get_settings().job_time_limit_seconds)
    with session_scope() as session:
        stalled = [
            (str(doc.id), doc.status)
            for doc in stalled_documents(session, idle_for=idle_for, now=datetime.now(UTC))
        ]
    for document_id, status in stalled:
        enqueue_next(document_id, status)
        print(f"queued {document_id} ({status})")
    print(f"{len(stalled)} stalled document(s) re-queued (idle > {idle_for})")
    return 0


def retry(document_id: str) -> int:
    with session_scope() as session:
        document = session.get(Document, uuid.UUID(document_id))
        if document is None:
            print(f"document {document_id} not found", file=sys.stderr)
            return 1
        try:
            retry_document(session, document, actor="admin:cli")
        except InvalidTransitionError:
            print(f"only FAILED documents can be retried (status: {document.status})")
            return 1
        status = document.status
    enqueue_next(document_id, status)
    print(f"document {document_id} sent back to {status} and queued")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resume", help="re-queue documents stranded between steps")
    retry_cmd = commands.add_parser("retry", help="send a FAILED document back to the start")
    retry_cmd.add_argument("document_id")
    args = parser.parse_args()
    return resume() if args.command == "resume" else retry(args.document_id)


if __name__ == "__main__":
    sys.exit(main())
