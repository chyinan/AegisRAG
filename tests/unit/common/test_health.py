from packages.common.health import (
    DependencyStatus,
    HealthData,
    ReadinessData,
    get_health_data,
)


def test_get_health_data_returns_api_liveness_payload() -> None:
    health = get_health_data()

    assert isinstance(health, HealthData)
    assert health.status == "ok"
    assert health.service == "api"
    assert health.version == "0.1.0"


def test_readiness_data_supports_structured_dependency_summary() -> None:
    readiness = ReadinessData(
        ready=True,
        dependencies=[
            DependencyStatus(
                name="database",
                status="not_configured",
                required=False,
                blocking=False,
                message="database dependency is not configured.",
            )
        ],
    )

    assert isinstance(readiness, ReadinessData)
    assert readiness.ready is True
    assert readiness.dependencies

    dependency_names = {dependency.name for dependency in readiness.dependencies}
    assert dependency_names == {"database"}

    for dependency in readiness.dependencies:
        assert isinstance(dependency, DependencyStatus)
        assert dependency.status == "not_configured"
        assert dependency.required is False
        assert dependency.blocking is False
        assert dependency.message


def test_readiness_payload_serializes_without_log_text_parsing() -> None:
    payload = ReadinessData(
        ready=True,
        dependencies=[
            DependencyStatus(
                name="redis",
                status="not_configured",
                required=False,
                blocking=False,
                message="redis dependency is not configured.",
            )
        ],
    ).model_dump()

    assert payload["ready"] is True
    assert isinstance(payload["dependencies"], list)
    assert payload["dependencies"][0].keys() == {
        "name",
        "status",
        "required",
        "blocking",
        "message",
        "details",
    }
