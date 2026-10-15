"""Settings contract shared by explicit uv and Compose environment loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.common.config import AppSettings, load_settings


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_blank_optional_llm_numbers_mean_provider_default(value: str) -> None:
    settings = AppSettings(LLM_MAX_OUTPUT_TOKENS=value, LLM_TEMPERATURE=value)
    assert settings.llm_max_output_tokens is None
    assert settings.llm_temperature is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("LLM_MAX_OUTPUT_TOKENS", "invalid"),
        ("LLM_MAX_OUTPUT_TOKENS", "0"),
        ("LLM_MAX_OUTPUT_TOKENS", "1.5"),
        ("LLM_MAX_OUTPUT_TOKENS", "-1"),
        ("LLM_TEMPERATURE", "invalid"),
        ("LLM_TEMPERATURE", "nan"),
        ("LLM_TEMPERATURE", "inf"),
        ("LLM_TEMPERATURE", "-0.1"),
        ("LLM_TEMPERATURE", "2.1"),
    ],
)
def test_nonempty_invalid_optional_llm_numbers_fail(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value})


def test_optional_llm_numbers_accept_explicit_zero_temperature() -> None:
    settings = AppSettings(LLM_MAX_OUTPUT_TOKENS="1024", LLM_TEMPERATURE="0")
    assert settings.llm_max_output_tokens == 1024
    assert settings.llm_temperature == 0.0


def test_settings_do_not_implicitly_load_working_directory_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text("LLM_MAX_OUTPUT_TOKENS=not-an-integer\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
    assert load_settings().llm_max_output_tokens is None
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "64")
    assert load_settings().llm_max_output_tokens == 64


@pytest.mark.parametrize(
    "field", ["INGESTION_QUEUE_NAME", "EMBEDDING_QUEUE_NAME", "WORKER_QUEUE_NAME"]
)
def test_queue_names_are_nonblank_and_normalized_consistently(field: str) -> None:
    assert getattr(AppSettings(**{field: " custom-queue "}), field.lower()) == "custom-queue"
    with pytest.raises(ValidationError):
        AppSettings(**{field: "  "})


def test_explicit_uv_env_file_reaches_settings_and_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv CLI is not installed; explicit environment loading is not exercised.")
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "LLM_MAX_OUTPUT_TOKENS=512\nLLM_TEMPERATURE=\n"
        "JWT_SECRET=test-env-file-secret-at-least-32-bytes\n"
    )
    for key in ("LLM_MAX_OUTPUT_TOKENS", "LLM_TEMPERATURE", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    result = subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--offline",
            "--no-python-downloads",
            "--python",
            sys.executable,
            "--env-file",
            str(env_file),
            sys.executable,
            "-c",
            "from packages.common.config import load_settings; "
            "from packages.auth.parsers import JwtAuthSettings; "
            "assert load_settings().llm_max_output_tokens == 512; "
            "assert load_settings().llm_temperature is None; "
            "assert JwtAuthSettings.from_environment().secret == "
            "'test-env-file-secret-at-least-32-bytes'",
        ],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_sanitized_example_environment_constructs_shared_application_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Read the real distributed example rather than maintaining another default
    # map in this test. Replace only explanatory secret placeholders. This is a
    # settings construction check, not Docker Compose interpolation/startup.
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.startswith("<") and value.endswith(">"):
            value = "synthetic-example-secret-at-least-32-bytes"
        monkeypatch.setenv(key, value)
    settings = load_settings()
    assert settings.llm_max_output_tokens is None
    assert settings.llm_temperature is None
    assert settings.llm_provider == "fake"
    assert settings.embedding_provider == "fake"
    assert settings.embedding_dim == settings.vector_index_dim == 8
    assert settings.ingestion_queue_name == "ingestion"
    assert settings.embedding_queue_name == "embedding"
