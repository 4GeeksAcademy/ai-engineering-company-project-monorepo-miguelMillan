"""Alta de permisos (solo con acceso al servidor; el API nunca deja que una cuenta se promocione a sí misma).

    uv run python accounts.py list
    uv run python accounts.py set-role    ana@example.com manager        # admin | manager | service | user
    uv run python accounts.py set-clients marca@example.com glowlab_cosmetics
"""

import argparse
import sys
from threading import RLock

from database import open_database
from models import UserRole
from security_log import security_event
from user_service import get_user_by_email, list_users, update_user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Permisos de las cuentas de TrackFlow")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    role = commands.add_parser("set-role")
    role.add_argument("email")
    role.add_argument("role", choices=[r.value for r in UserRole])
    clients = commands.add_parser("set-clients")
    clients.add_argument("email")
    clients.add_argument("client_ids", nargs="*")
    args = parser.parse_args(argv)
    with open_database() as db:
        if args.command == "list":
            for user in list_users(db):
                print(f"{user.id:>4}  {user.role.value:<8} {'activa' if user.is_active else 'inactiva':<9} {user.email} {' '.join(user.client_ids)}")
            return 0
        user = get_user_by_email(db, args.email)
        if user is None:
            print("No existe ninguna cuenta con ese email.", file=sys.stderr)
            return 1
        changes = {"role": UserRole(args.role)} if args.command == "set-role" else {"client_ids": args.client_ids}
        update_user(db, RLock(), user.id, changes)
        security_event("account_changed_by_cli", email=str(user.email), **{k: getattr(v, "value", v) for k, v in changes.items()})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
