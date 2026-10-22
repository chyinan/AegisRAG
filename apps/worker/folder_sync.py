"""Opt-in scheduler: submit bounded batches through the authenticated API.

Run ``python -m apps.worker.folder_sync --once`` from an external timer, or
keep the process running for continuous polling. Tokens are read from the
environment for each pass and are never persisted or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


async def run_scheduler_pass(client: httpx.AsyncClient) -> int:
    response = await client.get("/v1/sources/folder/sync/plans")
    response.raise_for_status()
    plans = response.json()["data"]
    submitted = 0
    for plan in plans:
        if not plan["enabled"] or plan["next_run_at"] > time.time():
            continue
        result = await client.post(
            f"/v1/sources/folder/sync/plans/{plan['id']}/batch", params={"due_only": "true"}
        )
        if result.status_code == 409:
            continue  # A different scheduler already owns this batch.
        result.raise_for_status()
        submitted += 1
    return submitted


async def _run(*, api_url: str, once: bool, poll_seconds: float) -> None:
    parsed = urlsplit(api_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("FOLDER_SYNC_API_URL must not contain credentials, query or fragment")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("Use HTTPS for remote API endpoints")
    if parsed.path not in {"", "/"}:
        raise ValueError("FOLDER_SYNC_API_URL must be an origin without a path")
    while True:
        token = os.getenv("FOLDER_SYNC_BEARER_TOKEN", "").strip()
        if not token:
            raise ValueError("FOLDER_SYNC_BEARER_TOKEN is required")
        try:
            async with httpx.AsyncClient(
                base_url=api_url.rstrip("/"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(330),
                follow_redirects=False,
            ) as client:
                submitted = await run_scheduler_pass(client)
            logger.info("folder_sync_scheduler_pass", extra={"submitted_batches": submitted})
        except httpx.HTTPStatusError as exc:
            # Never log response payloads or credential-bearing request headers.
            logger.error(
                "folder_sync_scheduler_http_error", extra={"status": exc.response.status_code}
            )
            if exc.response.status_code in {401, 403} or once:
                raise RuntimeError("Folder sync API rejected the scheduler request") from None
        except httpx.RequestError:
            logger.error("folder_sync_scheduler_connection_failed")
            if once:
                raise RuntimeError("Folder sync API is unavailable") from None
        if once:
            return
        await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default=os.getenv("FOLDER_SYNC_API_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--once", action="store_true", help="Submit one due batch per plan and exit"
    )
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be between 1 and 300")
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_run(api_url=args.api_url, once=args.once, poll_seconds=args.poll_seconds))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
