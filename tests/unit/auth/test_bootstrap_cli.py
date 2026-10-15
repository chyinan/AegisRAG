"""Bootstrap CLI fails safely and never reports credential material."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import bcrypt
import pytest
from sqlalchemy.exc import OperationalError

from packages.auth import bootstrap_cli
from packages.auth.bootstrap import BootstrapRequest, BootstrapResult
from packages.auth.user_service import hash_password
from packages.common.errors import DomainError

PASSWORD = "Bootstrap-Secret42!"
ARGS = ["--tenant-id", "test", "--tenant-name", "Test", "--admin-username", "admin"]


@pytest.mark.parametrize("password", ["short", "x" * 73, "密" * 25])
def test_bootstrap_rejects_unsafe_password_lengths(password: str) -> None:
    with pytest.raises(DomainError, match="Bootstrap password"):
        BootstrapRequest("test", "Test", "admin", password)


def test_bootstrap_request_repr_excludes_password() -> None:
    assert PASSWORD not in repr(BootstrapRequest("test", "Test", "admin", PASSWORD))


@pytest.mark.parametrize("tenant_id", ["", "has space", "a" * 37, "../test"])
def test_bootstrap_requires_bounded_explicit_tenant(tenant_id: str) -> None:
    with pytest.raises(DomainError):
        BootstrapRequest(tenant_id, "Test", "admin", PASSWORD)


def test_shared_hashing_uses_local_auth_policy() -> None:
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$2b$")
    assert bcrypt.checkpw(PASSWORD.encode(), hashed.encode())
    assert not bcrypt.checkpw(b"Different-Secret42!", hashed.encode())
    with pytest.raises(DomainError) as exc_info:
        hash_password("onlylowercase")
    assert exc_info.value.code == "AUTH_WEAK_PASSWORD"


def test_password_file_preserves_spaces_and_removes_one_newline(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_text(f" {PASSWORD} \n")
    assert bootstrap_cli._read_password(path) == f" {PASSWORD} "
    path.write_text("one\ntwo\n")
    with pytest.raises(ValueError):
        bootstrap_cli._read_password(path)


def test_noninteractive_input_requires_secret_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap_cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="password-file"):
        bootstrap_cli._read_password(None)


def test_cli_success_output_contains_no_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "password"
    path.write_text(PASSWORD)
    runner = AsyncMock(return_value=BootstrapResult("test", "user", "group", created=True))
    monkeypatch.setattr(bootstrap_cli, "_run", runner)
    assert bootstrap_cli.main([*ARGS, "--password-file", str(path)]) == 0
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert runner.await_args.args[0].password == PASSWORD


def test_cli_database_errors_do_not_leak_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "password"
    path.write_text(PASSWORD)
    error = OperationalError("SQL secret", {"password": PASSWORD}, Exception(PASSWORD))
    monkeypatch.setattr(bootstrap_cli, "_run", AsyncMock(side_effect=error))
    assert bootstrap_cli.main([*ARGS, "--password-file", str(path)]) == 1
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert "SQL secret" not in output.out + output.err


def test_invalid_cli_arguments_do_not_echo_accidental_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        bootstrap_cli.main([*ARGS, "--password", PASSWORD])
    assert exc_info.value.code == 2
    assert PASSWORD not in capsys.readouterr().err


def test_password_prompt_refuses_echoing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import warnings

    def unsafe_getpass(_prompt: str) -> str:
        warnings.warn("Cannot control echo", bootstrap_cli.getpass.GetPassWarning, stacklevel=2)
        raise AssertionError("Echoing fallback must not run")

    monkeypatch.setattr(bootstrap_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_cli.getpass, "getpass", unsafe_getpass)
    with pytest.raises(bootstrap_cli.getpass.GetPassWarning):
        bootstrap_cli._read_password(None)


@pytest.mark.parametrize("field", ["tenant_name", "username", "display_name"])
@pytest.mark.parametrize(
    "value",
    [
        "", " ", " leading", "trailing ", "a" * 256,
        "line\nbreak", "tab\tname", "nul\x00name", "del\x7fname",
    ],
)
def test_bootstrap_rejects_invalid_names_without_echoing_input(field, value) -> None:
    values = dict(tenant_id="test", tenant_name="Test", username="admin", password=PASSWORD)
    values[field] = value
    with pytest.raises(DomainError) as exc_info:
        BootstrapRequest(**values)
    assert exc_info.value.code == "AUTH_BOOTSTRAP_INVALID"
    assert exc_info.value.message == (
        "Names must be nonblank, trimmed text of at most 255 characters."
    )
    assert PASSWORD not in str(exc_info.value)


@pytest.mark.parametrize("field", ["tenant_name", "username", "display_name"])
def test_bootstrap_accepts_name_at_column_limit(field) -> None:
    values = dict(tenant_id="test", tenant_name="Test", username="admin", password=PASSWORD)
    values[field] = "a" * 255
    assert getattr(BootstrapRequest(**values), field) == "a" * 255


def test_password_file_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "password"
    path.write_bytes(b"x" * 4096)
    assert bootstrap_cli._read_password(path) == "x" * 4096
    path.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError, match=r"^Password file is too large\.$"):
        bootstrap_cli._read_password(path)


@pytest.mark.parametrize("confirmation", [PASSWORD, "Different-Secret42!"])
def test_interactive_confirmation(monkeypatch, confirmation) -> None:
    prompt = Mock(side_effect=[PASSWORD, confirmation])
    monkeypatch.setattr(bootstrap_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_cli.getpass, "getpass", prompt)
    if confirmation == PASSWORD:
        assert bootstrap_cli._read_password(None) == PASSWORD
    else:
        with pytest.raises(ValueError, match=r"^Passwords do not match\.$"):
            bootstrap_cli._read_password(None)
    assert [call.args for call in prompt.call_args_list] == [
        ("Initial administrator password: ",),
        ("Confirm administrator password: ",),
    ]


async def test_missing_database_url_fails_before_engine_creation(monkeypatch) -> None:
    engine = Mock(side_effect=AssertionError("Must not connect without DATABASE_URL"))
    monkeypatch.setattr(bootstrap_cli, "load_settings", lambda: SimpleNamespace(database_url=None))
    monkeypatch.setattr(bootstrap_cli, "create_async_db_engine", engine)
    with pytest.raises(ValueError, match=r"^DATABASE_URL is required\.$"):
        await bootstrap_cli._run(BootstrapRequest("test", "Test", "admin", PASSWORD))
    engine.assert_not_called()


def test_cli_domain_error_reports_only_safe_code_and_message(monkeypatch, caplog, capsys) -> None:
    error = DomainError(
        code="AUTH_BOOTSTRAP_CONFLICT",
        message="Existing administrator cannot be changed.",
        status_code=409,
    )
    # An exception cause can contain credentials even when the domain error is safe.
    error.__cause__ = RuntimeError(PASSWORD)
    runner = AsyncMock(side_effect=error)
    monkeypatch.setattr(bootstrap_cli, "configure_logging", lambda _level: None)
    monkeypatch.setattr(bootstrap_cli, "_read_password", lambda _path: PASSWORD)
    monkeypatch.setattr(bootstrap_cli, "_run", runner)
    with caplog.at_level("ERROR", logger=bootstrap_cli.__name__):
        assert bootstrap_cli.main(ARGS) == 1
    runner.assert_awaited_once()
    assert caplog.messages == [
        "Bootstrap refused (AUTH_BOOTSTRAP_CONFLICT): Existing administrator cannot be changed."
    ]
    output = capsys.readouterr()
    assert PASSWORD not in caplog.text + output.out + output.err
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
@pytest.mark.parametrize("prompt_number", [1, 2])
def test_cli_interactive_cancellation_never_runs_bootstrap(
    monkeypatch, caplog, capsys, exception, prompt_number,
) -> None:
    responses = [PASSWORD] * (prompt_number - 1) + [exception(PASSWORD)]
    prompt = Mock(side_effect=responses)
    runner = AsyncMock()
    monkeypatch.setattr(bootstrap_cli, "configure_logging", lambda _level: None)
    monkeypatch.setattr(bootstrap_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_cli.getpass, "getpass", prompt)
    monkeypatch.setattr(bootstrap_cli, "_run", runner)
    with caplog.at_level("ERROR", logger=bootstrap_cli.__name__):
        assert bootstrap_cli.main(ARGS) == 1
    runner.assert_not_called()
    assert prompt.call_count == prompt_number
    assert caplog.messages == ["Bootstrap cancelled."]
    output = capsys.readouterr()
    assert PASSWORD not in caplog.text + output.out + output.err
    assert all(record.exc_info is None for record in caplog.records)


def test_cli_confirmation_mismatch_never_runs_bootstrap(monkeypatch, caplog, capsys) -> None:
    other_password = "Different-Secret42!"
    runner = AsyncMock()
    monkeypatch.setattr(bootstrap_cli, "configure_logging", lambda _level: None)
    monkeypatch.setattr(bootstrap_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        bootstrap_cli.getpass, "getpass", Mock(side_effect=[PASSWORD, other_password])
    )
    monkeypatch.setattr(bootstrap_cli, "_run", runner)
    with caplog.at_level("ERROR", logger=bootstrap_cli.__name__):
        assert bootstrap_cli.main(ARGS) == 1
    runner.assert_not_called()
    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith("Bootstrap failed.")
    output = capsys.readouterr()
    for secret in (PASSWORD, other_password):
        assert secret not in caplog.text + output.out + output.err
