"""Missan users who log in to the Workbench (decision 71), from the terminal.

make add-user EMAIL=thilshan@example.com NAME="Thilshan" ROLE=admin
make list-users
make set-password EMAIL=thilshan@example.com
make deactivate-user EMAIL=thilshan@example.com
make remove-user EMAIL=thilshan@example.com

Passwords are typed without being shown and never passed as arguments, so they stay out of the
shell history and the process list.
"""

import argparse
import getpass
import sys

from app.core.db import session_scope
from app.domain.enums import Role
from app.services.users import (
    MIN_PASSWORD_LENGTH,
    UserError,
    create_user,
    deactivate,
    list_users,
    remove_user,
    set_password,
)

# Decision 93: give the first password by phone or in person, never in chat or email.
FIRST_LOGIN = "they choose their own password at first login"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="create a user")
    add.add_argument("email")
    add.add_argument("name")
    add.add_argument("role", type=str.upper, choices=[r.value for r in Role])
    commands.add_parser("list", help="list users")
    for name, text in (
        ("password", "set a new password"),
        ("deactivate", "stop a user's access"),
        ("remove", "delete a user's account"),
    ):
        commands.add_parser(name, help=text).add_argument("email")
    args = parser.parse_args()

    if args.command == "list":
        with session_scope() as session:
            for user in list_users(session):
                state = "active" if user.is_active else "deactivated"
                state += ", must change password" if user.must_change_password else ""
                print(f"{user.email:<36} {user.name:<24} {user.role.value:<11} {state}")
        return 0
    try:
        with session_scope() as session:
            if args.command == "add":
                user = create_user(session, email=args.email, name=args.name,
                                   role=Role(args.role), password=_new_password())  # fmt: skip
                print(f"added {user.email} ({user.role.value.lower()}): {FIRST_LOGIN}")
            elif args.command == "password":
                set_password(session, args.email, _new_password())
                print(f"password set for {args.email}, its sessions logged out: {FIRST_LOGIN}")
            elif args.command == "remove":
                remove_user(session, args.email)
                print(f"removed {args.email}: logged out everywhere; its history is kept")
            else:
                deactivate(session, args.email)
                print(f"deactivated {args.email}: logged out everywhere")
    except UserError as exc:
        print(f"not done: {exc}", file=sys.stderr)
        return 1
    return 0


def _new_password() -> str:
    password = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if getpass.getpass("Same password again: ") != password:
        raise UserError("the two passwords differ")
    return password


if __name__ == "__main__":
    sys.exit(main())
