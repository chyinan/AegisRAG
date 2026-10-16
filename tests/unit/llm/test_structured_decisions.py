"""Native decision boundary against real LangChain objects and mocked transport."""

# ruff: noqa: E402 - optional dependency is isolated from default installations
from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

pytest.importorskip("langchain_openai")
from langchain_core.messages import AIMessage, AIMessageChunk

from packages.llm.decisions import (
    FINAL_ANSWER_TOOL,
    DecisionRequest,
    DecisionToolSchema,
)
from packages.llm.exceptions import LLMProviderError
from tests.unit.llm.test_langchain_provider import (
    _body,
    _observed_provider,
    _ObservedModel,
    _provider,
    _request,
)


def _decision_request(**kwargs: Any) -> DecisionRequest:
    return DecisionRequest(
        **dict(_request(**kwargs)),
        tools=(
            DecisionToolSchema(
                name="calculator",
                description="Arithmetic only",
                parameters={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                    "additionalProperties": False,
                },
            ),
        ),
        final_answer_schema={
            "type": "object",
            "properties": {"final_answer": {"type": "string"}},
            "required": ["final_answer"],
            "additionalProperties": False,
        },
    )


def _call(name: str = "calculator", arguments: str = '{"expression":"2+2"}') -> dict[str, Any]:
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _decision_body(*calls: dict[str, Any]) -> dict[str, Any]:
    body = _body()
    body["choices"][0].update(
        {
            "message": {"role": "assistant", "content": None, "tool_calls": list(calls)},
            "finish_reason": "tool_calls",
        }
    )
    return body


@pytest.mark.parametrize("final", [False, True])
async def test_native_single_decision_preserves_usage_and_declarative_schema(final: bool) -> None:
    requests: list[dict[str, Any]] = []
    call = _call(FINAL_ANSWER_TOOL, '{"final_answer":"4"}') if final else _call()

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_decision_body(call))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await _provider(client).decide(_decision_request())
    assert response.action == ("final_answer" if final else "tool_call")
    assert response.final_answer == ({"final_answer": "4"} if final else None)
    assert response.arguments == ({} if final else {"expression": "2+2"})
    assert response.metadata.usage.total_tokens == 10
    assert response.metadata.finish_reason == "tool_calls"
    assert response.metadata.metadata["usage_unavailable_count"] == 0
    assert response.metadata.tenant_id == "tenant-1"
    assert response.metadata.request_id == "request-1"
    assert response.metadata.version == "test-version"
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == [
        "calculator",
        FINAL_ANSWER_TOOL,
    ]
    assert requests[0]["tools"][0]["function"]["parameters"] == (
        _decision_request().tools[0].parameters
    )
    assert "tenant_id" not in requests[0] and "metadata" not in requests[0]
    assert "handler" not in json.dumps(requests[0]["tools"])


@pytest.mark.parametrize(
    "calls",
    [
        (),
        (_call(), _call()),
        (_call("unknown_tool"),),
        (_call(arguments="broken"),),
        (_call(arguments='["not-an-object"]'),),
        (_call(arguments='{"value":NaN}'),),
    ],
)
async def test_invalid_native_calls_fail_without_retry(calls: tuple[dict[str, Any], ...]) -> None:
    requests = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=_decision_body(*calls))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(LLMProviderError, match="LLM_PROVIDER_MALFORMED_RESPONSE"):
            await _provider(client).decide(_decision_request(retry_budget=2))
    assert requests == 1


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content="text without native decision"),
        AIMessage(
            content="mixed text",
            tool_calls=[
                {
                    "name": "calculator",
                    "args": {"expression": "2+2"},
                    "id": "call-1",
                }
            ],
        ),
        AIMessage(content=[{"type": "text", "text": "unsupported"}]),
        AIMessage(content="", additional_kwargs={"function_call": {"name": "calculator"}}),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "calculator",
                    "args": "{broken",
                    "id": "call-1",
                    "error": "invalid",
                }
            ],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "calculator",
                    "args": "{}",
                    "id": "call-1",
                    "index": 0,
                }
            ],
        ),
        AIMessage(content="", tool_calls=[{"name": "calculator", "args": {}, "id": None}]),
        AIMessage(
            content="",
            tool_calls=[{"name": "calculator", "args": {}, "id": "call-1"}],
            response_metadata={"finish_reason": "length"},
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "calculator", "args": {}, "id": "call-1"}],
            additional_kwargs={"refusal": "refused"},
        ),
    ],
)
def test_unsupported_outputs_are_explicitly_rejected(message: Any) -> None:
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_MALFORMED_RESPONSE"):
        _observed_provider(_ObservedModel())._parse_decision(message, _decision_request())


async def test_native_missing_usage_is_marked_unavailable() -> None:
    body = _decision_body(_call())
    body.pop("usage")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        response = await _provider(client).decide(_decision_request())
    assert response.metadata.usage.total_tokens == 0
    assert response.metadata.metadata["usage_unavailable_count"] == 1


@pytest.mark.parametrize(
    "status,code,attempts",
    [
        (401, "LLM_PROVIDER_AUTH_FAILED", 1),
        (400, "LLM_GENERATION_INVALID_REQUEST", 1),
        (429, "LLM_PROVIDER_RATE_LIMITED", 3),
        (503, "LLM_PROVIDER_FAILED", 3),
    ],
)
async def test_native_errors_retry_budget_and_redaction(
    status: int, code: str, attempts: int
) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"message": "private-user-prompt sk-secret"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(LLMProviderError, match=code) as caught:
            await _provider(client).decide(_decision_request(retry_budget=2))
    assert calls == attempts
    assert "sk-secret" not in "".join(traceback.format_exception(caught.value))
    assert "private-user-prompt" not in repr(caught.value.details)


async def test_native_timeout_and_cancellation_propagate_to_transport() -> None:
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def handle(_: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        raise AssertionError("must not finish")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = _provider(client)
        with pytest.raises(LLMProviderError, match="LLM_PROVIDER_TIMEOUT"):
            await provider.decide(_decision_request(timeout_seconds=0.05, retry_budget=1))
        assert len(cancelled) == 2
        started.clear()
        pending = asyncio.create_task(provider.decide(_decision_request()))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(cancelled) == 3


async def test_native_ambient_callbacks_collectors_and_tracing_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.runnables.config import set_config_context
    from langchain_core.tracers.context import collect_runs
    from langsmith import get_tracing_context

    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
            seen.append(messages)

    def handle(_: httpx.Request) -> httpx.Response:
        assert get_tracing_context()["enabled"] is False
        return httpx.Response(200, json=_decision_body(_call()))

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with collect_runs() as collector:
            with set_config_context({"callbacks": [Capture()]}) as inherited:
                await asyncio.create_task(
                    _provider(client).decide(_decision_request()), context=inherited
                )
            assert collector.traced_runs == []
    assert seen == []


async def test_native_debug_logging_fails_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import langchain_core.globals as globals_module

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No network when debug is enabled"))
    ) as client:
        monkeypatch.setattr(globals_module, "_debug", True)
        with pytest.raises(LLMProviderError, match="LLM_PROVIDER_FAILED"):
            await _provider(client).decide(_decision_request())
    assert "private-user-prompt" not in capsys.readouterr().out


def test_decision_schemas_are_json_only_and_reserved_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DecisionToolSchema(
            name="test", description="test", parameters={"type": "object", "x": object()}
        )
    request = _decision_request()
    with pytest.raises(ValidationError, match="unique"):
        DecisionRequest(**{**dict(request), "tools": request.tools * 2})
    with pytest.raises(ValidationError, match="final-answer name"):
        DecisionRequest(
            **{
                **dict(request),
                "tools": (
                    DecisionToolSchema(
                        name=FINAL_ANSWER_TOOL, description="test", parameters={"type": "object"}
                    ),
                ),
            }
        )


def test_raw_native_arguments_when_exposed_must_agree_and_have_unique_keys() -> None:
    message = AIMessage(
        content="",
        tool_calls=[{"name": "calculator", "args": {"expression": "2+2"}, "id": "call-1"}],
        additional_kwargs={
            "tool_calls": [_call(arguments='{"expression":"1+1","expression":"2+2"}')]
        },
    )
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_MALFORMED_RESPONSE"):
        _observed_provider(_ObservedModel())._parse_decision(message, _decision_request())


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_real_native_binding_composes_with_governed_runtime(backend: str) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    from apps.api.agent_stepper import LLMStructuredAgentStepper
    from apps.api.factories.agent import create_agent_runtime
    from packages.agent.runtime import AgentTerminationReason
    from tests.unit.agent.test_runtime import HandlerProbe, _config, _context, _registry

    handler = HandlerProbe()
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        sent = json.loads(request.content)
        if calls == 1:
            call = _call("demo_tool", '{"query":"hi"}')
        else:
            assert "observations" in sent["messages"][-1]["content"]
            call = _call(FINAL_ANSWER_TOOL, '{"final_answer":"done","final_citations":[]}')
        return httpx.Response(200, json=_decision_body(call))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime = create_agent_runtime(
            backend=backend,
            registry=_registry(handler=handler),
            audit=None,
            config=_config(),
            stepper=LLMStructuredAgentStepper(
                decision_provider=_provider(client),
                provider="openai_compatible",
                model="test-chat",
                tools=(
                    DecisionToolSchema(
                        name="demo_tool",
                        description="Bound schema only",
                        parameters={"type": "object"},
                    ),
                ),
            ),
        )
        result = await runtime.run(context=_context())
    assert result.termination_reason == AgentTerminationReason.FINAL_ANSWER
    assert result.steps_used == calls == 2
    assert result.tool_calls_used == handler.call_count == 1


@pytest.mark.parametrize("factory_owned", [False, True])
@pytest.mark.parametrize(
    "malformation",
    [
        "duplicate_argument",
        "nested_duplicate_argument",
        "duplicate_final_answer",
        "missing_type",
        "unsupported_type",
        "nonstring_arguments",
        "multiple_choices",
    ],
)
async def test_raw_native_transport_is_validated_before_framework_normalization(
    factory_owned: bool,
    malformation: str,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    from apps.api.factories.common import create_llm_provider
    from packages.common.config import AppSettings
    from packages.llm.ports import DecisionProvider

    call = _call()
    body = _decision_body(call)
    if malformation == "duplicate_argument":
        call["function"]["arguments"] = '{"expression":"2+2","expression":"3+3"}'
    elif malformation == "nested_duplicate_argument":
        call["function"]["arguments"] = '{"expression":"2+2","nested":{"value":1,"value":2}}'
    elif malformation == "duplicate_final_answer":
        call["function"] = {
            "name": FINAL_ANSWER_TOOL,
            "arguments": '{"final_answer":"first","final_answer":"second"}',
        }
    elif malformation == "missing_type":
        del call["type"]
    elif malformation == "unsupported_type":
        call["type"] = "custom"
    elif malformation == "nonstring_arguments":
        call["function"]["arguments"] = {"expression": "private-malformed-native-payload"}
    else:
        body["choices"] = body["choices"] * 2
    requests = 0
    clients: list[Any] = []

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=body)

    if factory_owned:
        real_sync, real_async = httpx.Client, httpx.AsyncClient

        class LocalSyncClient(real_sync):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(transport=httpx.MockTransport(lambda _: pytest.fail("sync I/O")))
                clients.append(self)

        class LocalAsyncClient(real_async):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(transport=httpx.MockTransport(handle))
                clients.append(self)

        monkeypatch.setattr(httpx, "Client", LocalSyncClient)
        monkeypatch.setattr(httpx, "AsyncClient", LocalAsyncClient)
        provider = create_llm_provider(
            AppSettings.model_validate(
                {
                    "LLM_ADAPTER": "langchain",
                    "LLM_PROVIDER": "openai_compatible",
                    "LLM_MODEL": "test-chat",
                    "LLM_BASE_URL": "https://models.test/v1",
                    "LLM_API_KEY": "test-key",
                    "AGENT_DECISION_MODE": "native_tools",
                }
            )
        )
        assert isinstance(provider, DecisionProvider)
        with pytest.raises(LLMProviderError) as caught:
            await provider.decide(_decision_request(retry_budget=2))
        assert len(clients) == 2 and all(client.is_closed for client in clients)
    else:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(LLMProviderError) as caught:
                await _provider(client).decide(_decision_request(retry_budget=2))
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"
    assert requests == 1
    assert "expression" not in "".join(traceback.format_exception(caught.value))
    assert not recwarn, "malformed provider payloads must not leak via serializer warnings"


async def test_raw_native_envelope_duplicate_members_are_rejected() -> None:
    payload = json.dumps(_decision_body(_call()))
    payload = payload.replace('"type": "function"', '"type": "custom", "type": "function"')
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=payload, headers={"content-type": "application/json"}
            )
        )
    ) as client:
        with pytest.raises(LLMProviderError, match="LLM_PROVIDER_MALFORMED_RESPONSE"):
            await _provider(client).decide(_decision_request())


@pytest.mark.parametrize(
    "updates",
    [
        {"use_responses_api": True},
        {"output_version": "responses/v1"},
        {"model_kwargs": {"response_format": {"type": "json_object"}}},
    ],
)
async def test_alternate_sdk_routes_cannot_bypass_raw_native_validation(
    updates: dict[str, Any],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: pytest.fail("Unsupported API route must not make a request")
        )
    ) as client:
        provider = _provider(client)
        provider._chat_model = provider._chat_model.model_copy(update=updates)
        with pytest.raises(LLMProviderError, match="LLM_GENERATION_INVALID_REQUEST"):
            await provider.decide(_decision_request())
