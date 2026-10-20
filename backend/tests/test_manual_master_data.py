from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.manual import ManualAccountingSystem
from app.domain.enums import AccountClass, AccountingProvider, TaxType

SAMPLES = Path(__file__).resolve().parents[2] / "infra" / "samples" / "master_data"

ACCOUNTS = "code,name,class,default_tax_code,active\n5000,Food Cost,EXPENSE,STD5,yes\n"
TAXES = "code,name,rate,tax_type\nSTD5,Standard 5%,5,STANDARD_5\n"


def problems_of(**csvs: str) -> list[str]:
    with pytest.raises(MasterDataError) as excinfo:
        ManualAccountingSystem(**csvs).fetch_master_data()
    return excinfo.value.problems


def test_provider_is_manual() -> None:
    assert ManualAccountingSystem().provider is AccountingProvider.MANUAL


def test_sample_files_parse() -> None:
    data = ManualAccountingSystem.from_directory(SAMPLES).fetch_master_data()

    assert data.accounts is not None and data.tax_rates is not None and data.contacts is not None
    by_code = {a.code: a for a in data.accounts}
    assert by_code["1010"].name == "Petty Cash"
    assert by_code["1010"].account_class is AccountClass.ASSET
    assert by_code["5000"].default_tax_code == "STD5"
    assert by_code["6990"].is_active is False
    assert {t.tax_type for t in data.tax_rates} == set(TaxType)
    assert all(c.is_supplier for c in data.contacts)


def test_files_not_supplied_are_none() -> None:
    data = ManualAccountingSystem(accounts_csv=ACCOUNTS).fetch_master_data()

    assert data.accounts is not None
    assert data.tax_rates is None
    assert data.contacts is None


def test_header_case_bom_blank_lines_and_extra_columns() -> None:
    csv_text = "﻿Code,NAME,Class,Notes\n\n5000,Food Cost,expense,used daily\n"

    [account] = ManualAccountingSystem(accounts_csv=csv_text).fetch_master_data().accounts or []

    assert account.external_id == account.code == "5000"
    assert account.account_class is AccountClass.EXPENSE
    assert account.is_active is True
    assert account.raw["notes"] == "used daily"


def test_tax_rate_values() -> None:
    csv_text = "code,name,rate,tax_type\nRCM5,Reverse charge,5%,reverse charge\nX,Unmapped,,\n"

    data = ManualAccountingSystem(tax_rates_csv=csv_text).fetch_master_data()
    rcm, unmapped = data.tax_rates or []

    assert rcm.rate == Decimal(5)
    assert rcm.tax_type is TaxType.REVERSE_CHARGE
    assert unmapped.rate is None and unmapped.tax_type is None


def test_contact_identity_is_normalised_name() -> None:
    csv_text = "name,tax_number\n  Al Waha  Hypermarket LLC ,100999000000001\nCorner Grocery,\n"

    waha, corner = ManualAccountingSystem(contacts_csv=csv_text).fetch_master_data().contacts or []

    assert waha.external_id == "al waha hypermarket llc"
    assert waha.name == "Al Waha  Hypermarket LLC"
    assert waha.is_supplier is True
    assert corner.tax_number is None


def test_all_problems_reported_together_across_files() -> None:
    problems = problems_of(
        accounts_csv=(
            "code,name,class,active\n"
            "5000,Food Cost,COST,yes\n"  # bad class
            ",No Code,EXPENSE,yes\n"  # missing code
            "5010,Drinks,EXPENSE,maybe\n"  # bad flag
            "5020,A,EXPENSE,yes\n5020,B,EXPENSE,yes\n"  # duplicate
            "5030,Too,EXPENSE,yes,extra\n"  # too many values
        ),
        tax_rates_csv="code,name,rate\nSTD5,Std,five\nBIG,Big,150\n",
        contacts_csv="tax_number\n100999000000001\n",
    )

    expected = [
        "ledger_accounts.csv row 2: 'class' must be one of",
        "ledger_accounts.csv row 3: 'code' is required",
        "ledger_accounts.csv row 4: 'active' must be yes or no",
        "ledger_accounts.csv row 6: duplicate of row 5",
        "ledger_accounts.csv row 7: more values than columns",
        "tax_rates.csv row 2: 'rate' must be a number",
        "tax_rates.csv row 3: 'rate' must be between 0 and 100",
        "contacts.csv: missing column(s) name",
    ]
    for start in expected:
        assert any(p.startswith(start) for p in problems), (start, problems)
    assert len(problems) == len(expected)


def test_duplicate_contact_names_differing_only_in_case() -> None:
    problems = problems_of(contacts_csv="name\nMetro Mart\nMETRO  MART\n")

    assert problems == ["contacts.csv row 3: duplicate of row 2 ('metro mart')"]


def test_empty_file_is_a_problem() -> None:
    assert problems_of(tax_rates_csv="") == ["tax_rates.csv: file is empty"]
