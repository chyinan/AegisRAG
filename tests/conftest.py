import logging
import os

import pytest
import structlog

# Ragas buffers analytics and may flush from a background thread or at exit,
# after a test's monkeypatch fixture has restored the environment. Keep opt-out
# enabled for the whole pytest process, before test modules import Ragas.
# Individual privacy-contract tests can still temporarily remove it to verify
# fail-closed behavior; teardown restores this process-wide safe default.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"

# The application requires an explicit non-production environment before it
# permits the in-memory limiter used by unit tests. Production never inherits
# this test-only default.
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    """Reset structlog and logging to defaults before every test.

    Some tests call configure_logging() which mutates global structlog state
    and logging handler configuration. Without this reset, downstream tests
    that rely on caplog / capsys for log assertions can fail
    non-deterministically depending on execution order.
    """
    structlog.reset_defaults()
    # Clear any handlers added by configure_logging() to root logger
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
