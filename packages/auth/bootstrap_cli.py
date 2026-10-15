"""Run with python -m packages.auth.bootstrap_cli; passwords never belong in argv."""

# pattern: Imperative Shell

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from packages.auth.bootstrap import BootstrapRequest, BootstrapResult, bootstrap_admin
from packages.common.config import load_settings
from packages.common.errors import DomainError
from packages.common.logging import configure_logging
from packages.data.storage.session import create_async_db_engine, create_session_factory

logger = logging.getLogger(__name__)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default error can echo unknown arguments, including a user
        # mistakenly supplying a password. Help documents accepted arguments.
        del message
        self.exit(2, "Invalid bootstrap arguments; use --help. Never pass passwords in argv.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Explicitly initialize a tenant and its first admin.",
        allow_abbrev=False,
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--admin-display-name", default="Administrator")
    parser.add_argument(
        "--password-file",
        type=Path,
        help="Read a UTF-8 password from a mounted secret file; otherwise prompt without echo.",
    )
    return parser


def _read_password(path: Path | None) -> str:
    if path is not None:
        # Bound the read, preserve spaces, allow one line terminator from a secret file.
        with path.open("rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("Password file is too large.")
        password = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if "\n" in password or "\r" in password:
            raise ValueError("Password file must contain one line.")
        return password
    if not sys.stdin.isatty():
        raise ValueError("Use an interactive terminal or --password-file.")
    with warnings.catch_warnings():
        # getpass normally falls back to echoed stdin if terminal controls fail.
        # An administrator credential must never use that fallback.
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("Initial administrator password: ")
        if password != getpass.getpass("Confirm administrator password: "):
            raise ValueError("Passwords do not match.")
    return password


async def _run(request: BootstrapRequest) -> BootstrapResult:
    settings = load_settings()
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required.")
    engine = create_async_db_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            return await bootstrap_admin(session, request)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging("INFO")
    try:
        request = BootstrapRequest(
            tenant_id=args.tenant_id,
            tenant_name=args.tenant_name,
            username=args.admin_username,
            display_name=args.admin_display_name,
            password=_read_password(args.password_file),
        )
        result = asyncio.run(_run(request))
    except DomainError as exc:
        logger.error("Bootstrap refused (%s): %s", exc.code, exc.message)
        return 1
    except (OSError, ValueError, ValidationError, SQLAlchemyError, getpass.GetPassWarning):
        # Never log exception strings/tracebacks, SQL parameters, connection URLs,
        # Pydantic input values, password files, or passwords.
        logger.error(
            "Bootstrap failed. Check arguments, password input, DATABASE_URL and migrations; "
            "no credentials are included in this output."
        )
        return 1
    except (EOFError, KeyboardInterrupt):
        logger.error("Bootstrap cancelled.")
        return 1
    logger.info(
        "Bootstrap %s; existing passwords are never reset.",
        "created the tenant administrator" if result.created else "already complete",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
