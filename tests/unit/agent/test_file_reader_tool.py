from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from packages.agent.dto import ToolExecutionResult, ToolInvocationStatus, ToolRateLimit
from packages.agent.exceptions import TOOL_PERMISSION_DENIED, AgentToolError
from packages.agent.registry import InMemoryToolRateLimiter, ToolRegistry
from packages.agent.tools import (
    FILE_ACCESS_DENIED,
    FILE_NOT_READABLE,
    FILE_READER_PERMISSION,
    FILE_TOO_LARGE,
    FILE_UNSUPPORTED_TYPE,
    FileReaderOutput,
    build_file_reader_tool,
)
from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


def _context(
    *,
    permissions: tuple[str, ...] = (FILE_READER_PERMISSION,),
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("analyst",),
            department="delivery",
            permissions=permissions,
        ),
    )


def _registry(
    *,
    root: Path,
    audit: InMemoryAuditPort | None = None,
    max_file_bytes: int = 64,
    max_return_bytes: int = 24,
) -> ToolRegistry:
    registry = ToolRegistry(
        audit=audit or InMemoryAuditPort(),
        rate_limiter=InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 10.0,
    )
    registry.register(
        build_file_reader_tool(
            allowlist_roots=(root,),
            max_file_bytes=max_file_bytes,
            max_return_bytes=max_return_bytes,
            timeout_seconds=2.0,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60.0),
        )
    )
    return registry


def _output(result: ToolExecutionResult) -> dict[str, Any]:
    assert result.output is not None
    return result.output


@pytest.mark.asyncio
async def test_file_reader_definition_registers_and_reads_allowlisted_text_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "policy.txt").write_text("leave policy summary", encoding="utf-8")
    audit = InMemoryAuditPort()
    registry = _registry(root=root, audit=audit)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": "policy.txt"},
        context=_context(),
    )

    assert result.status is ToolInvocationStatus.SUCCESS
    assert result.output == {
        "status": "success",
        "file_ref": "policy.txt",
        "bytes_read": 20,
        "truncated": False,
        "content_excerpt": "leave policy summary",
        "error_code": None,
        "message": None,
    }
    assert audit.events[0].status is AuditStatus.SUCCESS
    assert audit.events[0].metadata["tool_name"] == "file_reader"
    assert audit.events[0].metadata["permission"] == FILE_READER_PERMISSION
    assert audit.events[0].metadata["argument_keys"] == ["path"]
    assert str(root) not in str(audit.events[0].metadata)


@pytest.mark.asyncio
async def test_file_reader_truncates_returned_content_without_reading_full_large_excerpt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text("abcdefghijklmnopqrstuvwxyz", encoding="utf-8")
    registry = _registry(root=root, max_file_bytes=64, max_return_bytes=10)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": "notes.txt"},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "success"
    assert output["file_ref"] == "notes.txt"
    assert output["bytes_read"] == 10
    assert output["truncated"] is True
    assert output["content_excerpt"] == "abcdefghij"


@pytest.mark.parametrize(
    ("requested_path", "expected_error"),
    [
        ("../outside.txt", FILE_ACCESS_DENIED),
        ("missing.txt", FILE_ACCESS_DENIED),
        (".hidden.txt", FILE_ACCESS_DENIED),
        (".env", FILE_ACCESS_DENIED),
        ("secret.key", FILE_ACCESS_DENIED),
    ],
)
@pytest.mark.asyncio
async def test_file_reader_rejects_unsafe_paths_without_leaking_existence_or_absolute_paths(
    tmp_path: Path,
    requested_path: str,
    expected_error: str,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("outside secret", encoding="utf-8")
    (root / ".hidden.txt").write_text("hidden secret", encoding="utf-8")
    (root / ".env").write_text("OPENAI_API_KEY=sk-secret", encoding="utf-8")
    (root / "secret.key").write_text("private key", encoding="utf-8")
    registry = _registry(root=root)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": requested_path},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "error"
    assert output["error_code"] == expected_error
    assert output["file_ref"] is None
    assert output["content_excerpt"] == ""
    assert str(tmp_path) not in str(output)
    assert "outside secret" not in str(output)
    assert "sk-secret" not in str(output)


@pytest.mark.asyncio
async def test_file_reader_rejects_absolute_path_even_when_target_is_inside_allowlist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "policy.txt"
    target.write_text("policy", encoding="utf-8")
    registry = _registry(root=root)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": str(target)},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "error"
    assert output["error_code"] == FILE_ACCESS_DENIED
    assert str(target) not in str(output)


@pytest.mark.asyncio
async def test_file_reader_rejects_directory_binary_file_and_oversized_file(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "folder").mkdir()
    (root / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
    (root / "large.txt").write_text("x" * 65, encoding="utf-8")
    registry = _registry(root=root, max_file_bytes=64)

    directory_result = await registry.execute(
        name="file_reader",
        arguments={"path": "folder"},
        context=_context(),
    )
    binary_result = await registry.execute(
        name="file_reader",
        arguments={"path": "binary.bin"},
        context=_context(),
    )
    large_result = await registry.execute(
        name="file_reader",
        arguments={"path": "large.txt"},
        context=_context(),
    )

    assert _output(directory_result)["error_code"] == FILE_NOT_READABLE
    assert _output(binary_result)["error_code"] == FILE_UNSUPPORTED_TYPE
    assert _output(large_result)["error_code"] == FILE_TOO_LARGE


@pytest.mark.asyncio
async def test_file_reader_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")
    registry = _registry(root=root)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": "link.txt"},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "error"
    assert output["error_code"] == FILE_ACCESS_DENIED
    assert "outside secret" not in str(output)


@pytest.mark.asyncio
async def test_file_reader_redacts_sensitive_content_from_excerpt(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "public.txt").write_text("api_key=sk-secret123456", encoding="utf-8")
    registry = _registry(root=root)

    result = await registry.execute(
        name="file_reader",
        arguments={"path": "public.txt"},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "success"
    assert output["content_excerpt"] == "[REDACTED]"
    assert "sk-secret" not in str(output)


@pytest.mark.asyncio
async def test_file_reader_permission_denied_prevents_file_access_and_keeps_audit_safe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "policy.txt").write_text("policy secret", encoding="utf-8")
    audit = InMemoryAuditPort()
    registry = _registry(root=root, audit=audit)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="file_reader",
            arguments={"path": "policy.txt"},
            context=_context(permissions=()),
        )

    assert exc_info.value.code == TOOL_PERMISSION_DENIED
    assert audit.events[0].status is AuditStatus.DENIED
    assert "policy secret" not in str(audit.events[0].metadata)
    assert str(root) not in str(audit.events[0].metadata)


def test_file_reader_output_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        FileReaderOutput.model_validate(
            {
                "status": "success",
                "file_ref": "policy.txt",
                "bytes_read": 6,
                "truncated": False,
                "content_excerpt": "policy",
                "absolute_path": "C:\\secret\\policy.txt",
            }
        )
