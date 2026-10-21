"""SQLAlchemy tables. Every table has client_id."""

from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.client import Client
from app.models.document import Correction, Document, Extraction
from app.models.master_data import Contact, LedgerAccount, TaxRate
from app.models.outbox import Outbox, OutboxStatus
from app.models.run import Run, RunStatus
from app.models.vendor_mapping import VendorMapping

__all__ = [
    "AuditLog",
    "Base",
    "Client",
    "Contact",
    "Correction",
    "Document",
    "Extraction",
    "LedgerAccount",
    "Outbox",
    "OutboxStatus",
    "Run",
    "RunStatus",
    "TaxRate",
    "VendorMapping",
]
