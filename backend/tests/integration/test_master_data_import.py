import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.manual import ManualAccountingSystem
from app.models import Contact, LedgerAccount, TaxRate
from app.services.master_data import Counts, ImportReport, import_master_data
from tests.integration.factories import CLIENT
from tests.test_manual_master_data import SAMPLES

pytestmark = pytest.mark.integration

TAXES = "code,name,rate,tax_type\nSTD5,Standard 5%,5,STANDARD_5\n"


def run(session: Session, client_id: str = CLIENT, **csvs: str) -> ImportReport:
    """Import the given CSV texts, or the sample files when none are given."""
    if csvs:
        source = ManualAccountingSystem(**csvs)
    else:
        source = ManualAccountingSystem.from_directory(SAMPLES)
    return import_master_data(session, client_id=client_id, source=source)


def count(session: Session, model: type, client_id: str = CLIENT) -> int:
    query = select(func.count()).select_from(model).where(model.client_id == client_id)
    return session.scalars(query).one()


def test_sample_import_then_rerun_is_unchanged(session: Session) -> None:
    first = run(session)
    second = run(session)

    assert first.accounts == Counts(created=47)
    assert first.tax_rates == Counts(created=5)
    assert first.contacts == Counts(created=12)
    assert second.accounts == Counts(unchanged=47)
    assert second.contacts == Counts(unchanged=12)
    assert count(session, LedgerAccount) == 47


def test_changed_rows_are_updated_and_missing_rows_kept(session: Session) -> None:
    accounts = "code,name,class\n5000,Food,EXPENSE\n6100,Rent,EXPENSE\n"
    run(session, tax_rates_csv=TAXES, accounts_csv=accounts)

    report = run(session, accounts_csv="code,name,class,active\n5000,Food Cost,EXPENSE,no\n")

    assert report.accounts == Counts(updated=1)
    assert report.tax_rates is None  # not part of this import
    food = session.scalars(select(LedgerAccount).where(LedgerAccount.code == "5000")).one()
    assert (food.name, food.is_active) == ("Food Cost", False)
    assert count(session, LedgerAccount) == 2  # 6100 not in the file, still there


def test_unknown_default_tax_code_rejects_everything(session: Session) -> None:
    with pytest.raises(MasterDataError, match="problem") as excinfo:
        run(
            session,
            tax_rates_csv=TAXES,
            accounts_csv="code,name,class,default_tax_code\n5000,Food,EXPENSE,NOPE\n",
            contacts_csv="name\nMetro Mart\n",
        )

    assert excinfo.value.problems == [
        "account 5000: default tax code 'NOPE' is not a known tax rate"
    ]
    assert count(session, TaxRate) == count(session, Contact) == 0


def test_default_tax_code_may_refer_to_previously_imported_rate(session: Session) -> None:
    run(session, tax_rates_csv=TAXES)

    accounts = "code,name,class,default_tax_code\n5000,Food,EXPENSE,STD5\n"
    report = run(session, accounts_csv=accounts)

    assert report.accounts == Counts(created=1)


def test_clients_are_isolated(session: Session) -> None:
    run(session, client_id="cafe-one")
    report = run(session, client_id="cafe-two")

    assert report.accounts == Counts(created=47)
    assert count(session, LedgerAccount, "cafe-one") == 47
    assert count(session, LedgerAccount, "cafe-two") == 47
