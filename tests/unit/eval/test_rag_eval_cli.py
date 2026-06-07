from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.rag import run_smoke

DATASET = Path("tests/eval/datasets/rag_smoke.json")


def test_rag_eval_cli_success_prints_safe_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_smoke.main(
        [
            "--dataset",
            str(DATASET),
            "--report-dir",
            str(tmp_path),
        ]
    )

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert '"case_count": 20' in stdout
    assert "How many annual leave" not in stdout
    assert "Synthetic HR policy" not in stdout
    assert list(tmp_path.glob("rag-smoke-*.json"))


def test_rag_eval_cli_dataset_error_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_smoke.main(["--dataset", "tests/eval/datasets/missing.json"])

    stdout = capsys.readouterr().out
    assert exit_code == 2
    assert "file_not_found" in stdout
    assert "D:\\" not in stdout


def test_rag_eval_cli_unexpected_runner_error_returns_3_without_raw_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_runner(*args: object, **kwargs: object) -> object:
        raise RuntimeError("secret query C:\\Users\\person\\token.txt")

    monkeypatch.setattr(run_smoke, "run_rag_eval", fail_runner)

    exit_code = run_smoke.main(["--dataset", str(DATASET)])

    stdout = capsys.readouterr().out
    assert exit_code == 3
    assert "rag eval runner error: runner" in stdout
    assert "secret query" not in stdout
    assert "C:\\Users" not in stdout
