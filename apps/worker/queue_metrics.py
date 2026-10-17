"""Small read-only RQ backlog exporter; no job deserialization or registry cleanup."""

from __future__ import annotations

import argparse
import logging
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from redis import Redis
from rq import Queue
from rq.intermediate_queue import IntermediateQueue
from rq.registry import (
    DeferredJobRegistry,
    FailedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)

from packages.common.config import load_settings

logger = logging.getLogger(__name__)


def collect_metrics(connection: Any, queue_names: tuple[str, ...]) -> str:
    """One bounded pipeline, raw cardinalities only, and no identifying labels."""
    states = ("queued", "intermediate", "started", "scheduled", "deferred", "failed")
    with connection.pipeline(transaction=False) as pipeline:
        for name in queue_names:
            queue_key = f"{Queue.redis_queue_namespace_prefix}{name}"
            pipeline.sismember(Queue.redis_queues_keys, queue_key)
            pipeline.llen(queue_key)
            pipeline.llen(IntermediateQueue.get_intermediate_queue_key(queue_key))
            for registry in (
                StartedJobRegistry,
                ScheduledJobRegistry,
                DeferredJobRegistry,
                FailedJobRegistry,
            ):
                pipeline.zcard(registry.key_template.format(name))
        counts = pipeline.execute()
    if len(counts) != len(queue_names) * 7:
        raise ValueError("Unexpected aggregate response")
    lines = [
        "# HELP aegisrag_queue_exporter_up Whether the bounded Redis inspection succeeded.",
        "# TYPE aegisrag_queue_exporter_up gauge",
        "aegisrag_queue_exporter_up 1",
        "# HELP aegisrag_queue_registered Whether this configured RQ queue is registered.",
        "# TYPE aegisrag_queue_registered gauge",
        "# HELP aegisrag_queue_entries Raw RQ queue/registry cardinality, including stale entries.",
        "# TYPE aegisrag_queue_entries gauge",
    ]
    for slot in range(len(queue_names)):
        values = counts[slot * 7 : (slot + 1) * 7]
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("Invalid aggregate response")
        lines.append(f'aegisrag_queue_registered{{queue_slot="{slot}"}} {int(values[0] > 0)}')
        lines.extend(
            f'aegisrag_queue_entries{{queue_slot="{slot}",state="{state}"}} {value}'
            for state, value in zip(states, values[1:], strict=True)
        )
    return "\n".join(lines) + "\n"


def handler(connection: Any, queue_names: tuple[str, ...]) -> type[BaseHTTPRequestHandler]:
    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                body = b"ok\n"
            elif self.path == "/metrics":
                try:
                    body = collect_metrics(connection, queue_names).encode()
                except Exception:
                    # Export only a fixed health gauge, never partial/stale cardinalities or URLs.
                    logger.warning("queue_metrics_inspection_failed")
                    body = (
                        b"# TYPE aegisrag_queue_exporter_up gauge\naegisrag_queue_exporter_up 0\n"
                    )
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return MetricsHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9181)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if (
        not 1 <= args.port <= 65535
        or not math.isfinite(args.timeout_seconds)
        or not 0.1 <= args.timeout_seconds <= 10
    ):
        parser.error("Invalid port or timeout (0.1 to 10 seconds)")
    settings = load_settings()
    if not settings.redis_url:
        parser.error("REDIS_URL is required")
    connection = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=args.timeout_seconds,
        socket_timeout=args.timeout_seconds,
        retry_on_timeout=False,
        max_connections=4,
    )
    names = tuple(dict.fromkeys((settings.ingestion_queue_name, settings.embedding_queue_name)))
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer((args.host, args.port), handler(connection, names))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        connection.close()


if __name__ == "__main__":
    main()
