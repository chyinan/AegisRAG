#!/usr/bin/env python3
"""Retired: a second /retrieve and direct SQL cannot establish generation context.

Use the trusted frozen-domain runner or the exact-capture Ragas adapter described
in docs/evaluation/FROZEN_DOMAIN.md. Importing this module reads no credentials,
loads no provider clients, and performs no I/O.
"""
from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "eval_ragas_v2 is retired: reconstructed contexts are not generation evidence. "
        "Use python -m packages.eval.domain_cli for offline contracts, or an explicit "
        "trusted application factory with query_with_capture. "
        "See docs/evaluation/FROZEN_DOMAIN.md.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
