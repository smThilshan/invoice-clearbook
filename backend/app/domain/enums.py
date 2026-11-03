"""Canonical enums. Accounting-system codes (e.g. Xero's ACCPAY) never appear here."""

from enum import StrEnum


class Direction(StrEnum):
    """Declared by the inbox folder the file was dropped into."""

    PURCHASE = "PURCHASE"
    SALES = "SALES"
    UNSORTED = "UNSORTED"


class DocumentType(StrEnum):
    BILL = "BILL"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"
    RECEIPT = "RECEIPT"
    SALES_INVOICE = "SALES_INVOICE"
    NON_POSTABLE = "NON_POSTABLE"  # quote, PO, proforma, statement, delivery note, remittance


class TaxType(StrEnum):
    STANDARD_5 = "STANDARD_5"
    ZERO = "ZERO"
    EXEMPT = "EXEMPT"
    REVERSE_CHARGE = "REVERSE_CHARGE"
    NONE = "NONE"


class AccountClass(StrEnum):
    """Top-level ledger account class; every accounting system maps its account types to one."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class Role(StrEnum):
    """What a Missan user may do (decision 71). Every user works on every client."""

    ACCOUNTANT = "ACCOUNTANT"  # documents: run, review, approve, send, reject, delete rejected
    ADMIN = "ADMIN"  # also the accounting system connection and master data


class AccountingProvider(StrEnum):
    """Which adapter a client's master data and postings go through (decision 20)."""

    MANUAL = "MANUAL"  # no connection: CSV master data in, export file out
    XERO = "XERO"


class LineGrouping(StrEnum):
    """How a client's approved lines are sent to its accounting system (decisions 51, 73)."""

    BY_ACCOUNT = "BY_ACCOUNT"  # one line per account and VAT code: a clean ledger
    BY_ITEM = "BY_ITEM"  # item by item; identical items one line with their quantity added


class AmountType(StrEnum):
    """Whether line amounts include VAT."""

    INCLUSIVE = "INCLUSIVE"
    EXCLUSIVE = "EXCLUSIVE"
    NO_TAX = "NO_TAX"


class QueueCategory(StrEnum):
    """Verification result shown in the Workbench."""

    READY = "READY"  # no errors, no warnings
    CHECK = "CHECK"  # warnings
    EXCEPTION = "EXCEPTION"  # errors, duplicate or type conflict


class DocumentStatus(StrEnum):
    """Lifecycle of a document. Moves only through `state_machine`."""

    CAPTURED = "CAPTURED"
    READ = "READ"
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    POSTING = "POSTING"
    POSTED = "POSTED"
    SYNCED = "SYNCED"
    REJECTED = "REJECTED"
    POST_ERROR = "POST_ERROR"
    VOIDED = "VOIDED"
    DUPLICATE = "DUPLICATE"
    NON_POSTABLE = "NON_POSTABLE"
    FAILED = "FAILED"
