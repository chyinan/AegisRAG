import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

MIGRATION_PATH = Path("migrations/versions/20260527_0005_vector_records.py")


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vector_records_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pgvector_column_type_is_stable_and_environment_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()

    monkeypatch.setenv("VECTOR_INDEX_DIM", "16")

    assert migration.PgVector().get_col_spec() == "vector"
