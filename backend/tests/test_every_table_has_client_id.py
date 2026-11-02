"""Rule 10: every table has client_id. The one exception is users (decision 71): Missan staff
work on every client and are not client data. A new table without client_id fails here."""

from app.models import Base

NOT_CLIENT_DATA = {"users"}


def test_every_table_but_users_belongs_to_a_client() -> None:
    tables = Base.metadata.tables.items()
    without = {name for name, table in tables if "client_id" not in table.columns}

    assert without == NOT_CLIENT_DATA
