"""Ensure ASGI send failures close suspended SSE formatting generators."""
from __future__ import annotations

from contextlib import suppress

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if callable(close):
                # AnyIO level cancellation must not skip the generator's
                # finally blocks. Native transports additionally own their
                # cleanup task when direct asyncio cancellation is repeated.
                with anyio.CancelScope(shield=True), suppress(Exception):
                    await close()
