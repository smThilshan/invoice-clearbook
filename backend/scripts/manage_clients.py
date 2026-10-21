"""Register and list clients (restaurants), until the admin screen exists (decision 25).

make add-client ID=cafe-al-noor NAME="Cafe Al Noor" INBOX="/Volumes/Shared/Cafe Al Noor"
make list-clients
"""

import argparse
import sys
from pathlib import Path

from app.core.db import session_scope
from app.domain.enums import AccountingProvider
from app.services.clients import ClientError, add_client, list_clients


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="register a restaurant and its shared-drive folder")
    add.add_argument("client_id")
    add.add_argument("name")
    add.add_argument("inbox", type=Path)
    add.add_argument("--base", type=Path, default=Path.cwd(), help="resolve a relative inbox here")
    add.add_argument(
        "--provider",
        choices=[p.value for p in AccountingProvider],
        default=AccountingProvider.MANUAL.value,
    )
    commands.add_parser("list", help="list active restaurants")
    args = parser.parse_args()

    if args.command == "list":
        with session_scope() as session:
            for client in list_clients(session):
                print(f"{client.client_id:<28} {client.name:<32} {client.inbox_path}")
        return 0

    try:
        with session_scope() as session:
            client = add_client(
                session,
                client_id=args.client_id,
                name=args.name,
                inbox_path=(args.base / args.inbox.expanduser()).resolve(),
                accounting_provider=AccountingProvider(args.provider),
            )
            print(f"added {client.client_id} ({client.name}) -> {client.inbox_path}")
    except ClientError as exc:
        print(f"not added: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
