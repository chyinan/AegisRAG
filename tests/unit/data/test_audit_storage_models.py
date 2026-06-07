from typing import Any, cast

from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.common.logging import REDACTED_VALUE
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import build_audit_log_model


def _column_names(model: Any) -> set[str]:
    return set(model.__table__.columns.keys())


def _index_map(model: Any) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in model.__table__.indexes
        if index.name is not None
    }


def test_audit_log_model_contains_required_governance_and_observability_fields() -> None:
    assert AuditLogModel.__tablename__ == "audit_logs"
    assert {
        "id",
        "created_at",
        "updated_at",
        "tenant_id",
        "user_id",
        "created_by",
        "status",
        "request_id",
        "trace_id",
        "action",
        "resource_type",
        "resource_id",
        "resource_metadata",
        "latency_ms",
        "error_code",
        "metadata",
    } <= _column_names(AuditLogModel)


def test_audit_log_model_indexes_operational_fields() -> None:
    indexes = _index_map(AuditLogModel)

    assert indexes["ix_audit_logs_tenant_id"] == ("tenant_id",)
    assert indexes["ix_audit_logs_user_id"] == ("user_id",)
    assert indexes["ix_audit_logs_request_id"] == ("request_id",)
    assert indexes["ix_audit_logs_trace_id"] == ("trace_id",)
    assert indexes["ix_audit_logs_created_at"] == ("created_at",)
    assert indexes["ix_audit_logs_status"] == ("status",)


def test_audit_log_model_builder_redacts_sensitive_metadata_before_persistence() -> None:
    event = AuditEvent(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        action="document.read",
        resource=AuditResource(
            type="document",
            id="doc-1",
            metadata={
                "file_path": "D:/private/hr-policy.pdf",
                "safe": "summary",
            },
        ),
        status=AuditStatus.SUCCESS,
        latency_ms=12.5,
        metadata={
            "api_key": "sk-secret-value",
            "prompt": "reveal the policy",
            "nested": {"Authorization": "Bearer abc.def.ghi"},
            "safe_count": 3,
        },
    )

    model = build_audit_log_model(event, audit_log_id="audit-1")

    assert model.resource_metadata["file_path"] == REDACTED_VALUE
    assert model.resource_metadata["safe"] == "summary"
    assert model.metadata_["api_key"] == REDACTED_VALUE
    assert model.metadata_["prompt"] == REDACTED_VALUE
    nested = cast(dict[str, object], model.metadata_["nested"])
    assert nested["Authorization"] == REDACTED_VALUE
    assert model.metadata_["safe_count"] == 3
