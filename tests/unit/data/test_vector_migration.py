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


def test_pgvector_column_type_includes_configured_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()

    monkeypatch.setenv("VECTOR_INDEX_DIM", "16")

    assert migration.PgVector(migration._vector_index_dim()).get_col_spec() == "vector(16)"


def test_pgvector_dimension_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration_module()

    monkeypatch.setenv("VECTOR_INDEX_DIM", "0")

    with pytest.raises(ValueError, match="greater than 0"):
        migration._vector_index_dim()
