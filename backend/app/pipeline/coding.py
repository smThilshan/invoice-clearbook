"""Coding (task 3.6, architecture §9): an account and a tax code for every line.

Account, cheapest reliable source first:
1. Supplier history (vendor_mappings) once it has enough consistent approvals: high confidence.
2. (Missan keyword rules: later, Phase 8.)
3. The model, text only, limited to the client's active expense/asset accounts: medium
   confidence, always for the accountant to confirm.
Tax type is decided by deterministic UAE VAT rules from the document (never the model), then
mapped to the client's own tax rate code. Only real, active codes are ever used (rule 14).

The suggestions go into the invoice's lines; `documents.coding` records their source and
confidence per line. `check_codes` is the "Codes" validation rule (architecture §8) and runs
again whenever the accountant edits.
"""

import json
import logging
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionClient, ExtractionError, ExtractionRequest
from app.domain.enums import (
    AccountClass,
    AccountingProvider,
    AmountType,
    DocumentType,
    TaxType,
)
from app.domain.invoice import Invoice
from app.models import LedgerAccount, TaxRate, VendorMapping
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import is_valid_trn, normalize_trn

logger = logging.getLogger(__name__)

HOME_CURRENCY = "AED"
# Purchase documents may only use these (architecture §8: bills use expense/asset accounts).
PURCHASE_ACCOUNT_CLASSES = frozenset({AccountClass.EXPENSE, AccountClass.ASSET})
CODING_PROMPT_VERSION = "coding-v1"

_SYSTEM_PROMPT = """You assign each line of a supplier document to one ledger account.
Use only account codes from the list. Choose by what was bought, not by the supplier alone.
If no account fits a line, use null."""


class Source(StrEnum):
    SUPPLIER_HISTORY = "supplier_history"
    MODEL = "model"
    VAT_RULES = "vat_rules"
    NONE = "none"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"  # a suggestion to confirm
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    account_class: AccountClass


@dataclass(frozen=True, slots=True)
class ChartOfAccounts:
    """The client's active master data needed for coding."""

    accounts: dict[str, Account]  # every active account with a code, by code
    tax_codes: dict[TaxType, str]  # canonical tax type -> the client's tax rate code

    def purchase_accounts(self) -> list[Account]:
        return [a for a in self.accounts.values() if a.account_class in PURCHASE_ACCOUNT_CLASSES]


@dataclass(frozen=True, slots=True)
class LineCoding:
    line: int
    account_code: str | None
    account_source: Source
    account_confidence: Confidence
    tax_type: TaxType | None
    tax_code: str | None
    tax_confidence: Confidence

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CodingResult:
    lines: list[LineCoding]
    issues: list[Issue]

    def apply(self, invoice: Invoice) -> Invoice:
        """The invoice with the suggested codes on its lines (a new object; rule 8: the
        extracted values themselves are untouched)."""
        coded = [
            line.model_copy(update={"account_code": c.account_code, "tax_type": c.tax_type})
            for line, c in zip(invoice.lines, self.lines, strict=True)
        ]
        return invoice.model_copy(update={"lines": coded})


def load_chart(
    session: Session, *, client_id: str, provider: AccountingProvider
) -> ChartOfAccounts:
    accounts = {
        account.code: Account(account.code, account.name, account.account_class)
        for account in session.scalars(
            select(LedgerAccount)
            .where(
                LedgerAccount.client_id == client_id,
                LedgerAccount.provider == provider,
                LedgerAccount.is_active.is_(True),
                LedgerAccount.code.is_not(None),
            )
            .order_by(LedgerAccount.code)
        )
        if account.code
    }
    tax_codes: dict[TaxType, str] = {}
    for rate in session.scalars(
        select(TaxRate)
        .where(
            TaxRate.client_id == client_id,
            TaxRate.provider == provider,
            TaxRate.is_active.is_(True),
            TaxRate.tax_type.is_not(None),
        )
        .order_by(TaxRate.code)
    ):
        if rate.tax_type is not None:
            tax_codes.setdefault(rate.tax_type, rate.code)
    return ChartOfAccounts(accounts, tax_codes)


def decide_tax_type(invoice: Invoice) -> tuple[TaxType | None, Confidence]:
    """UAE VAT rules (architecture §9), from what the document shows."""
    registered = is_valid_trn(normalize_trn(invoice.vendor_trn or ""))
    foreign = invoice.currency not in (None, HOME_CURRENCY)
    vat = invoice.vat
    if vat is not None and vat != 0:
        return TaxType.STANDARD_5, Confidence.HIGH  # the VAT rule checks it is really 5%
    if foreign:
        return TaxType.REVERSE_CHARGE, Confidence.MEDIUM  # foreign supplier, no UAE VAT
    if not registered:
        return TaxType.NONE, Confidence.MEDIUM  # unregistered supplier, no VAT
    if vat == 0 or invoice.amount_type is AmountType.NO_TAX:
        return TaxType.ZERO, Confidence.MEDIUM  # registered, 0%: zero-rated or exempt?
    return None, Confidence.NONE  # registered, VAT just not read: the accountant chooses


def code_document(
    session: Session,
    invoice: Invoice,
    chart: ChartOfAccounts,
    client: ExtractionClient,
    *,
    client_id: str,
    vendor_key: str | None,
    min_consistent_approvals: int,
) -> CodingResult:
    if invoice.document_type is DocumentType.NON_POSTABLE or not invoice.lines:
        return CodingResult([], [])
    issues: list[Issue] = []

    tax_type, tax_confidence = decide_tax_type(invoice)
    tax_code = chart.tax_codes.get(tax_type) if tax_type else None
    if tax_type is not None and tax_code is None:
        issues.append(
            Issue(
                "tax_code_unmapped",
                Severity.WARNING,
                "lines",
                f"No active tax rate in the master data is mapped to {tax_type.value}: map one "
                "in the tax rates import.",
            )
        )

    accounts: list[str | None]
    learned = _learned_account(session, chart, client_id, vendor_key, min_consistent_approvals)
    if learned is not None:
        accounts = [learned] * len(invoice.lines)
        source, confidence = Source.SUPPLIER_HISTORY, Confidence.HIGH
    else:
        accounts, problem = _model_accounts(client, invoice, chart)
        source, confidence = Source.MODEL, Confidence.MEDIUM
        if problem:
            issues.append(problem)

    lines = [
        LineCoding(
            line=index,
            account_code=code,
            account_source=source if code else Source.NONE,
            account_confidence=confidence if code else Confidence.NONE,
            tax_type=tax_type,
            tax_code=tax_code,
            tax_confidence=tax_confidence if tax_type else Confidence.NONE,
        )
        for index, code in enumerate(accounts)
    ]
    return CodingResult(lines, issues)


def check_codes(invoice: Invoice, chart: ChartOfAccounts) -> list[Issue]:
    """Codes rule (architecture §8): every line's account and tax code exist and are
    active; purchase documents use expense/asset accounts only. Errors block approval."""
    if invoice.document_type is DocumentType.NON_POSTABLE:
        return []
    issues = []
    for index, line in enumerate(invoice.lines):
        field = f"lines[{index}]"
        account = chart.accounts.get(line.account_code) if line.account_code else None
        if line.account_code is None:
            issues.append(
                Issue(
                    "account_missing",
                    Severity.ERROR,
                    f"{field}.account_code",
                    "Choose an account for this line.",
                )
            )
        elif account is None:
            issues.append(
                Issue(
                    "account_unknown",
                    Severity.ERROR,
                    f"{field}.account_code",
                    f"Account {line.account_code} is not an active account in the master data.",
                )
            )
        elif account.account_class not in PURCHASE_ACCOUNT_CLASSES:
            issues.append(
                Issue(
                    "account_wrong_class",
                    Severity.ERROR,
                    f"{field}.account_code",
                    f"Account {account.code} {account.name} is a {account.account_class.value} "
                    "account; supplier documents use expense or asset accounts.",
                )
            )
        if line.tax_type is None:
            issues.append(
                Issue(
                    "tax_missing",
                    Severity.ERROR,
                    f"{field}.tax_type",
                    "Choose a tax code for this line.",
                )
            )
        elif line.tax_type not in chart.tax_codes:
            issues.append(
                Issue(
                    "tax_unmapped",
                    Severity.ERROR,
                    f"{field}.tax_type",
                    f"No active tax rate is mapped to {line.tax_type.value}.",
                )
            )
    return issues


def _learned_account(
    session: Session,
    chart: ChartOfAccounts,
    client_id: str,
    vendor_key: str | None,
    min_consistent_approvals: int,
) -> str | None:
    if vendor_key is None:
        return None
    mapping = session.scalars(
        select(VendorMapping).where(
            VendorMapping.client_id == client_id, VendorMapping.vendor_key == vendor_key
        )
    ).first()
    if mapping is None or mapping.consistent_approvals < min_consistent_approvals:
        return None
    account = chart.accounts.get(mapping.account_code or "")
    if account is None or account.account_class not in PURCHASE_ACCOUNT_CLASSES:
        return None  # learned account archived or no longer suitable: ask the model
    return account.code


def _model_accounts(
    client: ExtractionClient, invoice: Invoice, chart: ChartOfAccounts
) -> tuple[list[str | None], Issue | None]:
    """One text-only model call; answers are constrained to real purchase account codes."""
    choices = chart.purchase_accounts()
    count = len(invoice.lines)
    if not choices:
        return [None] * count, Issue(
            "coding_no_accounts",
            Severity.WARNING,
            "lines",
            "No active expense or asset accounts in the master data: import the chart of accounts.",
        )
    codes = [account.code for account in choices]
    schema = {
        "type": "object",
        "properties": {
            "accounts": {
                "type": "array",
                "items": {"type": ["string", "null"], "enum": [*codes, None]},
            },
        },
        "required": ["accounts"],
        "additionalProperties": False,
    }
    listing = "\n".join(f"{a.code} {a.name}" for a in choices)
    items = "\n".join(
        f"{n}. {line.description or '(no description)'}" for n, line in enumerate(invoice.lines, 1)
    )
    prompt = (
        f"Supplier: {invoice.vendor_name or 'unknown'}\n\nAccounts:\n{listing}\n\n"
        f"Lines:\n{items}\n\nAnswer with one account code per line, in line order "
        f"({count} codes)."
    )
    try:
        result = client.extract(
            ExtractionRequest(prompt=prompt, json_schema=schema, system_prompt=_SYSTEM_PROMPT)
        )
        answer = json.loads(result.raw_output).get("accounts") or []
    except (ExtractionError, ValueError, AttributeError) as exc:
        logger.warning("coding: model suggestion unavailable (%s)", type(exc).__name__)
        return [None] * count, Issue(
            "coding_unavailable",
            Severity.WARNING,
            "lines",
            "No account suggestion this time (model unavailable): choose accounts in review.",
        )

    # Constrained decoding should guarantee this; never trust it blindly (rule 7 spirit).
    suggested = [code if code in chart.accounts else None for code in answer[:count]]
    return suggested + [None] * (count - len(suggested)), None
