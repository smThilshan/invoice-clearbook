import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.domain.enums import Direction, DocumentStatus
from app.models import Client, Document, Run

CLIENT = "test-client"


def make_client(session: Session, inbox: Path, client_id: str = CLIENT) -> Client:
    """A restaurant whose shared-drive folder is `inbox`."""
    inbox.mkdir(parents=True, exist_ok=True)
    client = Client(client_id=client_id, name=f"Test {client_id}", inbox_path=str(inbox))
    session.add(client)
    session.flush()
    return client


def make_run(session: Session) -> Run:
    run = Run(client_id=CLIENT, direction=Direction.PURCHASE, folder_date=date(2026, 10, 20))
    session.add(run)
    session.flush()
    return run


def make_document(session: Session, **overrides: Any) -> Document:
    fields: dict[str, Any] = {
        "client_id": CLIENT,
        "direction": Direction.PURCHASE,
        "folder_date": date(2026, 10, 20),
        "source_path": "Purchase/2026-10-20/inv.pdf",
        "storage_key": f"originals/{uuid.uuid4()}.pdf",
        "sha256": uuid.uuid4().hex * 2,
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "status": DocumentStatus.CAPTURED,
    }
    fields.update(overrides)
    if "run_id" not in fields:
        fields["run_id"] = make_run(session).id
    document = Document(**fields)
    session.add(document)
    session.flush()
    return document
