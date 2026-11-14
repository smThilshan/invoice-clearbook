"""What every document posted to Xero shares, bills and expenses alike: the original attached,
whether it was deleted there, and what a Xero failure means for the posting worker."""

from urllib.parse import quote

from app.adapters.accounting.base import (
    Attachment,
    PostingAccessDenied,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.xero.client import XeroApiError, XeroClient

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # Xero's limit for a file attached through the API
GONE_STATUSES = frozenset({"DELETED", "VOIDED"})


def posting_error(exc: XeroApiError) -> Exception:
    """Xero's failure as the posting worker reads it: reconnect, try later, or back to review."""
    if exc.access:
        return PostingAccessDenied(str(exc), reconnect=exc.access == 403)
    if exc.temporary:
        return PostingUnavailable(str(exc), exc.retry_after)
    return PostingRefused("; ".join(exc.messages) or str(exc))


def attach(xero: XeroClient, resource: str, file: Attachment | None) -> bool:
    """Attach the original to `resource` ("Invoices/<id>") once: a retry finds it there.
    False = not attached; the document itself is fine and the original stays in Missan's
    folders."""
    if file is None or len(file.content) > MAX_ATTACHMENT_BYTES:
        return False
    attachments = f"{resource}/Attachments"
    there = xero.get(attachments).get("Attachments", [])
    if any(a.get("FileName") == file.file_name for a in there):
        return True
    try:
        xero.put_file(f"{attachments}/{quote(file.file_name)}", file.content, file.mime_type)
    except XeroApiError as exc:
        if exc.temporary:
            raise
        return False  # refused (type or size)
    return True


def is_gone(xero: XeroClient, collection: str, external_id: str) -> bool:
    """Xero keeps a deleted or voided document with that status; an unknown ID is gone too.
    `collection`: "Invoices" or "BankTransactions"."""
    try:
        found = xero.get(f"{collection}/{quote(external_id)}").get(collection) or []
    except XeroApiError as exc:
        if exc.status == 404:
            return True
        if exc.access:
            raise PostingAccessDenied(str(exc), reconnect=exc.access == 403) from exc
        raise PostingUnavailable(str(exc), exc.retry_after) from exc
    return not found or found[0].get("Status") in GONE_STATUSES
