from typing import Literal

from pydantic import BaseModel, Field

API_SERVICE_NAME = "api"
API_VERSION = "0.1.0"


class HealthData(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = API_SERVICE_NAME
    version: str = API_VERSION


class DependencyStatus(BaseModel):
    name: str
    status: Literal["not_configured", "ok", "degraded", "unavailable"]
    required: bool
    blocking: bool
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ReadinessData(BaseModel):
    ready: bool
    dependencies: list[DependencyStatus]


def get_health_data() -> HealthData:
    return HealthData()
