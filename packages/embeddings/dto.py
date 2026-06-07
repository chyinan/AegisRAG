from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    texts: list[str]
    provider: str
    model: str
    timeout_seconds: float
    retry_budget: int
    rate_limit_key: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    chunk_ids: list[str] | None = None

    @field_validator("provider", "model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("texts")
    @classmethod
    def _texts_required(cls, value: list[str]) -> list[str]:
        normalized = [text for text in value if text.strip()]
        if len(normalized) != len(value) or not normalized:
            raise ValueError("texts must contain non-blank text values")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return value

    @field_validator("retry_budget")
    @classmethod
    def _retry_budget_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry_budget must not be negative")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return dict(value)

    @field_validator("chunk_ids")
    @classmethod
    def _chunk_ids_non_blank(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(value):
            raise ValueError("chunk_ids must not contain blank values")
        return normalized

    @model_validator(mode="after")
    def _chunk_ids_match_text_count(self) -> EmbeddingRequest:
        if self.chunk_ids is not None and len(self.chunk_ids) != len(self.texts):
            raise ValueError("chunk_ids length must match texts length")
        return self


class EmbeddingVector(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    vector: list[float]
    chunk_id: str | None = None

    @field_validator("index")
    @classmethod
    def _index_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("index must not be negative")
        return value

    @field_validator("chunk_id")
    @classmethod
    def _optional_chunk_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    vectors: list[EmbeddingVector]
    provider: str
    model: str
    version: str | None = None
    dim: int
    usage: dict[str, object] = Field(default_factory=dict)
    latency_ms: float

    @field_validator("provider", "model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("dim")
    @classmethod
    def _dim_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("dim must be greater than 0")
        return value

    @field_validator("latency_ms")
    @classmethod
    def _latency_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("latency_ms must not be negative")
        return value

    @field_validator("usage", mode="before")
    @classmethod
    def _usage_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("usage must be a mapping")
        return dict(value)
