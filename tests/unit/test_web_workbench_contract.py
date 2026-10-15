from scripts.generate_web_workbench_fixtures import FIXTURE_PATH, serialized_fixtures


def test_browser_http_contract_fixtures_match_backend_dtos() -> None:
    assert FIXTURE_PATH.read_text(encoding="utf-8") == serialized_fixtures()


def test_example_environment_supports_local_pilot_tokens() -> None:
    from pathlib import Path

    values = dict(
        line.split("=", 1)
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["JWT_JWKS_URL"] == ""
    assert values["JWT_ISSUER"] == ""
    assert values["JWT_AUDIENCE"] == ""
    assert values["ENABLE_DEV_AUTH_HEADERS"] == "false"
    assert values["JWT_REQUIRE_LOCAL_SESSION"] == "true"
