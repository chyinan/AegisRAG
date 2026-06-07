from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import cast

from packages.auth.context import AuthContext
from packages.auth.policies import build_access_filter
from packages.common.logging import redact_mapping
from packages.rag.dto import (
    ContextCandidate,
    ContextDroppedCandidate,
    ContextPackingConfig,
    ContextPackingTrace,
    PackedCitationSource,
    PackedContext,
    PackedContextItem,
)
from packages.rag.exceptions import (
    RAG_CONTEXT_BUDGET_EXCEEDED,
    RAG_CONTEXT_UNAUTHORIZED_CHUNK,
    RagContextPackingError,
)
from packages.vectorstores.acl import acl_allows
from packages.vectorstores.dto import AclFilter

PRIMARY_CONTEXT_REASON = "retrieval_candidate"
PARENT_CONTEXT_REASON = "parent_context"
CHILD_CONTEXT_REASON = "child_context"
NEIGHBOR_CONTEXT_REASON = "neighbor_context"
RELATED_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class _SelectedCandidate:
    candidate: ContextCandidate
    reason: str
    order: int


class ContextPacker:
    def pack(
        self,
        *,
        candidates: Sequence[object],
        auth: AuthContext,
        config: ContextPackingConfig | None = None,
        related_chunks_by_id: Mapping[str, ContextCandidate] | None = None,
        request_id: str,
        trace_id: str,
    ) -> PackedContext:
        packing_config = config or ContextPackingConfig()
        access_filter = build_access_filter(auth)
        acl_filter = AclFilter(
            user_id=access_filter.user_id,
            roles=list(access_filter.roles),
            department=access_filter.department,
            permissions=list(access_filter.permissions),
        )
        related_chunks = related_chunks_by_id or {}
        dropped: list[ContextDroppedCandidate] = []
        selected: list[_SelectedCandidate] = []
        selected_identities: set[tuple[str, str, str, str]] = set()
        related_counts: Counter[str] = Counter()
        related_trace_items: list[dict[str, object]] = []
        total_tokens = 0
        authorized_count = 0

        valid_candidates: list[ContextCandidate] = []
        for candidate in candidates:
            if not isinstance(candidate, ContextCandidate):
                dropped.append(_drop(candidate, reason="invalid_candidate"))
                continue
            valid_candidates.append(candidate)

        sorted_candidates = sorted(
            valid_candidates,
            key=lambda candidate: (
                -_candidate_score(candidate),
                *_candidate_identity(candidate)[1:],
            ),
        )

        for candidate in sorted_candidates:
            invalid_reason = self._invalid_reason(candidate)
            if invalid_reason is not None:
                dropped.append(_drop(candidate, reason="invalid_candidate"))
                continue

            self._authorize_or_raise(
                candidate=candidate,
                auth=auth,
                acl_filter=acl_filter,
                request_id=request_id,
                trace_id=trace_id,
                input_count=len(candidates),
            )
            authorized_count += 1

            identity = _candidate_identity(candidate)
            if identity in selected_identities:
                dropped.append(_drop(candidate, reason="duplicate"))
                continue

            if candidate.token_count > packing_config.max_tokens:
                self._handle_oversized_candidate(
                    candidate=candidate,
                    config=packing_config,
                    request_id=request_id,
                    trace_id=trace_id,
                    auth=auth,
                    input_count=len(candidates),
                )
                dropped.append(_drop(candidate, reason="oversized"))
                continue

            if total_tokens + candidate.token_count > packing_config.max_tokens:
                dropped.append(_drop(candidate, reason="budget_exceeded"))
                continue

            order = len(selected)
            selected.append(
                _SelectedCandidate(
                    candidate=candidate,
                    reason=PRIMARY_CONTEXT_REASON,
                    order=order,
                )
            )
            selected_identities.add(identity)
            total_tokens += candidate.token_count

            related_attempts = 0
            for related_id, reason in self._related_chunk_requests(candidate, packing_config):
                if related_attempts >= packing_config.max_related_chunks_per_candidate:
                    break
                related_attempts += 1
                if not _is_safe_identifier(related_id):
                    dropped.append(
                        ContextDroppedCandidate(
                            reason="invalid_related_id",
                            document_id=candidate.document_id,
                            version_id=candidate.version_id,
                            chunk_id=_safe_diagnostic_id(related_id),
                            tenant_id=candidate.tenant_id,
                            related_reason=reason,
                        )
                    )
                    continue
                related_candidate = related_chunks.get(related_id)
                if related_candidate is None:
                    dropped.append(
                        ContextDroppedCandidate(
                            reason="missing_related",
                            document_id=candidate.document_id,
                            version_id=candidate.version_id,
                            chunk_id=_safe_diagnostic_id(related_id),
                            tenant_id=candidate.tenant_id,
                            related_reason=reason,
                        )
                    )
                    continue

                invalid_related_reason = self._invalid_reason(related_candidate)
                if invalid_related_reason is not None:
                    dropped.append(
                        _drop(
                            related_candidate,
                            reason="invalid_candidate",
                            related_reason=reason,
                        )
                    )
                    continue

                self._authorize_or_raise(
                    candidate=related_candidate,
                    auth=auth,
                    acl_filter=acl_filter,
                    request_id=request_id,
                    trace_id=trace_id,
                    input_count=len(candidates),
                )
                authorized_count += 1
                invalid_relation_reason = _invalid_related_reason(
                    primary=candidate,
                    related=related_candidate,
                    requested_id=related_id,
                    reason=reason,
                )
                if invalid_relation_reason is not None:
                    dropped.append(
                        _drop(
                            related_candidate,
                            reason=invalid_relation_reason,
                            related_reason=reason,
                        )
                    )
                    continue
                related_identity = _candidate_identity(related_candidate)
                if related_identity in selected_identities:
                    dropped.append(
                        _drop(related_candidate, reason="duplicate", related_reason=reason)
                    )
                    continue
                if related_candidate.token_count > packing_config.max_tokens:
                    self._handle_oversized_candidate(
                        candidate=related_candidate,
                        config=packing_config,
                        request_id=request_id,
                        trace_id=trace_id,
                        auth=auth,
                        input_count=len(candidates),
                    )
                    dropped.append(
                        _drop(related_candidate, reason="oversized", related_reason=reason)
                    )
                    continue
                if total_tokens + related_candidate.token_count > packing_config.max_tokens:
                    dropped.append(
                        _drop(related_candidate, reason="budget_exceeded", related_reason=reason)
                    )
                    continue

                selected.append(
                    _SelectedCandidate(
                        candidate=related_candidate,
                        reason=reason,
                        order=len(selected),
                    )
                )
                selected_identities.add(related_identity)
                total_tokens += related_candidate.token_count
                related_counts[reason] += 1
                related_trace_items.append(
                    {
                        "document_id": related_candidate.document_id,
                        "version_id": related_candidate.version_id,
                        "chunk_id": related_candidate.chunk_id,
                        "reason": reason,
                        "source_chunk_id": candidate.chunk_id,
                        "token_count": related_candidate.token_count,
                        "score": related_candidate.score,
                    }
                )

        items, merged_groups = self._build_items(
            selected=selected,
            merge_adjacent=packing_config.merge_adjacent,
        )
        drop_reasons = Counter(item.reason for item in dropped)
        trace = ContextPackingTrace(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            input_count=len(candidates),
            authorized_count=authorized_count,
            packed_count=len(items),
            dropped_count=len(dropped),
            total_tokens=total_tokens,
            budget=packing_config.max_tokens,
            drop_reasons=dict(drop_reasons),
            merged_groups=tuple(merged_groups),
            related_context_items=tuple(related_trace_items),
            related_context_counts=dict(related_counts),
            safe_counts={
                "input_candidates": len(candidates),
                "selected_chunks": len(selected),
                "packed_items": len(items),
                "dropped_candidates": len(dropped),
            },
        )
        return PackedContext(
            items=tuple(items),
            total_tokens=total_tokens,
            budget=packing_config.max_tokens,
            dropped_candidates=tuple(dropped),
            packing_trace=trace,
        )

    def _invalid_reason(self, candidate: object) -> str | None:
        if not isinstance(candidate, ContextCandidate):
            return "candidate_type"
        if not candidate.content.strip():
            return "content"
        if candidate.token_count <= 0:
            return "token_count"
        if not isfinite(candidate.score) or candidate.score < 0.0 or candidate.score > 1.0:
            return "score"
        if not candidate.title_path:
            return "title_path"
        if (candidate.page_start is None) != (candidate.page_end is None):
            return "page_range"
        if candidate.page_start is not None and candidate.page_end is not None:
            if candidate.page_start < 1 or candidate.page_end < candidate.page_start:
                return "page_range"
        return None

    def _authorize_or_raise(
        self,
        *,
        candidate: ContextCandidate,
        auth: AuthContext,
        acl_filter: AclFilter,
        request_id: str,
        trace_id: str,
        input_count: int,
    ) -> None:
        if candidate.tenant_id != auth.tenant_id:
            raise _unauthorized_error(
                candidate=candidate,
                auth=auth,
                request_id=request_id,
                trace_id=trace_id,
                input_count=input_count,
                reason="tenant_mismatch",
            )
        if not acl_allows(candidate.acl, acl_filter):
            raise _unauthorized_error(
                candidate=candidate,
                auth=auth,
                request_id=request_id,
                trace_id=trace_id,
                input_count=input_count,
                reason="acl_denied",
            )

    def _handle_oversized_candidate(
        self,
        *,
        candidate: ContextCandidate,
        config: ContextPackingConfig,
        request_id: str,
        trace_id: str,
        auth: AuthContext,
        input_count: int,
    ) -> None:
        if config.oversized_policy != "fail_closed":
            return
        raise RagContextPackingError(
            code=RAG_CONTEXT_BUDGET_EXCEEDED,
            message="Context candidate exceeds token budget.",
            details=_safe_error_details(
                candidate=candidate,
                auth=auth,
                request_id=request_id,
                trace_id=trace_id,
                reason="oversized",
                input_count=input_count,
                error_code=RAG_CONTEXT_BUDGET_EXCEEDED,
            ),
            status_code=400,
        )

    def _related_chunk_requests(
        self,
        candidate: ContextCandidate,
        config: ContextPackingConfig,
    ) -> tuple[tuple[str, str], ...]:
        requests: list[tuple[str, str]] = []
        if config.include_parent_context:
            parent_id = _metadata_text(candidate.metadata, "parent_chunk_id")
            if parent_id is not None:
                requests.append((parent_id, PARENT_CONTEXT_REASON))
        if config.include_child_context:
            for child_id in _metadata_texts(candidate.metadata, "child_chunk_ids"):
                requests.append((child_id, CHILD_CONTEXT_REASON))
        if config.include_neighbor_context:
            prev_id = _metadata_text(candidate.metadata, "neighbor_prev_chunk_id")
            next_id = _metadata_text(candidate.metadata, "neighbor_next_chunk_id")
            if prev_id is not None:
                requests.append((prev_id, NEIGHBOR_CONTEXT_REASON))
            if next_id is not None:
                requests.append((next_id, NEIGHBOR_CONTEXT_REASON))
        return tuple(requests)

    def _build_items(
        self,
        *,
        selected: Sequence[_SelectedCandidate],
        merge_adjacent: bool,
    ) -> tuple[list[PackedContextItem], list[dict[str, object]]]:
        if not merge_adjacent:
            return (
                [_item_from_group((item,)) for item in selected],
                [],
            )

        grouped: dict[tuple[object, ...], list[_SelectedCandidate]] = defaultdict(list)
        for item in selected:
            grouped[_merge_key(item.candidate)].append(item)

        grouped_items: list[tuple[int, PackedContextItem]] = []
        merged_groups: list[dict[str, object]] = []
        for group in grouped.values():
            ordered_group = sorted(group, key=lambda item: _adjacency_sort_key(item.candidate))
            current: list[_SelectedCandidate] = []
            for item in ordered_group:
                if not current:
                    current.append(item)
                    continue
                if _are_adjacent(current[-1].candidate, item.candidate):
                    current.append(item)
                    continue
                self._append_group(
                    grouped_items=grouped_items,
                    merged_groups=merged_groups,
                    group=current,
                )
                current = [item]
            if current:
                self._append_group(
                    grouped_items=grouped_items,
                    merged_groups=merged_groups,
                    group=current,
                )

        grouped_items.sort(key=lambda item: item[0])
        return [item for _, item in grouped_items], merged_groups

    def _append_group(
        self,
        *,
        grouped_items: list[tuple[int, PackedContextItem]],
        merged_groups: list[dict[str, object]],
        group: Sequence[_SelectedCandidate],
    ) -> None:
        item = _item_from_group(group)
        grouped_items.append((min(selected.order for selected in group), item))
        if len(group) > 1:
            first = group[0].candidate
            merged_groups.append(
                {
                    "document_id": first.document_id,
                    "version_id": first.version_id,
                    "chunk_ids": item.chunk_ids,
                    "reason": "adjacent_chunks",
                    "token_count": item.token_count,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                }
            )


def _item_from_group(group: Sequence[_SelectedCandidate]) -> PackedContextItem:
    ordered = tuple(item.candidate for item in group)
    first = ordered[0]
    return PackedContextItem(
        content="\n".join(candidate.content for candidate in ordered),
        token_count=sum(candidate.token_count for candidate in ordered),
        document_id=first.document_id,
        version_id=first.version_id,
        chunk_ids=tuple(candidate.chunk_id for candidate in ordered),
        source=first.source,
        source_uri=first.source_uri,
        source_type=first.source_type,
        page_start=_min_page(ordered),
        page_end=_max_page(ordered),
        title_path=first.title_path,
        score=max(candidate.score for candidate in ordered),
        retrieval_method=_merged_retrieval_method(ordered),
        citation_sources=tuple(
            _citation_source(item.candidate, reason=item.reason) for item in group
        ),
    )


def _citation_source(candidate: ContextCandidate, *, reason: str) -> PackedCitationSource:
    return PackedCitationSource(
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        chunk_id=candidate.chunk_id,
        source=candidate.source,
        source_uri=candidate.source_uri,
        source_type=candidate.source_type,
        page_start=candidate.page_start,
        page_end=candidate.page_end,
        title_path=candidate.title_path,
        score=candidate.score,
        retrieval_method=candidate.retrieval_method,
        token_count=candidate.token_count,
        inclusion_reason=reason,
        metadata=_safe_metadata(candidate.metadata),
    )


def _candidate_identity(candidate: ContextCandidate) -> tuple[str, str, str, str]:
    return (candidate.tenant_id, candidate.document_id, candidate.version_id, candidate.chunk_id)


def _candidate_score(candidate: ContextCandidate) -> float:
    score = getattr(candidate, "score", 0.0)
    return score if isinstance(score, int | float) and isfinite(score) else 0.0


def _drop(
    candidate: object,
    *,
    reason: str,
    related_reason: str | None = None,
) -> ContextDroppedCandidate:
    if not isinstance(candidate, ContextCandidate):
        return ContextDroppedCandidate(reason=reason, related_reason=related_reason)
    return ContextDroppedCandidate(
        reason=reason,
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        chunk_id=candidate.chunk_id,
        tenant_id=candidate.tenant_id,
        token_count=candidate.token_count,
        score=candidate.score,
        retrieval_method=candidate.retrieval_method,
        related_reason=related_reason,
    )


def _unauthorized_error(
    *,
    candidate: ContextCandidate,
    auth: AuthContext,
    request_id: str,
    trace_id: str,
    input_count: int,
    reason: str,
) -> RagContextPackingError:
    return RagContextPackingError(
        code=RAG_CONTEXT_UNAUTHORIZED_CHUNK,
        message="Context candidate is not authorized for this request.",
        details=_safe_error_details(
            candidate=candidate,
            auth=auth,
            request_id=request_id,
            trace_id=trace_id,
            reason=reason,
            input_count=input_count,
            error_code=RAG_CONTEXT_UNAUTHORIZED_CHUNK,
        ),
        status_code=403,
    )


def _safe_error_details(
    *,
    candidate: ContextCandidate,
    auth: AuthContext,
    request_id: str,
    trace_id: str,
    reason: str,
    input_count: int,
    error_code: str,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "document_id": candidate.document_id,
        "version_id": candidate.version_id,
        "chunk_id": candidate.chunk_id,
        "candidate_tenant_id": candidate.tenant_id,
        "reason": reason,
        "drop_reason": "unauthorized",
        "error_code": error_code,
        "safe_counts": {
            "input_candidates": input_count,
            "candidate_token_count": candidate.token_count,
        },
    }


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    return cast("dict[str, object]", _redact_local_paths(redact_mapping(metadata)))


def _redact_local_paths(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _redact_local_paths(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_local_paths(item) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[REDACTED]"
    return value


def _looks_like_local_path(value: str) -> bool:
    normalized = value.strip()
    if len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}:
        return True
    return normalized.startswith(("/home/", "/Users/", "\\\\"))


def _metadata_text(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _metadata_texts(metadata: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, list | tuple | set):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _is_safe_identifier(value: str) -> bool:
    return RELATED_ID_PATTERN.fullmatch(value) is not None


def _safe_diagnostic_id(value: str) -> str:
    return value if _is_safe_identifier(value) else "[REDACTED]"


def _invalid_related_reason(
    *,
    primary: ContextCandidate,
    related: ContextCandidate,
    requested_id: str,
    reason: str,
) -> str | None:
    if related.chunk_id != requested_id:
        return "invalid_related_identity"
    if _same_lineage(primary, related) is False:
        return "invalid_related_lineage"
    if reason == PARENT_CONTEXT_REASON:
        child_ids = set(_metadata_texts(related.metadata, "child_chunk_ids"))
        if child_ids and primary.chunk_id not in child_ids:
            return "invalid_related_metadata"
    elif reason == CHILD_CONTEXT_REASON:
        parent_id = _metadata_text(related.metadata, "parent_chunk_id")
        if parent_id is not None and parent_id != primary.chunk_id:
            return "invalid_related_metadata"
    elif reason == NEIGHBOR_CONTEXT_REASON:
        primary_prev = _metadata_text(primary.metadata, "neighbor_prev_chunk_id")
        primary_next = _metadata_text(primary.metadata, "neighbor_next_chunk_id")
        if requested_id == primary_prev:
            reciprocal = _metadata_text(related.metadata, "neighbor_next_chunk_id")
            if reciprocal is not None and reciprocal != primary.chunk_id:
                return "invalid_related_metadata"
        elif requested_id == primary_next:
            reciprocal = _metadata_text(related.metadata, "neighbor_prev_chunk_id")
            if reciprocal is not None and reciprocal != primary.chunk_id:
                return "invalid_related_metadata"
        else:
            return "invalid_related_metadata"
    return None


def _same_lineage(left: ContextCandidate, right: ContextCandidate) -> bool:
    return (
        left.tenant_id == right.tenant_id
        and left.document_id == right.document_id
        and left.version_id == right.version_id
        and left.title_path == right.title_path
    )


def _merge_key(candidate: ContextCandidate) -> tuple[object, ...]:
    return (
        candidate.tenant_id,
        candidate.document_id,
        candidate.version_id,
        candidate.title_path,
        tuple(sorted((str(key), repr(value)) for key, value in candidate.acl.items())),
    )


def _adjacency_sort_key(candidate: ContextCandidate) -> tuple[int, int, str]:
    sequence = _sequence(candidate)
    if sequence is not None:
        return (0, sequence, candidate.chunk_id)
    if candidate.page_start is not None:
        return (1, candidate.page_start, candidate.chunk_id)
    return (2, 0, candidate.chunk_id)


def _are_adjacent(left: ContextCandidate, right: ContextCandidate) -> bool:
    if _merge_key(left) != _merge_key(right):
        return False
    left_sequence = _sequence(left)
    right_sequence = _sequence(right)
    if left_sequence is not None and right_sequence is not None:
        return right_sequence == left_sequence + 1
    if (
        left.page_start is not None
        and left.page_end is not None
        and right.page_start is not None
        and right.page_end is not None
    ):
        return right.page_start == left.page_end + 1
    return False


def _sequence(candidate: ContextCandidate) -> int | None:
    for key in ("chunk_index", "sequence"):
        value = candidate.metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _min_page(candidates: Sequence[ContextCandidate]) -> int | None:
    pages = [candidate.page_start for candidate in candidates if candidate.page_start is not None]
    return min(pages) if pages else None


def _max_page(candidates: Sequence[ContextCandidate]) -> int | None:
    pages = [candidate.page_end for candidate in candidates if candidate.page_end is not None]
    return max(pages) if pages else None


def _merged_retrieval_method(candidates: Sequence[ContextCandidate]) -> str:
    methods = tuple(dict.fromkeys(candidate.retrieval_method for candidate in candidates))
    if len(methods) == 1:
        return methods[0]
    return "merged"
