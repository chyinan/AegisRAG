"""Prevent frontend mock streams drifting from the backend wire contract."""

from scripts.generate_web_stream_fixtures import FIXTURE_PATH, serialized_fixtures


def test_frontend_stream_fixtures_match_backend_serialization() -> None:
    assert FIXTURE_PATH.read_text(encoding="utf-8") == serialized_fixtures(), (
        "Run PYTHONPATH=. python scripts/generate_web_stream_fixtures.py"
    )
