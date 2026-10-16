"""Real persisted StateGraph orchestration carrying opaque business references only.

The saver is intentionally purpose-built for this sequential graph. It accepts
JSON control channels only (no pickle, object deserialization, messages, prompts,
arguments, evidence excerpts, auth context, callbacks or arbitrary metadata).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from packages.agent.durable.dto import durable_error
from packages.agent.durable.service import _DurableExecution
from packages.common.framework_context import isolated_framework_context


class _ReferenceState(TypedDict):
    run_ref: str
    revision: int
    route: Literal["advance", "approval", "done"]


class ReferenceCheckpointSaver(BaseCheckpointSaver[int]):
    """Scoped SQL checkpoint persistence with the business lease as write fence."""

    def __init__(self, execution: _DurableExecution) -> None:
        super().__init__()
        self._execution = execution
        self._checkpoint_ready: dict[str, asyncio.Event] = {}
        self._checkpoint_failed: set[str] = set()

    def _config(self, checkpoint_id: str | None = None) -> RunnableConfig:
        values: dict[str, Any] = {
            "thread_id": self._execution.snapshot.run_id,
            "checkpoint_ns": "",
        }
        if checkpoint_id is not None:
            values["checkpoint_id"] = checkpoint_id
        return {"configurable": values}

    def _check_config(self, config: RunnableConfig) -> None:
        values = config.get("configurable", {})
        if (
            values.get("thread_id") != self._execution.snapshot.run_id
            or values.get("checkpoint_ns", "") != ""
        ):
            raise durable_error("DURABLE_GRAPH_SCOPE_INVALID")

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self._check_config(config)
        row = await self._execution.service._store.graph_get(
            self._execution.snapshot,
            config.get("configurable", {}).get("checkpoint_id"),
        )
        if row is None:
            return None
        checkpoint = self._checkpoint(row["checkpoint"])
        self._checkpoint_ready.setdefault(checkpoint["id"], asyncio.Event()).set()
        metadata = self._metadata(row["metadata"])
        writes = [
            (
                str(item["task_id"]),
                str(item["channel"]),
                self._channel(str(item["channel"]), item["value"]),
            )
            for item in sorted(row["writes"], key=lambda item: (item["task_path"], item["index"]))
        ]
        return CheckpointTuple(
            config=self._config(checkpoint["id"]),
            checkpoint=cast(Checkpoint, checkpoint),
            metadata=cast(CheckpointMetadata, metadata),
            pending_writes=writes,
            parent_config=self._config(row["parent_id"]) if row["parent_id"] else None,
        )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions
        self._check_config(config)
        ready = self._checkpoint_ready.setdefault(checkpoint["id"], asyncio.Event())
        try:
            await self._execution.service._store.graph_put(
                self._execution.snapshot,
                token=self._execution.token,
                now=self._execution.service._clock(),
                checkpoint=self._checkpoint(dict(checkpoint)),
                metadata=self._metadata(dict(metadata)),
                parent_id=config.get("configurable", {}).get("checkpoint_id"),
            )
        except BaseException:
            self._checkpoint_failed.add(checkpoint["id"])
            raise
        finally:
            ready.set()
        return self._config(checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._check_config(config)
        entries: list[dict[str, Any]] = []
        for index, (channel, value) in enumerate(writes):
            # Exception strings are potentially sensitive. The authoritative
            # business state is sufficient to recover; never store these strings.
            if channel in ("__error__", "__interrupt__"):
                continue
            entries.append(
                {
                    "task_id": task_id,
                    "channel": channel,
                    "value": self._channel(channel, value),
                    "task_path": task_path,
                    "index": WRITES_IDX_MAP.get(channel, index),
                }
            )
        if entries:
            checkpoint_id = config["configurable"]["checkpoint_id"]
            # Pregel schedules checkpoint insertion and task writes separately,
            # even with sync durability. Our normalized row owns its writes, so
            # wait for that insert rather than assuming scheduler ordering. The
            # enclosing execution deadline also bounds this wait.
            await self._checkpoint_ready.setdefault(checkpoint_id, asyncio.Event()).wait()
            if checkpoint_id in self._checkpoint_failed:
                raise durable_error("DURABLE_GRAPH_CHECKPOINT_FAILED")
            await self._execution.service._store.graph_writes(
                self._execution.snapshot,
                token=self._execution.token,
                now=self._execution.service._clock(),
                checkpoint_id=checkpoint_id,
                writes=entries,
            )

    def _channel(self, channel: str, value: Any) -> Any:
        if channel == "run_ref" and value == self._execution.snapshot.run_id:
            return value
        if channel == "revision" and type(value) is int and 0 <= value <= 1000:
            return value
        if channel == "route" and value in ("advance", "approval", "done"):
            return value
        if channel == "__start__" and isinstance(value, dict):
            if set(value) == {"run_ref", "revision", "route"}:
                return {key: self._channel(key, item) for key, item in value.items()}
        if channel in ("branch:to:advance", "branch:to:approval") and value is None:
            return None
        raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")

    def _checkpoint(self, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "v",
            "id",
            "ts",
            "channel_values",
            "channel_versions",
            "versions_seen",
            "pending_sends",
            "updated_channels",
        }
        # The locked Pregel engine writes v4. checkpoint.base's legacy helper
        # advertises v2, but that is not the runtime's checkpoint format.
        required = allowed - {"pending_sends", "updated_channels"}
        if set(value) - allowed or required - set(value) or value.get("v") != 4:
            raise durable_error("DURABLE_GRAPH_SCHEMA_UNSUPPORTED")
        if value.get("pending_sends") not in (None, []):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        try:
            checkpoint_id = value["id"]
            timestamp = value["ts"]
            if (
                not isinstance(checkpoint_id, str)
                or str(UUID(checkpoint_id)) != checkpoint_id
                or not isinstance(timestamp, str)
                or len(timestamp) > 64
                or datetime.fromisoformat(timestamp).utcoffset() is None
            ):
                raise ValueError("invalid checkpoint identity")
        except (TypeError, ValueError, AttributeError):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE") from None
        if any(
            not isinstance(value[name], dict)
            for name in ("channel_values", "channel_versions", "versions_seen")
        ):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        result = dict(value)
        result["channel_values"] = {
            channel: self._channel(channel, item)
            for channel, item in value["channel_values"].items()
        }
        # Framework bookkeeping is bounded JSON primitives; no framework serde
        # is called for stored state. Only our fixed graph's nodes/channels exist.
        known_channels = {
            "run_ref",
            "revision",
            "route",
            "__start__",
            "branch:to:advance",
            "branch:to:approval",
        }
        def safe_versions(versions: object) -> bool:
            return (
                isinstance(versions, dict)
                and not (set(versions) - known_channels)
                and all(type(item) is int and 0 <= item <= 1000 for item in versions.values())
            )

        if not safe_versions(value["channel_versions"]):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        for node, versions in value["versions_seen"].items():
            if node not in ("__input__", "__start__", "advance", "approval", "__interrupt__"):
                raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
            if not safe_versions(versions):
                raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        updated = value.get("updated_channels")
        if updated is not None and (
            not isinstance(updated, (list, tuple))
            or len(updated) > len(known_channels)
            or any(
                not isinstance(channel, str) or channel not in known_channels for channel in updated
            )
        ):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded) > 32768:
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        return cast(dict[str, Any], json.loads(encoded))

    @staticmethod
    def _metadata(value: dict[str, Any]) -> dict[str, Any]:
        # Do not call get_checkpoint_metadata: arbitrary runnable config metadata
        # must not enter persistent framework state.
        source = value.get("source")
        step = value.get("step")
        if (
            source not in ("input", "loop", "update", "fork")
            or type(step) is not int
            or not -2 <= step <= 1000
        ):
            raise durable_error("DURABLE_GRAPH_STATE_UNSAFE")
        return {"source": source, "step": step, "parents": {}}


async def run_durable_graph(execution: _DurableExecution) -> None:
    def reference() -> _ReferenceState:
        status = execution.snapshot.status
        return {
            "run_ref": execution.snapshot.run_id,
            "revision": execution.snapshot.revision,
            "route": (
                "advance"
                if status == "running"
                else "approval"
                if status == "awaiting_approval"
                else "done"
            ),
        }

    async def advance(state: _ReferenceState) -> _ReferenceState:
        if state["run_ref"] != execution.snapshot.run_id:
            raise durable_error("DURABLE_GRAPH_SCOPE_INVALID")
        await execution.advance()
        return reference()

    async def approval(state: _ReferenceState) -> _ReferenceState:
        if state["run_ref"] != execution.snapshot.run_id:
            raise durable_error("DURABLE_GRAPH_SCOPE_INVALID")
        # Approval itself was committed under the run CAS before resuming the
        # framework interrupt. Never let a framework resume value authorize it.
        execution.snapshot = await execution.service._store.load(
            tenant_id=execution.context.auth.tenant_id,
            user_id=execution.context.auth.user_id,
            run_id=execution.snapshot.run_id,
        )
        if execution.snapshot.status == "awaiting_approval":
            raise durable_error("DURABLE_APPROVAL_REQUIRED")
        return reference()

    builder = StateGraph(_ReferenceState)
    builder.add_node("advance", advance)
    builder.add_node("approval", approval)
    builder.add_edge(START, "advance")
    for node in ("advance", "approval"):
        builder.add_conditional_edges(
            node,
            lambda state: state["route"],
            {"advance": "advance", "approval": "approval", "done": END},
        )
    graph = builder.compile(
        checkpointer=ReferenceCheckpointSaver(execution), interrupt_before=["approval"]
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": execution.snapshot.run_id},
        "callbacks": [],
        "recursion_limit": 4 * execution.snapshot.config.max_steps + 12,
    }

    async def invoke() -> None:
        with tracing_context(enabled=False):
            state = await graph.aget_state(config)
            await graph.ainvoke(
                None if state.next else reference(), config=config, durability="sync"
            )

    await asyncio.create_task(invoke(), context=isolated_framework_context())
