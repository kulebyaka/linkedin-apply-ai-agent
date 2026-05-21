"""CLI utility to set a user's role.

Usage:
    uv run python -m scripts.promote_user --email you@example.com --role admin
    uv run python -m scripts.promote_user --email you@example.com           # defaults to admin
    uv run python -m scripts.promote_user --list-admins

Exit codes:
    0 — success
    1 — user not found or invalid arguments
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.config.settings import get_settings
from src.models.user import UserRole
from src.services.auth.user_repository import UserRepository


async def _promote(email: str, role: UserRole, db_path: str) -> int:
    repo = UserRepository()
    await repo.initialize(db_path=db_path)

    user = await repo.get_by_email(email)
    if user is None:
        print(f"Error: user with email {email!r} not found", file=sys.stderr)
        return 1

    await repo.set_role(user.id, role)
    print(f"Promoted {email} to {role.value}")
    return 0


async def _list_admins(db_path: str) -> int:
    repo = UserRepository()
    await repo.initialize(db_path=db_path)

    users = await repo.list_all_users(limit=1000)
    admins = [u for u in users if u.role == UserRole.ADMIN]
    if not admins:
        print("No admin users found.")
        return 0

    print(f"Admins ({len(admins)}):")
    for u in admins:
        print(f"  - {u.email} (id={u.id})")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set a user's role (admin/premium/trial) by email.",
    )
    parser.add_argument(
        "--email",
        help="Email address of the user to promote.",
    )
    parser.add_argument(
        "--role",
        choices=[r.value for r in UserRole],
        default=UserRole.ADMIN.value,
        help="Role to assign (default: admin).",
    )
    parser.add_argument(
        "--list-admins",
        action="store_true",
        help="List all admin users and exit.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite DB path (default: settings.db_path).",
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    """Async entry point — usable from tests that already have a running loop."""
    args = _parse_args(argv)
    db_path = args.db_path or get_settings().db_path

    if args.list_admins:
        return await _list_admins(db_path)

    if not args.email:
        print(
            "Error: --email is required (or use --list-admins).",
            file=sys.stderr,
        )
        return 1

    role = UserRole(args.role)
    return await _promote(args.email, role, db_path)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
