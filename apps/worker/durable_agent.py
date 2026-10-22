"""Explicitly configured durable worker; importing this module starts nothing.

The operator supplies a trusted application factory, not a user API payload.
That factory owns database lifetime, runtime providers, owner scopes and a
current-authorization resolver. No credential refresh mechanism is implied.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import math
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from packages.agent.durable.scheduler import DurableBackgroundScheduler


class SchedulerFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[DurableBackgroundScheduler]: ...


async def run_scheduler(
    scheduler: DurableBackgroundScheduler, *, once: bool, poll_seconds: float
) -> None:
    if not math.isfinite(poll_seconds) or not 0.1 <= poll_seconds <= 3600:
        raise ValueError("poll interval must be between 0.1 and 3600 seconds")
    while True:
        await scheduler.run_once()
        if once:
            return
        await asyncio.sleep(poll_seconds)


async def _run(factory_path: str, *, once: bool, poll_seconds: float) -> None:
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute.isidentifier():
        raise ValueError("factory must be an operator-configured module:attribute")
    factory = cast(SchedulerFactory, getattr(importlib.import_module(module_name), attribute))
    async with factory() as scheduler:
        if not isinstance(scheduler, DurableBackgroundScheduler):
            raise TypeError("factory must yield a DurableBackgroundScheduler")
        await run_scheduler(scheduler, once=once, poll_seconds=poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the opt-in durable Agent scheduler")
    parser.add_argument(
        "--factory",
        required=True,
        help="Trusted application async-context-manager factory: module:attribute",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    asyncio.run(_run(args.factory, once=args.once, poll_seconds=args.poll_seconds))


if __name__ == "__main__":
    main()
