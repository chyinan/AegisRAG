"""The offline journey never needs a collector or background telemetry export."""

import os

# Match framework integration tests: configure the SDK before app import.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
