"""The account an expense was paid through (decision 89), for clients whose expenses are sent as
expenses: Zoho takes an expense without one and puts it on "Undeposited Funds", so it is always
chosen here, by code, never by the model (rule 7). The first that applies wins:

1. The accountant's own choice for the document (`verified_fields`): never chosen again.
2. The supplier's memory: the account its last approved expense was paid through.
3. The client's default ("usually paid through", set on the Clients page).

Only one of the client's synced payment accounts counts (rule 14); a remembered or default
account no longer there is passed over. None left: an error, so the accountant picks one before
approving. A choice that is no longer a payment account (closed, or the master data changed):
an error too. A chosen account is proven, so it is quiet.
"""

from dataclasses import dataclass

from app.domain.invoice import Invoice
from app.pipeline import coding
from app.pipeline.payment import EXPENSE
from app.pipeline.verify.issues import Issue, Severity

FIELD = "paid_through"


@dataclass(frozen=True, slots=True)
class PaidThrough:
    """What chooses it, besides the accountant."""

    remembered: str | None  # the supplier's last approved expense
    default: str | None  # the client's usual account


def choose_paid_through(
    invoice: Invoice,
    chart: coding.ChartOfAccounts,
    sources: PaidThrough,
    *,
    verified: frozenset[str],
) -> tuple[Invoice, list[Issue]]:
    """The invoice with its paid-through account, and the error when there is none. Bills and
    other documents are left as they are."""
    if invoice.document_type is not EXPENSE:
        return invoice, []
    payable = {account.code for account in chart.payment_accounts()}
    if FIELD in verified and invoice.paid_through is not None:
        if invoice.paid_through in payable:
            return invoice, []
        return invoice, [
            Issue(
                "paid_through_invalid",
                Severity.ERROR,
                FIELD,
                "Paid through: this account cannot pay an expense any more. Sync the master "
                "data and choose the account it was paid from.",
            )
        ]
    chosen = next((code for code in (sources.remembered, sources.default) if code in payable), None)
    invoice = invoice.model_copy(update={FIELD: chosen})
    if chosen is not None:
        return invoice, []
    return invoice, [
        Issue(
            "paid_through_missing",
            Severity.ERROR,
            FIELD,
            "Paid through: choose the account this expense was paid from (petty cash, a card, "
            "an employee ...).",
        )
    ]
