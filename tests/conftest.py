import logging

import pytest
import structlog


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
