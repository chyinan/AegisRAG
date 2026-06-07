from __future__ import annotations

from packages.ingestion.cleaner import DefaultDocumentCleaner
from packages.ingestion.dedup import ExactSectionDeduplicator
from packages.ingestion.domain import ParsedDocument, Section


def _section(
    section_id: str,
    content: str,
    *,
    title_path: list[str] | None = None,
    page: int | None = None,
    acl: dict[str, object] | None = None,
) -> Section:
    return Section(
        section_id=section_id,
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type="pdf" if page is not None else "markdown",
        source_uri="kb://policy",
        title=f"Section {section_id}",
        title_path=title_path or ["Policy", "Scope"],
        content=content,
        page_start=page,
        page_end=page,
        acl=acl or {"visibility": "tenant", "groups": ["hr"]},
        metadata={"parser": "synthetic"},
    )


def _document(sections: list[Section]) -> ParsedDocument:
    return ParsedDocument(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type="markdown",
        source_uri="kb://policy",
        sections=sections,
        acl={"visibility": "tenant", "groups": ["hr"]},
        checksum="raw-checksum-1",
        metadata={"stage": "parsed"},
    )


def test_dedup_removes_exact_duplicate_sections_under_same_title_path() -> None:
    document = DefaultDocumentCleaner().clean(
        _document(
            [
                _section("s1", "Same body", title_path=["Policy", "Scope"]),
                _section("s2", "Same body  ", title_path=["Policy", "Scope"]),
                _section("s3", "Different body", title_path=["Policy", "Scope"]),
            ]
        )
    )

    deduped = ExactSectionDeduplicator().deduplicate(document)

    assert [section.section_id for section in deduped.sections] == ["s1", "s3"]
    assert deduped.metadata["cleaning_stage"] == "deduped"
    assert deduped.metadata["duplicate_section_count"] == 1
    assert deduped.metadata["deduped_section_count"] == 2
    assert deduped.metadata["dropped_duplicate_section_ids"] == ["s2"]


def test_dedup_keeps_first_duplicate_and_preserves_governance_metadata() -> None:
    first = _section(
        "s1",
        "Same body",
        title_path=["Policy", "Scope"],
        page=1,
        acl={"visibility": "restricted", "users": ["user-1"]},
    )
    duplicate = _section(
        "s2",
        "Same body",
        title_path=["Policy", "Scope"],
        page=2,
        acl={"visibility": "restricted", "users": ["user-1"]},
    )
    document = DefaultDocumentCleaner().clean(_document([first, duplicate]))

    deduped = ExactSectionDeduplicator().deduplicate(document)

    kept = deduped.sections[0]
    assert kept.section_id == first.section_id
    assert kept.tenant_id == first.tenant_id
    assert kept.document_id == first.document_id
    assert kept.version_id == first.version_id
    assert kept.source_uri == first.source_uri
    assert kept.title_path == first.title_path
    assert kept.page_start == first.page_start
    assert kept.page_end == first.page_end
    assert kept.acl == first.acl


def test_dedup_does_not_remove_same_content_under_different_title_path() -> None:
    document = DefaultDocumentCleaner().clean(
        _document(
            [
                _section("s1", "Shared body", title_path=["Policy", "Scope"]),
                _section("s2", "Shared body", title_path=["Policy", "Definitions"]),
            ]
        )
    )

    deduped = ExactSectionDeduplicator().deduplicate(document)

    assert [section.section_id for section in deduped.sections] == ["s1", "s2"]
    assert deduped.metadata["duplicate_section_count"] == 0


def test_dedup_does_not_trust_stale_content_checksum_metadata() -> None:
    stale_checksum = "a" * 64
    document = _document(
        [
            _section("s1", "First body").model_copy(
                update={"metadata": {"content_checksum": stale_checksum}}
            ),
            _section("s2", "Different body").model_copy(
                update={"metadata": {"content_checksum": stale_checksum}}
            ),
        ]
    )

    deduped = ExactSectionDeduplicator().deduplicate(document)

    assert [section.section_id for section in deduped.sections] == ["s1", "s2"]
    assert deduped.metadata["duplicate_section_count"] == 0
    assert deduped.sections[0].metadata["content_checksum"] != stale_checksum


def test_dedup_does_not_remove_exact_duplicates_with_different_acl() -> None:
    document = DefaultDocumentCleaner().clean(
        _document(
            [
                _section("s1", "Same body", acl={"visibility": "tenant"}),
                _section(
                    "s2",
                    "Same body",
                    acl={"visibility": "restricted", "users": ["user-1"]},
                ),
            ]
        )
    )

    deduped = ExactSectionDeduplicator().deduplicate(document)

    assert [section.section_id for section in deduped.sections] == ["s1", "s2"]
    assert deduped.metadata["duplicate_section_count"] == 0


def test_dedup_preserves_source_order_after_dropping_later_duplicates() -> None:
    document = DefaultDocumentCleaner().clean(
        _document(
            [
                _section("s1", "Alpha", title_path=["Policy"]),
                _section("s2", "Beta", title_path=["Policy"]),
                _section("s3", "Alpha", title_path=["Policy"]),
                _section("s4", "Gamma", title_path=["Policy"]),
                _section("s5", "Beta", title_path=["Policy"]),
            ]
        )
    )

    deduped = ExactSectionDeduplicator().deduplicate(document)

    assert [section.section_id for section in deduped.sections] == ["s1", "s2", "s4"]


def test_dedup_content_checksum_is_stable_for_canonical_text() -> None:
    cleaner = DefaultDocumentCleaner()
    first = cleaner.clean(_document([_section("s1", "Alpha\r\n\r\nBeta   ")]))
    second = cleaner.clean(_document([_section("s1", "Alpha\n\nBeta")]))

    first_checksum = first.sections[0].metadata["content_checksum"]
    second_checksum = second.sections[0].metadata["content_checksum"]

    assert first_checksum == second_checksum
