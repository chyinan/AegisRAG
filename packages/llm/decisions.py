"""Provider-neutral, declarative one-step tool decisions.

These DTOs carry JSON schemas and values only. They never contain executable
handlers, framework messages, callbacks, or an agent loop.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from packages.llm.dto import GenerateRequest, GenerationMetadata

FINAL_ANSWER_TOOL = "aegis_final_answer"


class DecisionToolSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1)
    parameters: dict[str, JsonValue]

    @field_validator("parameters")
    @classmethod
    def _object_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _finite_json(value)
        if value.get("type") != "object":
            raise ValueError("tool parameters must be an object JSON schema")
        return value


class DecisionRequest(GenerateRequest):
    """Exactly one tool invocation or a schema-shaped final answer per call."""

    tools: tuple[DecisionToolSchema, ...] = ()
    final_answer_schema: dict[str, JsonValue]

    @field_validator("final_answer_schema")
    @classmethod
    def _object_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return DecisionToolSchema._object_schema(value)

    @model_validator(mode="after")
    def _unique_tool_names(self) -> DecisionRequest:
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names) or FINAL_ANSWER_TOOL in names:
            raise ValueError("tool names must be unique and must not use the final-answer name")
        return self


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["tool_call", "final_answer"]
    tool_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    final_answer: dict[str, JsonValue] | None = Field(default=None, repr=False)
    metadata: GenerationMetadata

    @field_validator("arguments", "final_answer")
    @classmethod
    def _json_payload(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        if value is not None:
            _finite_json(value)
        return value

    @model_validator(mode="after")
    def _exclusive_decision(self) -> DecisionResponse:
        if self.action == "tool_call":
            if self.tool_name is None or self.final_answer is not None:
                raise ValueError("tool decisions require a name and no final answer")
        elif self.tool_name is not None or self.arguments or self.final_answer is None:
            raise ValueError("final decisions require only a final-answer payload")
        return self


def _finite_json(value: object) -> None:
    # JsonValue rejects framework objects; JSON also excludes NaN and infinity.
    json.dumps(value, allow_nan=False)
