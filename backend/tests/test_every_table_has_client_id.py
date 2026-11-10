"""Rule 10: every table has client_id. The exceptions belong to Missan, not to a client: users
(decision 71), staff who work on every client, and sharepoint_connections (decision 83), a
staff member's read-only SharePoint sign-in used for every client. A new table without client_id
fails here."""

from app.models import Base

NOT_CLIENT_DATA = {"users", "sharepoint_connections"}


def test_every_table_but_missans_own_belongs_to_a_client() -> None:
    tables = Base.metadata.tables.items()
    without = {name for name, table in tables if "client_id" not in table.columns}

    assert without == NOT_CLIENT_DATA


def test_deleting_a_company_empties_every_table_of_it_but_the_proof() -> None:
    """Decision 91: a new table with client_id must be added to the company delete."""
    from app.services.company_deletion import COMPANY_TABLES

    tables = Base.metadata.tables.items()
    with_client = {name for name, table in tables if "client_id" in table.columns}

    deleted = [model.__tablename__ for model in COMPANY_TABLES]
    assert set(deleted) == with_client - {"deletions"}
    assert len(deleted) == len(set(deleted))
