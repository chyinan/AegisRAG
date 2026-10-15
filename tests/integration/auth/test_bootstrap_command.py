"""Real CLI over a migrated temporary SQLite DB, never a deployed account."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_migrated_database_bootstrap_command_is_idempotent_and_silent_about_secrets(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bootstrap.db"
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "APP_ENV": "test",
        "LLM_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "fake",
        "LLM_MAX_OUTPUT_TOKENS": "",
        "LLM_TEMPERATURE": "",
    }
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert migration.returncode == 0, migration.stderr
    secret = tmp_path / "bootstrap-password"
    password = "Initial-Bootstrap42!"
    secret.write_text(password + "\n")
    command = [
        sys.executable,
        "-m",
        "packages.auth.bootstrap_cli",
        "--tenant-id",
        "default",
        "--tenant-name",
        "Default Tenant",
        "--admin-username",
        "admin",
        "--password-file",
        str(secret),
    ]
    first = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(db_path) as connection:
        original = connection.execute(
            "SELECT id, password_hash, group_id FROM local_users WHERE tenant_id = 'default'"
        ).fetchone()
    secret.write_text("Changed-Bootstrap42!\n")
    second = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert second.returncode == 0, second.stderr
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT id, password_hash, group_id FROM local_users WHERE tenant_id = 'default'"
        ).fetchall() == [original]
    output = first.stdout + first.stderr + second.stdout + second.stderr
    for forbidden in (password, "Changed-Bootstrap42!", original[1], str(secret), str(db_path)):
        assert forbidden not in output
