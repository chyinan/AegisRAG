"""Read-only JSON preflight: python -m scripts.check_upgrade_readiness --help."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from collections.abc import Sequence
from typing import NoReturn

from packages.common.config import load_settings
from packages.operations.upgrade_check import Check, Report, Status, manual_checks, run_checks


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ValueError("Invalid arguments.")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(
        description="Read-only 0016/0017 -> 0018 upgrade inspection. Reads DATABASE_URL (runtime), "
        "REDIS_URL and optionally AEGISRAG_UPGRADE_AUDIT_DATABASE_URL (an existing authorized "
        "all-tenant inspection login). Never put credentials in command arguments. "
        "Manual restore/quiescence evidence stays UNVERIFIED; this is not upgrade authorization.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="Per-I/O timeout from 0.1 to 60 seconds (default: 5).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Never emit connector logs/tracebacks or pydantic input values. Only fixed
    # descriptions, reviewed revision IDs and aggregate counts go to stdout.
    previous_levels = {
        logging.getLogger(name): logging.getLogger(name).level
        for name in ("sqlalchemy.engine", "sqlalchemy.pool", "alembic", "redis", "asyncio")
    }
    for logger in previous_levels:
        logger.setLevel(logging.CRITICAL + 1)
    try:
        args = _parser().parse_args(argv)
        if not math.isfinite(args.timeout_seconds) or not 0.1 <= args.timeout_seconds <= 60:
            raise ValueError("Invalid timeout.")
        report = asyncio.run(
            run_checks(
                load_settings(),
                audit_url=os.getenv("AEGISRAG_UPGRADE_AUDIT_DATABASE_URL") or None,
                timeout=args.timeout_seconds,
            )
        )
    except (KeyboardInterrupt, EOFError):
        report = Report(
            tuple(
                manual_checks()
                + [
                    Check(
                        "inspection",
                        Status.UNVERIFIED,
                        "Inspection cancelled; readiness is unverified.",
                    )
                ]
            )
        )
    except Exception:
        report = Report(
            tuple(
                manual_checks()
                + [
                    Check(
                        "configuration",
                        Status.BLOCKED,
                        "Invalid configuration/arguments or inspection failure; use --help. "
                        "Credentials and exception details are intentionally omitted.",
                    )
                ]
            )
        )
    finally:
        for logger, level in previous_levels.items():
            logger.setLevel(level)
    sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
