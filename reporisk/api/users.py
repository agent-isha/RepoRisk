"""Manage RepoRisk accounts from the command line.

There is no public sign-up: analysis reads the server's filesystem, so accounts
are provisioned by whoever runs the server.

    python -m reporisk.api.users create you@example.com      # prompts for the password
    python -m reporisk.api.users list
    python -m reporisk.api.users set-password you@example.com
    python -m reporisk.api.users deactivate you@example.com   # also ends their sessions
    python -m reporisk.api.users activate you@example.com
    python -m reporisk.api.users delete you@example.com

Passwords are read with a hidden prompt (or from stdin with --password-stdin,
for scripts), never from command-line arguments, which end up in shell history.
The database is REPORISK_DB_PATH (default instance/reporisk.sqlite3).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from typing import Sequence

from .auth import AuthError, AuthStore
from .config import ConfigError, settings_from_env


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise AuthError("passwords do not match")
    return first


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reporisk.api.users", description="Manage RepoRisk accounts.")
    parser.add_argument("--db", help="SQLite database path (default: REPORISK_DB_PATH or instance/reporisk.sqlite3)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "set-password"):
        cmd = commands.add_parser(name)
        cmd.add_argument("email")
        cmd.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    commands.add_parser("list")
    for name in ("activate", "deactivate", "delete"):
        commands.add_parser(name).add_argument("email")
    args = parser.parse_args(argv)

    try:
        db_path = args.db or settings_from_env().db_path
        store = AuthStore(db_path)
        if args.command == "create":
            user = store.create_user(args.email, _read_password(args))
            print(f"Created {user.email}")
        elif args.command == "set-password":
            store.set_password(args.email, _read_password(args))
            print(f"Password updated for {args.email}; existing sessions were signed out")
        elif args.command == "list":
            users = store.list_users()
            if not users:
                print(f"No accounts in {db_path}")
            for u in users:
                status = "active" if u.is_active else "inactive"
                print(f"{u.email:40} {status:9} created {u.created_at[:10]}  last login {(u.last_login_at or 'never')[:10]}")
        elif args.command in ("activate", "deactivate"):
            store.set_active(args.email, args.command == "activate")
            print(f"{args.email} {args.command}d")
        elif args.command == "delete":
            store.delete_user(args.email)
            print(f"Deleted {args.email}")
    except (AuthError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
