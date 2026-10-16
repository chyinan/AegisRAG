"""Logging configuration from one test cannot suppress another test's logs."""

import logging
import os
import subprocess
import sys
from logging.config import fileConfig
from pathlib import Path
from uuid import uuid4

import pytest
import structlog

from tests.conftest import _isolated_logging_state


def test_alembic_logging_is_restored_without_losing_pytest_capture(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("packages.agent.service")
    root = logging.getLogger()
    root_handlers = tuple(root.handlers)
    root_level = root.level
    logger_level = logger.level
    assert not logger.disabled
    assert caplog.handler in root_handlers

    with _isolated_logging_state():
        fileConfig(Path(__file__).resolve().parents[2] / "alembic.ini")
        assert logger.disabled
        assert caplog.handler not in root.handlers

    assert not logger.disabled
    assert logger.level == logger_level
    assert root.level == root_level
    assert tuple(root.handlers) == root_handlers
    logger.warning("test.logging.capture_restored")
    assert "test.logging.capture_restored" in caplog.text


def test_logger_flags_filters_and_global_disable_are_restored() -> None:
    logger = logging.getLogger("tests.logging_isolation.existing")
    original = (logger.level, logger.disabled, logger.propagate)
    handlers, filters = tuple(logger.handlers), tuple(logger.filters)
    disabled_level = logging.root.manager.disable

    with _isolated_logging_state():
        added = logging.getLogger(f"tests.logging_isolation.new_{uuid4().hex}")
        for changed in (logger, added):
            changed.setLevel(logging.CRITICAL)
            changed.disabled = True
            changed.propagate = False
            changed.addHandler(logging.NullHandler())
            changed.addFilter(logging.Filter("blocked"))
        logging.disable(logging.CRITICAL)

    assert (logger.level, logger.disabled, logger.propagate) == original
    assert tuple(logger.handlers) == handlers
    assert tuple(logger.filters) == filters
    assert logging.root.manager.disable == disabled_level
    assert (added.level, added.disabled, added.propagate) == (logging.NOTSET, False, True)
    assert added.handlers == []
    assert added.filters == []


def test_structlog_configuration_is_restored_after_nested_isolation() -> None:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    configuration = structlog.get_config()
    with _isolated_logging_state():
        assert not structlog.is_configured()
        structlog.configure(processors=[])
    assert structlog.is_configured()
    assert structlog.get_config() == configuration


def test_existing_file_handler_keeps_pre_and_post_configuration_records(tmp_path: Path) -> None:
    path = tmp_path / "existing.log"
    logger = logging.getLogger("tests.logging_isolation.file")
    handler = logging.FileHandler(path, mode="w")
    handler.name = "existing-isolation-file"
    logger.addHandler(handler)
    stream = handler.stream
    logger.warning("before configuration")
    handler.flush()

    with _isolated_logging_state():
        fileConfig(Path(__file__).resolve().parents[2] / "alembic.ini")
        assert handler.stream is stream
        assert not stream.closed
        added = logging.FileHandler(tmp_path / "test-created.log", mode="w")
        added.name = "created-isolation-file"
        logging.getLogger().addHandler(added)

    assert handler.stream is stream
    assert not stream.closed
    assert logging._handlers[handler.name] is handler
    assert any(ref() is handler for ref in logging._handlerList)
    assert added.stream is None
    assert added.name not in logging._handlers
    assert not any(ref() is added for ref in logging._handlerList)
    logger.warning("after configuration")
    handler.flush()
    assert path.read_text().splitlines() == ["before configuration", "after configuration"]


def test_isolation_spans_module_fixture_setup_and_teardown(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import _reset_structlog, pytest_runtest_protocol\n",
    )
    (tmp_path / "test_first.py").write_text(
        "import logging\n"
        "from logging.config import fileConfig\n"
        "import pytest\n"
        "logger = logging.getLogger('tests.cross_module.application')\n"
        "@pytest.fixture(scope='module', autouse=True)\n"
        "def module_logging():\n"
        f"    fileConfig({str(root / 'alembic.ini')!r})\n"
        "    yield\n"
        f"    fileConfig({str(root / 'alembic.ini')!r})\n"
        "def test_configuration_takes_effect():\n"
        "    assert logger.disabled\n",
    )
    (tmp_path / "test_second.py").write_text(
        "import logging\n"
        "logger = logging.getLogger('tests.cross_module.application')\n"
        "def test_later_module_can_capture_logs(caplog):\n"
        "    assert not logger.disabled\n"
        "    logger.warning('cross module capture restored')\n"
        "    assert 'cross module capture restored' in caplog.text\n",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(root), environment.get("PYTHONPATH"))),
    )
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-c", str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path),
         "--confcutdir", str(tmp_path), "test_first.py", "test_second.py"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
