import logging
import logging.config
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

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


@dataclass(frozen=True)
class _LoggerState:
    level: int
    disabled: bool
    propagate: bool
    handlers: tuple[logging.Handler, ...]
    filters: tuple[logging.Filter, ...]


def _registered_loggers() -> tuple[logging.Logger, ...]:
    return (
        logging.getLogger(),
        *(
            logger
            for logger in logging.root.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ),
    )


@contextmanager
def _preserve_existing_handlers() -> Iterator[None]:
    """Keep fileConfig/dictConfig from closing resources owned by pytest.

    Their shared clear helper otherwise closes even detached existing handlers
    and clears their registrations. Reattaching a closed mode='w' FileHandler
    cannot recover it safely. Protect those resources instead of reopening them.
    """
    handler_refs = logging._handlerList[:]
    registered_handlers = dict(logging._handlers)
    protected = {handler for ref in handler_refs if (handler := ref()) is not None}

    def clear_test_handlers() -> None:
        with logging._lock:
            current_refs = logging._handlerList[:]
            logging.shutdown([ref for ref in current_refs if ref() not in protected])
            logging._handlerList[:] = [ref for ref in current_refs if ref() in protected]
            for name, handler in list(logging._handlers.items()):
                if handler not in protected:
                    del logging._handlers[name]

    with patch.object(logging.config, "_clearExistingHandlers", clear_test_handlers):
        try:
            yield
        finally:
            # Close resources created by this test before removing the last
            # strong references held by its logger configuration.
            clear_test_handlers()
            with logging._lock:
                logging._handlers.clear()
                logging._handlers.update(registered_handlers)
                logging._handlerList[:] = handler_refs


@contextmanager
def _isolated_logging_state() -> Iterator[None]:
    """Restore process-global logging changes, including Alembic fileConfig.

    Keep pytest's capture handlers installed. Clearing only root handlers does
    not undo fileConfig's disabling of already-imported application loggers.
    """
    states = {
        logger: _LoggerState(
            logger.level, logger.disabled, logger.propagate,
            tuple(logger.handlers), tuple(logger.filters),
        )
        for logger in _registered_loggers()
    }
    disabled_level = logging.root.manager.disable
    structured_config = structlog.get_config()
    structured_was_configured = structlog.is_configured()
    structlog.reset_defaults()
    try:
        with _preserve_existing_handlers():
            yield
    finally:
        for logger in _registered_loggers():
            # A logger first created by a test must not carry that test's
            # disabled flag, level, handlers or filters into later tests.
            state = states.get(logger, _LoggerState(logging.NOTSET, False, True, (), ()))
            logger.setLevel(state.level)
            logger.disabled = state.disabled
            logger.propagate = state.propagate
            logger.handlers[:] = state.handlers
            logger.filters[:] = state.filters
        logging.disable(disabled_level)
        structlog.reset_defaults()
        if structured_was_configured:
            structlog.configure(**structured_config)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    # A function fixture starts too late: higher-scoped migration fixtures can
    # already have disabled the application loggers during test setup.
    with _isolated_logging_state():
        yield


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()
