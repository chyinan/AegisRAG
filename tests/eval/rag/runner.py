from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from time import perf_counter

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import ChunkRecord
from packages.llm.dto import (
    GenerateChunk,
    GenerateRequest,
    GenerateResponse,
    GenerationMetadata,
    TokenUsage,
)
from packages.llm.exceptions import LLM_PROVIDER_FAILED, LLMProviderError
from packages.rag.citation_extractor import CitationExtractor
from packages.rag.context_packer import ContextPacker
from packages.rag.dto import Citation, QueryCommand, QueryResponse
from packages.rag.generation import RagGenerationService
from packages.rag.hydration import RetrievalCandidateHydrator
from packages.rag.prompt_builder import PromptBuilder
from packages.rag.query import RagQueryApplicationService
from packages.retrieval.dto import (
    MAX_RETRIEVAL_TOP_K,
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
)
from packages.retrieval.ports import CandidateRetriever
from packages.retrieval.service import RetrievalService
from tests.eval.rag.dto import (
    AttackType,
    ExpectedAnswerPolicy,
    ExpectedCitation,
    FailureStage,
    RagEvalCase,
    RagEvalCaseResult,
    RagEvalCorpusRecord,
    RagEvalGenerationSummary,
    RagEvalReport,
    RagEvalReportSummary,
)
from tests.eval.rag.loader import RagEvalDatasetError
from tests.eval.rag.reporting import build_rag_eval_report, write_rag_eval_report

_CITATION_ID_PATTERN = re.compile(r'id="(cite-[A-Za-z0-9_-]+)"')


class FixtureRagCandidateRetriever:
    def __init__(self, corpus: Sequence[RagEvalCorpusRecord]) -> None:
        self._corpus = tuple(corpus)
        self.calls = 0

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        self.calls += 1
        _ = filters
        case_id = request.request_id.removeprefix("eval-")
        candidates = [
            _candidate_from_record(record)
            for record in self._corpus
            if case_id in record.relevant_case_ids
        ]
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates


class FixtureChunkRepository:
    def __init__(self, corpus: Sequence[RagEvalCorpusRecord]) -> None:
        self._records = {
            (record.tenant_id, record.document_id, record.version_id, record.chunk_id): record
            for record in corpus
        }

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        if document_id is None or version_id is None:
            return None
        record = self._records.get((tenant_id, document_id, version_id, chunk_id))
        if record is None:
            return None
        return ChunkRecord(
            tenant_id=record.tenant_id,
            document_id=record.document_id,
            version_id=record.version_id,
            chunk_id=record.chunk_id,
            created_by="eval-fixture",
            status="active",
            source_type=record.source_type,
            source_uri=record.source_uri,
            title_path=list(record.title_path),
            content=record.content,
            page_start=record.page_start,
            page_end=record.page_end,
            token_count=record.token_count,
            acl=record.acl.model_dump(mode="json"),
            checksum=f"checksum-{record.chunk_id}",
            section_ids=[f"section-{record.chunk_id}"],
            metadata=dict(record.metadata),
        )


class RagEvalFakeLLMProvider:
    def __init__(
        self,
        *,
        cases: Sequence[RagEvalCase] = (),
        forged_case_ids: Sequence[str] = (),
        citation_miss_case_ids: Sequence[str] = (),
        failure_case_ids: Sequence[str] = (),
        provider: str = "fake",
        model: str = "fake-llm",
        version: str = "fake-v1",
    ) -> None:
        self._case_by_id = {case.case_id: case for case in cases}
        self._forged_case_ids = set(forged_case_ids)
        self._citation_miss_case_ids = set(citation_miss_case_ids)
        self._failure_case_ids = set(failure_case_ids)
        self._provider = provider
        self._model = model
        self._version = version

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        case_id = request.request_id.removeprefix("eval-")
        if case_id in self._failure_case_ids:
            raise LLMProviderError(
                code=LLM_PROVIDER_FAILED,
                message="Fake RAG eval generation failed.",
                retryable=False,
                details={
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "provider": self._provider,
                    "model": self._model,
                    "version": self._version,
                },
                status_code=502,
            )
        answer = self._answer_for(request=request, case_id=case_id)
        usage = TokenUsage(
            input_tokens=sum(len(message.content.split()) for message in request.messages),
            output_tokens=len(answer.split()),
            total_tokens=sum(len(message.content.split()) for message in request.messages)
            + len(answer.split()),
        )
        metadata = GenerationMetadata(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=0.0,
            finish_reason="stop",
            error_code=None,
            token_count=usage.output_tokens,
            metadata={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
        )
        return GenerateResponse(
            text=answer,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=0.0,
            finish_reason="stop",
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            error_code=None,
            metadata=metadata,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        response = await self.generate(request)
        yield GenerateChunk(
            delta="",
            index=0,
            is_final=True,
            response=response,
        )

    def _answer_for(self, *, request: GenerateRequest, case_id: str) -> str:
        if case_id in self._forged_case_ids:
            return "Local eval answer with forged reference cite-forged-source."
        citation_ids = _citation_ids_from_request(request)
        if case_id in self._citation_miss_case_ids:
            return "Local eval answer with unsupported reference doc-forged."
        if not citation_ids:
            return "Local eval answer without source markers."
        case = self._case_by_id.get(case_id)
        expected_terms = case.expected_answer.must_include_terms if case is not None else ()
        term_text = " ".join(expected_terms).strip()
        citation_text = " ".join(citation_ids)
        if term_text:
            return f"Local eval answer: {term_text}. Evidence: {citation_text}."
        return f"Local eval answer supported by context. Evidence: {citation_text}."


async def run_rag_eval(
    cases: Sequence[RagEvalCase],
    corpus: Sequence[RagEvalCorpusRecord],
    *,
    report_dir: Path | None = None,
    report_path: Path | None = None,
    top_k: int | None = None,
    provider: RagEvalFakeLLMProvider | None = None,
    counter: Callable[[], float] = perf_counter,
) -> RagEvalReport:
    if not cases:
        raise RagEvalDatasetError(code="empty_case_set", details={"case_count": 0})
    if top_k is not None:
        _validate_top_k_override(top_k)

    retriever = FixtureRagCandidateRetriever(corpus)
    service = _query_service(
        cases=cases,
        corpus=corpus,
        retriever=retriever,
        provider=provider,
    )
    results: list[RagEvalCaseResult] = []
    for case in cases:
        request_top_k = top_k if top_k is not None else case.top_k
        started = counter()
        try:
            response = await service.query(
                context=_request_context(case),
                command=QueryCommand(
                    query=case.query,
                    top_k=request_top_k,
                    metadata_filter=dict(case.metadata_filter),
                ),
            )
        except DomainError as exc:
            latency_ms = max((counter() - started) * 1000, 0.0)
            results.append(_failed_case_result(case, request_top_k, latency_ms, exc))
            continue
        latency_ms = max((counter() - started) * 1000, 0.0)
        results.append(evaluate_rag_case(case, response, latency_ms=latency_ms))

    case_results = tuple(results)
    summary = build_rag_eval_summary(cases=tuple(cases), results=case_results)
    report = build_rag_eval_report(results=case_results, summary=summary)
    if report_dir is not None or report_path is not None:
        write_rag_eval_report(report, report_dir=report_dir, report_path=report_path)
    return report


def evaluate_rag_case(
    case: RagEvalCase,
    response: QueryResponse,
    *,
    latency_ms: float | None = None,
) -> RagEvalCaseResult:
    expected_documents = set(case.expected_documents)
    expected_chunks = set(case.expected_chunks)
    required_citations = tuple(
        citation for citation in case.expected_citations if citation.required
    )
    required_citation_ids = {_citation_key(citation) for citation in required_citations}
    citation_ids = {_citation_key(citation) for citation in response.citations}
    matched_citations = tuple(sorted(citation_ids & required_citation_ids))
    matched_documents = tuple(
        sorted(
            citation.document_id
            for citation in response.citations
            if citation.document_id in expected_documents
        )
    )
    matched_chunks = tuple(
        sorted(
            citation.chunk_id
            for citation in response.citations
            if citation.chunk_id in expected_chunks
        )
    )

    metadata = _response_metadata(response)
    retrieval_result_count = _nested_int(metadata, "retrieval", "result_count")
    citation_count = len(response.citations)
    unsupported_count = len(response.unsupported_claims)
    forged_reference_count = _nested_int(metadata, "citation", "forged_reference_count")
    prompt_risk_count = _nested_int(metadata, "prompt_risk", "detected_risk_count")

    failure_stage = _failure_stage(
        case=case,
        response=response,
        retrieval_result_count=retrieval_result_count,
        matched_document_count=len(matched_documents),
        matched_chunk_count=len(matched_chunks),
        required_citation_count=len(required_citations),
        matched_required_citation_count=len(matched_citations),
        forged_reference_count=forged_reference_count,
        unsupported_count=unsupported_count,
    )
    return RagEvalCaseResult(
        case_id=case.case_id,
        request_id=response.request_id,
        trace_id=response.trace_id,
        tenant_id=response.tenant_id,
        user_id=response.user_id,
        top_k=int(_nested_int(metadata, "retrieval", "top_k") or case.top_k),
        latency_ms=(
            latency_ms if latency_ms is not None else _float_value(metadata.get("latency_ms"))
        ),
        passed=failure_stage is None,
        failure_stage=failure_stage,
        matched_documents=tuple(dict.fromkeys(matched_documents)),
        matched_chunks=tuple(dict.fromkeys(matched_chunks)),
        matched_citations=matched_citations,
        retrieval_result_count=retrieval_result_count,
        context_item_count=_nested_int(metadata, "context", "item_count"),
        citation_count=citation_count,
        unsupported_count=unsupported_count,
        forged_reference_count=forged_reference_count,
        prompt_risk_count=prompt_risk_count,
        generation=_generation_summary(metadata),
    )


def build_rag_eval_summary(
    *,
    cases: tuple[RagEvalCase, ...],
    results: tuple[RagEvalCaseResult, ...],
) -> RagEvalReportSummary:
    result_by_case_id = {result.case_id: result for result in results}
    case_ids = {case.case_id for case in cases}
    result_ids = set(result_by_case_id)
    if case_ids != result_ids:
        raise RagEvalDatasetError(
            code="result_case_mismatch",
            details={
                "case_count": len(cases),
                "result_count": len(results),
                "missing_count": len(case_ids - result_ids),
                "extra_count": len(result_ids - case_ids),
            },
        )
    passed_count = sum(1 for result in results if result.passed)
    answerable_cases = tuple(case for case in cases if case.answerable)
    retrieval_hits = sum(
        1
        for case in answerable_cases
        if result_by_case_id[case.case_id].matched_documents
        or result_by_case_id[case.case_id].matched_chunks
    )
    required_citation_count = sum(
        1 for case in cases for citation in case.expected_citations if citation.required
    )
    matched_required_citation_count = sum(
        len(result_by_case_id[case.case_id].matched_citations) for case in cases
    )
    no_answer_cases = tuple(case for case in cases if case.expected_no_answer)
    no_answer_passed = sum(1 for case in no_answer_cases if result_by_case_id[case.case_id].passed)
    return RagEvalReportSummary(
        case_count=len(results),
        passed_count=passed_count,
        failed_count=len(results) - passed_count,
        retrieval_hit_rate=retrieval_hits / len(answerable_cases) if answerable_cases else 1.0,
        citation_coverage=(
            matched_required_citation_count / required_citation_count
            if required_citation_count
            else 1.0
        ),
        required_citation_count=required_citation_count,
        matched_required_citation_count=matched_required_citation_count,
        no_answer_correctness=no_answer_passed / len(no_answer_cases) if no_answer_cases else 1.0,
        no_answer_case_count=len(no_answer_cases),
        acl_isolation_passed=_all_attack_cases_passed(
            "acl_isolation",
            cases,
            result_by_case_id,
        ),
        prompt_injection_passed=_all_attack_cases_passed(
            "prompt_injection",
            cases,
            result_by_case_id,
        ),
        average_latency_ms=(
            sum(result.latency_ms for result in results) / len(results) if results else 0.0
        ),
    )


def _query_service(
    *,
    cases: Sequence[RagEvalCase],
    corpus: Sequence[RagEvalCorpusRecord],
    retriever: CandidateRetriever,
    provider: RagEvalFakeLLMProvider | None,
) -> RagQueryApplicationService:
    return RagQueryApplicationService(
        retrieval_service=RetrievalService(retriever=retriever),
        hydrator=RetrievalCandidateHydrator(repository=FixtureChunkRepository(corpus)),
        context_packer=ContextPacker(),
        prompt_builder=PromptBuilder(),
        generation_service=RagGenerationService(
            provider=provider or RagEvalFakeLLMProvider(cases=cases),
            provider_name="fake",
            model="fake-llm",
        ),
        citation_extractor=CitationExtractor(),
        audit=InMemoryAuditPort(),
    )


def _request_context(case: RagEvalCase) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id=f"eval-{case.case_id}",
        trace_id=f"trace-{case.case_id}",
        auth=AuthContext(
            user_id=case.user_id,
            tenant_id=case.tenant_id,
            roles=case.roles,
            department=case.department,
            permissions=case.permissions,
        ),
    )


def _candidate_from_record(record: RagEvalCorpusRecord) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=record.document_id,
        version_id=record.version_id,
        chunk_id=record.chunk_id,
        source=record.source,
        source_type=record.source_type,
        source_uri=record.source_uri,
        page_start=record.page_start,
        page_end=record.page_end,
        title_path=record.title_path,
        score=record.score,
        retrieval_method=record.retrieval_method,
        tenant_id=record.tenant_id,
        acl=record.acl.model_dump(mode="json"),
        metadata=record.metadata,
    )


def _validate_top_k_override(top_k: int) -> None:
    if isinstance(top_k, bool) or top_k <= 0 or top_k > MAX_RETRIEVAL_TOP_K:
        raise RagEvalDatasetError(
            code="invalid_top_k_override",
            details={"top_k": top_k, "max_top_k": MAX_RETRIEVAL_TOP_K},
        )


def _failure_stage(
    *,
    case: RagEvalCase,
    response: QueryResponse,
    retrieval_result_count: int,
    matched_document_count: int,
    matched_chunk_count: int,
    required_citation_count: int,
    matched_required_citation_count: int,
    forged_reference_count: int,
    unsupported_count: int,
) -> FailureStage | None:
    if case.expected_no_answer:
        if (
            response.no_answer
            and not response.citations
            and unsupported_count == 0
            and forged_reference_count == 0
        ):
            return None
        return "no_answer"
    if retrieval_result_count <= 0:
        return "retrieval"
    if response.no_answer:
        return "no_answer"
    if forged_reference_count > 0 or unsupported_count > 0:
        return "citation"
    if _missing_expected_retrieval_hit(
        case=case,
        matched_document_count=matched_document_count,
        matched_chunk_count=matched_chunk_count,
    ):
        return "retrieval"
    if _has_out_of_scope_citation(case, response):
        return "permission"
    if required_citation_count and matched_required_citation_count < required_citation_count:
        return "citation"
    policy_failure = _expected_answer_policy_failure(
        policy=case.expected_answer,
        answer=response.answer,
    )
    if policy_failure:
        return "prompt_build" if case.attack_type == "prompt_injection" else "generation"
    return None


def _missing_expected_retrieval_hit(
    *,
    case: RagEvalCase,
    matched_document_count: int,
    matched_chunk_count: int,
) -> bool:
    if case.expected_documents and matched_document_count == 0:
        return True
    return bool(case.expected_chunks and matched_chunk_count == 0)


def _has_out_of_scope_citation(case: RagEvalCase, response: QueryResponse) -> bool:
    expected = {
        (citation.document_id, citation.version_id, citation.chunk_id)
        for citation in case.expected_citations
    }
    expected_documents = set(case.expected_documents)
    expected_chunks = set(case.expected_chunks)
    if not expected:
        return any(
            citation.document_id not in expected_documents
            and citation.chunk_id not in expected_chunks
            for citation in response.citations
        )
    return any(
        (citation.document_id, citation.version_id, citation.chunk_id) not in expected
        for citation in response.citations
    )


def _failed_case_result(
    case: RagEvalCase,
    top_k: int,
    latency_ms: float,
    exc: DomainError,
) -> RagEvalCaseResult:
    return RagEvalCaseResult(
        case_id=case.case_id,
        request_id=f"eval-{case.case_id}",
        trace_id=f"trace-{case.case_id}",
        tenant_id=case.tenant_id,
        user_id=case.user_id,
        top_k=top_k,
        latency_ms=latency_ms,
        passed=False,
        failure_stage=_failure_stage_from_error(exc),
        generation=RagEvalGenerationSummary(error_code=exc.code),
    )


def _failure_stage_from_error(exc: DomainError) -> FailureStage:
    explicit_stage = exc.details.get("stage")
    if explicit_stage in {
        "retrieval",
        "rerank",
        "context_packing",
        "prompt_build",
        "generation",
        "citation",
        "permission",
        "no_answer",
        "dataset",
        "runner",
    }:
        return explicit_stage  # type: ignore[return-value]
    if "FORBIDDEN" in exc.code or "AUTH" in exc.code:
        return "permission"
    if exc.code.startswith("RETRIEVAL_"):
        return "retrieval"
    if exc.code.startswith("RAG_CONTEXT_"):
        return "context_packing"
    if exc.code.startswith("RAG_PROMPT_"):
        return "prompt_build"
    if exc.code.startswith(("RAG_GENERATION_", "LLM_")):
        return "generation"
    if exc.code.startswith("RAG_CITATION_"):
        return "citation"
    return "runner"


def _all_attack_cases_passed(
    attack_type: AttackType,
    cases: tuple[RagEvalCase, ...],
    result_by_case_id: Mapping[str, RagEvalCaseResult],
) -> bool:
    matching = [case for case in cases if case.attack_type == attack_type]
    return bool(matching) and all(result_by_case_id[case.case_id].passed for case in matching)


def _expected_answer_policy_failure(
    *,
    policy: ExpectedAnswerPolicy,
    answer: str,
) -> bool:
    normalized_answer = answer.casefold()
    return any(
        term.casefold() not in normalized_answer for term in policy.must_include_terms
    ) or any(term.casefold() in normalized_answer for term in policy.must_not_include_terms)


def _citation_ids_from_request(request: GenerateRequest) -> tuple[str, ...]:
    ids: list[str] = []
    for message in request.messages:
        ids.extend(_CITATION_ID_PATTERN.findall(message.content))
    return tuple(dict.fromkeys(ids))


def _citation_key(citation: ExpectedCitation | Citation) -> str:
    return f"{citation.document_id}:{citation.version_id}:{citation.chunk_id}"


def _response_metadata(response: QueryResponse) -> Mapping[str, object]:
    return response.metadata if isinstance(response.metadata, Mapping) else {}


def _nested_int(metadata: Mapping[str, object], parent_key: str, child_key: str) -> int:
    parent = metadata.get(parent_key)
    if not isinstance(parent, Mapping):
        return 0
    value = parent.get(child_key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _generation_summary(metadata: Mapping[str, object]) -> RagEvalGenerationSummary:
    raw = metadata.get("generation")
    if not isinstance(raw, Mapping):
        return RagEvalGenerationSummary()
    token_usage = raw.get("token_usage")
    safe_usage: dict[str, int] | None = None
    if isinstance(token_usage, Mapping):
        safe_usage = {
            key: int(value)
            for key, value in token_usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
    return RagEvalGenerationSummary(
        provider=_optional_str(raw.get("provider")),
        model=_optional_str(raw.get("model")),
        version=_optional_str(raw.get("version")),
        token_usage=safe_usage,
        finish_reason=_optional_str(raw.get("finish_reason")),
        error_code=_optional_str(raw.get("error_code")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _float_value(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0
