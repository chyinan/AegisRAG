"""Bootstrap CLI fails safely and never reports credential material."""

from pathlib import Path
from unittest.mock import AsyncMock

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
