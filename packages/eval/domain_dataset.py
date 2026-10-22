"""Frozen synthetic domain inputs and byte-level reproducibility identities.

This is evaluation-only data, not an application configuration/credential loader.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=12000)]
METRICS = ("no_answer_correctness", "citation_coverage", "answer_required_terms")


class DomainEvaluationError(ValueError):
    """Stable code only: input/provider bodies must never reach CLI diagnostics."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class DomainChunk(FrozenModel):
    document_id: Identifier
    version_id: Identifier
    chunk_id: Identifier
    title: NonEmptyText
    content: NonEmptyText
    lookup_terms: tuple[NonEmptyText, ...] = Field(min_length=1)


class DomainCase(FrozenModel):
    case_id: Identifier
    question: NonEmptyText
    reference_answer: NonEmptyText
    expected_no_answer: bool
    expected_chunk_ids: tuple[Identifier, ...] = ()
    required_terms: tuple[tuple[NonEmptyText, ...], ...] = ()

    @model_validator(mode="after")
    def _expectations_complete(self) -> DomainCase:
        if self.expected_no_answer:
            if self.expected_chunk_ids or self.required_terms:
                raise ValueError("No-answer cases cannot require evidence or answer terms")
        elif not self.expected_chunk_ids or not self.required_terms:
            raise ValueError("Answerable cases require citation and answer expectations")
        if any(not group for group in self.required_terms):
            raise ValueError("Term alternative groups must not be empty")
        if len(set(self.expected_chunk_ids)) != len(self.expected_chunk_ids):
            raise ValueError("Expected chunk identities must be unique")
        return self


class DomainProfile(FrozenModel):
    profile_id: Identifier
    profile_version: Identifier
    mode: Literal["offline_contract", "live_application"]
    configuration_sha256: Digest
    top_k: int = Field(ge=1, le=100)
    query_timeout_seconds: float = Field(gt=0, le=3600)
    minimum_scores: dict[str, float]
    max_baseline_drop: dict[str, float]

    @model_validator(mode="after")
    def _valid_metrics(self) -> DomainProfile:
        for values in (self.minimum_scores, self.max_baseline_drop):
            if set(values) != set(METRICS) or any(not 0 <= score <= 1 for score in values.values()):
                raise ValueError("Every supported metric needs a finite bound in [0, 1]")
        return self


class DomainManifest(FrozenModel):
    schema_version: Literal[1]
    dataset_id: Identifier
    dataset_version: Identifier
    corpus_id: Identifier
    corpus_version: Identifier
    provenance: Literal["synthetic-authored-no-customer-data"]
    files: dict[str, Digest]

    @model_validator(mode="after")
    def _locked_files(self) -> DomainManifest:
        if set(self.files) != {"corpus.json", "cases.json", "offline_profile.json"}:
            raise ValueError("Manifest must lock exactly the three domain inputs")
        return self


class DomainDataset(FrozenModel):
    manifest: DomainManifest
    manifest_sha256: Digest
    corpus: tuple[DomainChunk, ...]
    cases: tuple[DomainCase, ...]
    offline_profile: DomainProfile

    @model_validator(mode="after")
    def _consistent_inputs(self) -> DomainDataset:
        chunk_ids = [chunk.chunk_id for chunk in self.corpus]
        case_ids = [case.case_id for case in self.cases]
        if not chunk_ids or len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Corpus must have unique, non-empty chunk identities")
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("Dataset must have unique, non-empty case identities")
        if any(set(case.expected_chunk_ids) - set(chunk_ids) for case in self.cases):
            raise ValueError("Case references an unknown corpus chunk")
        if self.offline_profile.mode != "offline_contract":
            raise ValueError("Locked default profile must be an offline contract")
        if not any(case.expected_no_answer for case in self.cases):
            raise ValueError("Dataset must include no-answer cases")
        return self


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def configuration_digest(value: object) -> str:
    """Hash an operator-curated, complete, non-secret runtime configuration."""
    return sha256_bytes(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    )


def _read_json(path: Path) -> tuple[bytes, object]:
    try:
        raw = path.read_bytes()
        return raw, json.loads(raw, object_pairs_hook=_unique_keys)
    except (OSError, ValueError, UnicodeError) as exc:
        raise DomainEvaluationError("domain_input_unreadable") from exc


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key")
        result[key] = value
    return result


def load_domain_dataset(directory: Path) -> DomainDataset:
    raw, value = _read_json(directory / "manifest.json")
    try:
        manifest = DomainManifest.model_validate(value)
        values: dict[str, object] = {}
        for filename, expected_hash in manifest.files.items():
            content, parsed = _read_json(directory / filename)
            if sha256_bytes(content) != expected_hash:
                raise DomainEvaluationError("domain_input_hash_mismatch")
            values[filename] = parsed
        corpus = values["corpus.json"]
        cases = values["cases.json"]
        if not isinstance(corpus, list) or not isinstance(cases, list):
            raise DomainEvaluationError("domain_input_schema_invalid")
        return DomainDataset(
            manifest=manifest,
            manifest_sha256=sha256_bytes(raw),
            corpus=tuple(DomainChunk.model_validate(chunk) for chunk in corpus),
            cases=tuple(DomainCase.model_validate(case) for case in cases),
            offline_profile=DomainProfile.model_validate(values["offline_profile.json"]),
        )
    except ValidationError as exc:
        raise DomainEvaluationError("domain_input_schema_invalid") from exc


def load_domain_profile(path: Path) -> tuple[DomainProfile, str]:
    raw, value = _read_json(path)
    try:
        return DomainProfile.model_validate(value), sha256_bytes(raw)
    except ValidationError as exc:
        raise DomainEvaluationError("domain_profile_invalid") from exc
