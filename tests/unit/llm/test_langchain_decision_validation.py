"""Reject malformed decisions before SDK normalization or domain dispatch."""

# ruff: noqa: E402 - installed optional dependencies must import successfully
from __future__ import annotations

import importlib.metadata
import os
import traceback
from importlib import import_module
from typing import Any

import httpx
import pytest

for distribution in ("langchain-core", "langchain-openai"):
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        if os.environ.get("AEGISRAG_REQUIRE_FRAMEWORKS") == "1":
            raise pytest.UsageError(
                f"Required framework distribution is absent: {distribution}"
            ) from exc
        pytest.skip(
            f"Optional framework distribution is absent: {distribution}", allow_module_level=True
        )

# Import normally before reusing fixtures whose legacy importorskip is broader.
import_module("langchain_openai")

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from packages.llm.exceptions import LLMProviderError
from tests.unit.llm.test_langchain_provider import _observed_provider, _ObservedModel, _provider
from tests.unit.llm.test_structured_decisions import _call, _decision_body, _decision_request


@pytest.mark.parametrize(
    "path,value",
    [
        pytest.param(("choices", 0), None, id="nonmapping-choice"),
        pytest.param(("choices", 0, "message"), None, id="nonmapping-message"),
        pytest.param(("choices", 0, "message", "role"), "tool", id="non-assistant-role"),
        pytest.param(
            ("choices", 0, "message", "content"), "private-provider-payload", id="mixed-text"
        ),
        pytest.param(
            ("choices", 0, "message", "refusal"), "private-provider-payload", id="refusal"
        ),
        pytest.param(
            ("choices", 0, "message", "tool_calls", 0, "function"),
            None,
            id="nonmapping-function",
        ),
        pytest.param(("choices", 0, "finish_reason"), 7, id="nonstring-finish-reason"),
        pytest.param(("usage",), [], id="nonmapping-usage"),
    ],
)
async def test_raw_decision_envelope_fails_closed_without_retry(
    path: tuple[str | int, ...], value: Any, recwarn: pytest.WarningsRecorder
) -> None:
    body = _decision_body(_call())
    target: Any = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    await _assert_raw_decision_rejected(body)
    assert not recwarn, "Raw private payloads must not escape in SDK serializer warnings"


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens", "total_tokens"])
@pytest.mark.parametrize(
    "value", [-1, True, "1", 1.5], ids=["negative", "boolean", "text", "float"]
)
async def test_raw_decision_token_counts_are_strict_nonnegative_integers(
    field: str, value: Any, recwarn: pytest.WarningsRecorder
) -> None:
    body = _decision_body(_call())
    body["usage"][field] = value
    await _assert_raw_decision_rejected(body)
    assert not recwarn


async def _assert_raw_decision_rejected(body: dict[str, Any]) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).decide(_decision_request(retry_budget=2))
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"
    assert caught.value.retryable is False
    assert calls == 1
    assert "private-provider-payload" not in "".join(traceback.format_exception(caught.value))
    assert "private-provider-payload" not in repr(caught.value.details)
    assert "response" not in caught.value.details


class _DecisionModel(_ObservedModel):
    """A real BaseChatModel using its normal async generation/message protocol."""

    message: AIMessage
    invocations: list[str] = []
    bindings: list[dict[str, Any]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _DecisionModel:
        self.bindings.append({"tools": tools, **kwargs})
        return self

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        self.invocations.append("generate")
        return ChatResult(generations=[ChatGeneration(message=self.message)])


@pytest.mark.parametrize("expose_raw_call", [False, True])
async def test_injected_model_accepts_normalized_decision_and_agreeing_raw_call(
    expose_raw_call: bool,
) -> None:
    message = AIMessage(
        content="",
        tool_calls=[{"name": "calculator", "args": {"expression": "2+2"}, "id": "call-1"}],
        additional_kwargs={"tool_calls": [_call()]} if expose_raw_call else {},
    )
    model = _DecisionModel(message=message)
    response = await _observed_provider(model).decide(_decision_request())
    assert response.action == "tool_call"
    assert response.tool_name == "calculator"
    assert response.arguments == {"expression": "2+2"}
    assert response.metadata.metadata["usage_unavailable_count"] == 1
    assert model.invocations == ["generate"]
    assert model.bindings[0]["tool_choice"] == "required"
    assert model.bindings[0]["parallel_tool_calls"] is False
    assert [tool["function"]["name"] for tool in model.bindings[0]["tools"]] == [
        "calculator",
        "aegis_final_answer",
    ]


@pytest.mark.parametrize(
    "malformation",
    [
        "empty-raw-calls",
        "raw-mismatch",
        "nonjson-arguments",
        "nonfinite-arguments",
        "finish-reason",
    ],
)
async def test_injected_model_decisions_cannot_bypass_strict_argument_validation(
    malformation: str,
) -> None:
    arguments: dict[str, Any] = {"expression": "2+2"}
    additional: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    if malformation == "empty-raw-calls":
        additional["tool_calls"] = []
    elif malformation == "raw-mismatch":
        additional["tool_calls"] = [_call(arguments='{"expression":"private-provider-payload"}')]
    elif malformation == "nonjson-arguments":
        arguments["expression"] = object()
    elif malformation == "nonfinite-arguments":
        arguments["expression"] = float("inf")
    else:
        metadata["finish_reason"] = 7
    model = _DecisionModel(
        message=AIMessage(
            content="",
            tool_calls=[{"name": "calculator", "args": arguments, "id": "call-1"}],
            additional_kwargs=additional,
            response_metadata=metadata,
        )
    )
    with pytest.raises(LLMProviderError) as caught:
        await _observed_provider(model).decide(_decision_request(retry_budget=2))
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"
    assert caught.value.retryable is False
    assert model.invocations == ["generate"]
    assert len(model.bindings) == 1
    assert "private-provider-payload" not in "".join(traceback.format_exception(caught.value))
