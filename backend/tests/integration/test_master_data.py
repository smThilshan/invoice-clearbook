"""Neutral master data tables (decision 20): uniqueness per provider and fuzzy contact search."""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import AccountClass, AccountingProvider
from app.models import Contact, LedgerAccount
from tests.integration.factories import CLIENT

pytestmark = pytest.mark.integration

MANUAL = AccountingProvider.MANUAL
XERO = AccountingProvider.XERO


def _account(session: Session, **overrides: Any) -> LedgerAccount:
    fields: dict[str, Any] = {
        "client_id": CLIENT,
        "provider": MANUAL,
        "external_id": "429",
        "code": "429",
        "name": "General Expenses",
        "account_class": AccountClass.EXPENSE,
    }
    account = LedgerAccount(**{**fields, **overrides})
    session.add(account)
    session.flush()
    return account


def _contact(session: Session, name: str, external_id: str) -> Contact:
    contact = Contact(
        client_id=CLIENT, provider=MANUAL, external_id=external_id, name=name, is_supplier=True
    )
    session.add(contact)
    session.flush()
    return contact


def test_external_id_unique_per_client_and_provider(session: Session) -> None:
    _account(session)
    _account(session, provider=XERO, code="429")
    _account(session, client_id="other-client")

    with pytest.raises(IntegrityError, match="uq_ledger_accounts_client_id_provider_external_id"):
        _account(session, code="430")


def test_account_code_unique_but_may_be_missing(session: Session) -> None:
    _account(session, external_id="a1", code=None)
    _account(session, external_id="a2", code=None)
    _account(session, external_id="a3", code="429")

    with pytest.raises(IntegrityError, match="uq_ledger_accounts_code"):
        _account(session, external_id="a4", code="429")


def test_contact_fuzzy_name_match_uses_trigrams(session: Session) -> None:
    _contact(session, "Al Noor Trading LLC", "c1")
    _contact(session, "Noor Al Huda Stores", "c2")
    _contact(session, "Emirates Office Supplies", "c3")

    best = session.scalars(
        select(Contact.name)
        .where(Contact.client_id == CLIENT)
        .order_by(func.similarity(Contact.name, "AL NOOR TRADING L.L.C").desc())
        .limit(1)
    ).one()

    assert best == "Al Noor Trading LLC"
