"""Controlled Registry -> StructuredTool export for async ecosystem consumers.

No handler callable crosses the boundary. Even direct arun/_arun paths execute
through ToolRegistry, preserving schema, permission, rate limit, timeout and audit.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_serializer

try:
    from langchain_core.messages import ToolCall, ToolMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import StructuredTool
except ImportError:
    raise RuntimeError("Registry tool exports require the optional 'langchain' extra.") from None

from packages.agent.dto import ToolDefinition
from packages.agent.registry import ToolRegistry
from packages.common.context import AuthenticatedRequestContext
from packages.integrations.langchain_boundary import async_only, call_export


class AegisStructuredTool(StructuredTool):
    """Immutable, request-bound tool schema; execution stays in the Registry.

    Normal calls return a serialized ToolExecutionResult envelope (without its
    private prompt_context). ToolCall inputs return a ToolMessage containing the
    same envelope and an artifact, retaining safe status/metadata/citations.
    Errors remain native domain exceptions; no framework error handler runs.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    _registry: ToolRegistry = PrivateAttr()
    _context: AuthenticatedRequestContext = PrivateAttr()
    _registered_name: str = PrivateAttr()
    _agent_run_id: str = PrivateAttr()
    _gate: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    @field_serializer("args_schema", when_used="json")
    def _serialize_args_schema(self, schema: type[BaseModel]) -> dict[str, Any]:
        # ToolDefinition guarantees a Pydantic model class. Older framework
        # versions do not supply JSON serialization for this inherited field.
        return schema.model_json_schema()

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        definition: ToolDefinition,
        context: AuthenticatedRequestContext,
        agent_run_id: str,
    ) -> None:
        if not agent_run_id.strip():
            raise ValueError("agent_run_id must not be blank")
        super().__init__(
            name=definition.name,
            description=definition.description,
            args_schema=definition.input_schema,
            callbacks=None,
            verbose=False,
        )
        self._registry = registry
        self._context = context.model_copy(deep=True)
        self._registered_name = definition.name
        self._agent_run_id = agent_run_id.strip()

    def invoke(
        self,
        input: str | dict[str, Any] | ToolCall,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        async_only()

    async def ainvoke(
        self,
        input: str | dict[str, Any] | ToolCall,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        if isinstance(input, dict) and input.get("type") == "tool_call":
            if input.get("name") != self._registered_name:
                raise ValueError("ToolCall name does not match the exported tool.")
            call_id = input.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("ToolCall id must not be blank.")
            return await self.arun(input.get("args"), tool_call_id=call_id)
        return await self.arun(input)

    def run(self, tool_input: Any, *args: Any, **kwargs: Any) -> Any:
        async_only()

    async def arun(
        self, tool_input: Any, *args: Any, tool_call_id: str | None = None, **kwargs: Any
    ) -> Any:
        async def execute() -> dict[str, Any]:
            result = await self._registry.execute(
                name=self._registered_name,
                arguments=tool_input,
                context=self._context,
                agent_run_id=self._agent_run_id,
            )
            return result.model_dump(mode="json")

        # ToolNode runs calls concurrently; the Registry recorder may own one session.
        async with self._gate:
            result = await call_export(execute, request_context=self._context)
        if tool_call_id is None:
            return result
        return ToolMessage(
            content=json.dumps(result, ensure_ascii=False),
            artifact=result,
            tool_call_id=tool_call_id,
            name=self._registered_name,
            status="success" if result["status"] == "success" else "error",
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        async_only()

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            raise ValueError("Structured tool arguments must be named.")
        return await self.arun(kwargs)


async def export_registry_tools(
    *,
    registry: ToolRegistry,
    context: AuthenticatedRequestContext,
    tool_names: Sequence[str],
    agent_run_id: str,
) -> tuple[AegisStructuredTool, ...]:
    """Export an explicit allowlist, binding identity and persisted run attribution.

    Registry execution rechecks permission on every call. Exporting a schema does
    not authorize its execution, and no external callable can be supplied.
    """
    if isinstance(tool_names, str) or len(set(tool_names)) != len(tool_names):
        raise ValueError("tool_names must be an explicit, unique sequence of names")
    if not agent_run_id.strip():
        raise ValueError("agent_run_id must not be blank")

    async def build() -> tuple[AegisStructuredTool, ...]:
        definitions = [await registry.get(name=name, context=context) for name in tool_names]
        exported = tuple(
            AegisStructuredTool(
                registry=registry,
                definition=definition,
                context=context,
                agent_run_id=agent_run_id,
            )
            for definition in definitions
        )
        gate = asyncio.Lock()
        for tool in exported:
            tool._gate = gate
        return exported

    return await call_export(build, request_context=context)
