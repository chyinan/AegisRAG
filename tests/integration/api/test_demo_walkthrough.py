from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_openwebui_chat_adapter, get_source_resolve_service
from packages.common.context import AuthenticatedRequestContext
from packages.data.demo_seed import load_demo_manifest
from packages.data.demo_walkthrough import DemoWalkthroughRunner, WalkthroughHttpResponse
from packages.rag.dto import Citation
from packages.rag.openwebui import (
    OpenAIChatChoice,
    OpenAIChatChoiceMessage,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIUsage,
)
from packages.rag.source_resolver import SourceResolveCommand, SourceResolveResponse

MANIFEST_PATH = Path("docs/demo/enterprise-rag/manifest.json")


class StubOpenWebUIAdapter:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]] = []
        self.stream_calls: list[
            tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]
        ] = []

    async def chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> OpenAIChatCompletionResponse:
        self.chat_calls.append((context, request))
        query = request.messages[-1].content
        no_answer = "火星" in query
        citations = () if no_answer else (_citation_for_query(query),)
        return OpenAIChatCompletionResponse(
            id=f"chatcmpl-{context.request_id}",
            created=1,
            model="configured-rag-model",
            choices=(
                OpenAIChatChoice(
                    index=0,
                    message=OpenAIChatChoiceMessage(
                        content=(
                            "无法从已授权上下文确认。"
                            if no_answer
                            else "年假审批需要直属经理确认。"
                        )
                    ),
                ),
            ),
            usage=OpenAIUsage(total_tokens=10),
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id="session-demo",
            citations=citations,
            no_answer=no_answer,
            metadata={"retrieval": {"result_count": len(citations)}, "safe": "ok"},
        )

    async def stream_chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> AsyncIterator[str]:
        self.stream_calls.append((context, request))
        yield (
            'data: {"object":"chat.completion.chunk","request_id":"'
            + context.request_id
            + '","trace_id":"'
            + context.trace_id
            + '","session_id":"session-demo","citations":[],"choices":'
            + '[{"delta":{},"index":0,"finish_reason":"stop"}]}\n\n'
        )
        yield "data: [DONE]\n\n"


def _citation_for_query(query: str) -> Citation:
    if "提示注入" in query:
        return Citation(
            document_id="doc-demo-rag-ops",
            version_id="ver-demo-rag-ops",
            chunk_id="chunk-demo-tech-prompt-injection",
            source_display_name="RAG Operations Technical Notes",
            source_type="markdown",
            page_start=1,
            page_end=1,
            title_path=("RAG Operations Technical Notes", "Prompt Injection Sample"),
            score=0.91,
            retrieval_method="hybrid",
        )
    return Citation(
        document_id="doc-demo-hr-policy",
        version_id="ver-demo-hr-policy",
        chunk_id="chunk-demo-hr-policy-leave",
        source_display_name="HR Leave Policy",
        source_type="markdown",
        page_start=1,
        page_end=1,
        title_path=("HR Leave Policy", "Annual Leave"),
        score=0.92,
        retrieval_method="hybrid",
    )


class StubSourceResolveService:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, SourceResolveCommand]] = []

    async def resolve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: SourceResolveCommand,
    ) -> SourceResolveResponse:
        self.calls.append((context, command))
        return SourceResolveResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            document_id=command.document_id,
            version_id=command.version_id,
            chunk_id=command.chunk_id,
            source_display_name="HR Leave Policy",
            source_type="markdown",
            page_start=1,
            page_end=1,
            title_path=("HR Leave Policy", "Annual Leave"),
            text_excerpt="Synthetic excerpt for annual leave approvals.",
            excerpt_char_count=45,
            token_count=8,
            retrieval_method="hybrid",
            score=0.92,
        )


class ClientWalkthroughTransport:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def post_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> WalkthroughHttpResponse:
        _ = timeout_seconds
        response = self.client.post(path, headers=headers, json=payload)
        return WalkthroughHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            json_body=response.json() if response.text else None,
        )


class CapturingWalkthroughTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def post_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> WalkthroughHttpResponse:
        _ = timeout_seconds
        self.calls.append((path, headers, payload))
        return WalkthroughHttpResponse(
            status_code=200,
            text="{}",
            json_body={
                "request_id": headers["X-Request-ID"],
                "trace_id": headers["X-Trace-ID"],
                "session_id": "session-demo",
                "citations": [
                    {
                        "document_id": "doc-demo-hr-policy",
                        "version_id": "ver-demo-hr-policy",
                        "chunk_id": "chunk-demo-hr-policy-leave",
                    }
                ],
                "metadata": {"retrieval": {"result_count": 1}},
            },
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_demo_walkthrough_runner_validates_chat_and_source_resolve_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    source_service = StubSourceResolveService()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    app.dependency_overrides[get_source_resolve_service] = lambda: source_service
    client = TestClient(app)
    runner = DemoWalkthroughRunner(
        manifest=load_demo_manifest(MANIFEST_PATH),
        http=ClientWalkthroughTransport(client),
        report_dir=tmp_path,
    )

    report = await runner.run(case_selector=("case-demo-hr-leave", "case-demo-source-resolve"))

    assert report.summary.case_count == 2
    assert report.summary.failed_count == 0
    assert len(adapter.chat_calls) == 2
    assert len(source_service.calls) == 1
    _, source_command = source_service.calls[0]
    assert source_command.document_id == "doc-demo-hr-policy"
    assert source_command.chunk_id == "chunk-demo-hr-policy-leave"
    report_text = next(tmp_path.glob("enterprise-rag-walkthrough-*.json")).read_text(
        encoding="utf-8"
    )
    assert "source_uri" not in report_text
    assert "Synthetic excerpt" not in report_text
    assert "年假审批" not in report_text


@pytest.mark.asyncio
async def test_demo_walkthrough_runner_validates_no_answer_acl_and_prompt_injection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: StubOpenWebUIAdapter()
    app.dependency_overrides[get_source_resolve_service] = lambda: StubSourceResolveService()
    runner = DemoWalkthroughRunner(
        manifest=load_demo_manifest(MANIFEST_PATH),
        http=ClientWalkthroughTransport(TestClient(app)),
        report_dir=tmp_path,
    )

    report = await runner.run(
        case_selector=(
            "case-demo-no-answer",
            "case-demo-acl-isolation",
            "case-demo-prompt-injection",
        )
    )

    assert report.summary.case_count == 3
    assert report.summary.failed_count == 0
    by_case = {case.case_id: case for case in report.cases}
    assert by_case["case-demo-no-answer"].no_answer is True
    assert by_case["case-demo-acl-isolation"].citation_count == 0
    assert by_case["case-demo-prompt-injection"].prompt_injection_safe is True


def test_openwebui_streaming_response_keeps_safe_metadata_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers("req-demo-stream"),
        json={
            "model": "configured-rag-model",
            "stream": True,
            "messages": [{"role": "user", "content": "年假审批怎么走？"}],
            "metadata_filter": {"category": "policy"},
        },
    )

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert "source_uri" not in response.text
    assert "event: token" not in response.text
    assert adapter.stream_calls[0][0].auth.tenant_id == "tenant-demo-alpha"


@pytest.mark.asyncio
async def test_demo_walkthrough_runner_supports_bearer_token_and_report_path(
    tmp_path: Path,
) -> None:
    transport = CapturingWalkthroughTransport()
    report_path = tmp_path / "walkthrough-report.json"
    runner = DemoWalkthroughRunner(
        manifest=load_demo_manifest(MANIFEST_PATH),
        http=transport,
        bearer_tokens_by_user={"demo-user-employee": "local-service-token"},
        report_path=report_path,
    )

    report = await runner.run(case_selector=("case-demo-hr-leave",))

    assert report.summary.failed_count == 0
    _path, headers, _payload = transport.calls[0]
    assert headers["Authorization"] == "Bearer local-service-token"
    assert "X-User-ID" not in headers
    assert report_path.exists()


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "acl", "roles", "permissions"])
def test_openwebui_demo_rejects_auth_scope_metadata_filters(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: StubOpenWebUIAdapter()
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers("req-demo-filter"),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "metadata_filter": {field: "attacker-controlled"},
        },
    )

    assert response.status_code == 422
    assert "attacker-controlled" not in response.text


def _auth_headers(request_id: str) -> dict[str, str]:
    return {
        "X-Request-ID": request_id,
        "X-Trace-ID": f"trace-{request_id}",
        "X-User-ID": "demo-user-employee",
        "X-Tenant-ID": "tenant-demo-alpha",
        "X-Roles": "knowledge_user",
        "X-Permissions": "document:read,retrieval:query",
    }
