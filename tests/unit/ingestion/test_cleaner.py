from __future__ import annotations

import pytest

from packages.ingestion.cleaner import DefaultDocumentCleaner
from packages.ingestion.domain import ParsedDocument, Section
from packages.ingestion.exceptions import (
    DOCUMENT_CLEAN_EMPTY_CONTENT,
    EmptyCleanedDocumentError,
)


def _section(
    section_id: str,
    content: str,
    *,
    source_type: str = "markdown",
    title_path: list[str] | None = None,
    page: int | None = None,
    acl: dict[str, object] | None = None,
) -> Section:
    return Section(
        section_id=section_id,
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type=source_type,
        source_uri="kb://policy",
        title=f"Section {section_id}",
        title_path=title_path or ["Policy"],
        content=content,
        page_start=page,
        page_end=page,
        acl=acl or {"visibility": "tenant", "groups": ["hr"]},
        metadata={"parser": "synthetic"},
    )


def _document(sections: list[Section], *, source_type: str = "markdown") -> ParsedDocument:
    return ParsedDocument(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type=source_type,
        source_uri="kb://policy",
        sections=sections,
        acl={"visibility": "tenant", "groups": ["hr"]},
        checksum="raw-checksum-1",
        metadata={"stage": "parsed"},
    )


def test_cleaner_normalizes_whitespace_and_records_stable_section_checksum() -> None:
    document = _document(
        [
            _section(
                "s1",
                "  Policy title  \r\nFirst paragraph.   \r\n\r\n\r\nSecond paragraph.\t\r\n",
            )
        ]
    )

    cleaned = DefaultDocumentCleaner().clean(document)

    assert cleaned.checksum == "raw-checksum-1"
    assert cleaned.metadata["cleaning_stage"] == "cleaned"
    assert cleaned.metadata["cleaned_section_count"] == 1
    assert cleaned.metadata["removed_section_count"] == 0
    assert cleaned.metadata["removed_empty_section_count"] == 0
    assert cleaned.sections[0].content == "Policy title\nFirst paragraph.\n\nSecond paragraph."
    content_checksum = cleaned.sections[0].metadata["content_checksum"]
    assert isinstance(content_checksum, str)
    assert len(content_checksum) == 64


def test_cleaner_preserves_governance_fields_and_page_metadata() -> None:
    section = _section(
        "s1",
        "Body",
        source_type="pdf",
        title_path=["Policy", "Page 1"],
        page=1,
        acl={"visibility": "restricted", "users": ["user-1"]},
    )
    document = _document([section], source_type="pdf")

    cleaned = DefaultDocumentCleaner().clean(document)

    cleaned_section = cleaned.sections[0]
    assert cleaned_section.section_id == section.section_id
    assert cleaned_section.tenant_id == section.tenant_id
    assert cleaned_section.document_id == section.document_id
    assert cleaned_section.version_id == section.version_id
    assert cleaned_section.source_type == section.source_type
    assert cleaned_section.source_uri == section.source_uri
    assert cleaned_section.title == section.title
    assert cleaned_section.title_path == section.title_path
    assert cleaned_section.page_start == section.page_start
    assert cleaned_section.page_end == section.page_end
    assert cleaned_section.acl == section.acl


def test_cleaner_removes_repeated_pdf_headers_and_footers_conservatively() -> None:
    document = _document(
        [
            _section(
                "p1",
                "Company Confidential\nBody paragraph repeated.\nBusiness rule A\nPage 1 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 1"],
                page=1,
            ),
            _section(
                "p2",
                "Company Confidential\nBody paragraph repeated.\nBusiness rule B\nPage 2 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 2"],
                page=2,
            ),
            _section(
                "p3",
                "Company Confidential\nBody paragraph repeated.\nBusiness rule C\nPage 3 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 3"],
                page=3,
            ),
        ],
        source_type="pdf",
    )

    cleaned = DefaultDocumentCleaner().clean(document)

    assert "Company Confidential" not in "\n".join(section.content for section in cleaned.sections)
    assert "Page 1 of 3" not in cleaned.sections[0].content
    assert "Page 2 of 3" not in cleaned.sections[1].content
    assert "Page 3 of 3" not in cleaned.sections[2].content
    assert all("Body paragraph repeated." in section.content for section in cleaned.sections)
    assert cleaned.metadata["removed_header_footer_line_count"] == 6


def test_cleaner_does_not_remove_repeated_pdf_body_lines() -> None:
    document = _document(
        [
            _section(
                "p1",
                "Company Confidential\nEligibility Requirement\nBusiness rule A\nPage 1 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 1"],
                page=1,
            ),
            _section(
                "p2",
                "Company Confidential\nEligibility Requirement\nBusiness rule B\nPage 2 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 2"],
                page=2,
            ),
            _section(
                "p3",
                "Company Confidential\nEligibility Requirement\nBusiness rule C\nPage 3 of 3",
                source_type="pdf",
                title_path=["Policy", "Page 3"],
                page=3,
            ),
        ],
        source_type="pdf",
    )

    cleaned = DefaultDocumentCleaner().clean(document)

    assert all("Eligibility Requirement" in section.content for section in cleaned.sections)
    assert "Company Confidential" not in "\n".join(section.content for section in cleaned.sections)
    assert cleaned.metadata["removed_header_footer_line_count"] == 6


def test_cleaner_does_not_remove_repeated_lines_without_reliable_page_metadata() -> None:
    document = _document(
        [
            _section(
                "s1",
                "Repeated business sentence.\nFirst detail",
                source_type="docx",
                title_path=["Policy", "Scope"],
            ),
            _section(
                "s2",
                "Repeated business sentence.\nSecond detail",
                source_type="docx",
                title_path=["Policy", "Scope"],
            ),
        ],
        source_type="docx",
    )

    cleaned = DefaultDocumentCleaner().clean(document)

    assert all("Repeated business sentence." in section.content for section in cleaned.sections)
    assert cleaned.metadata["removed_header_footer_line_count"] == 0


def test_cleaner_removes_empty_sections_without_recording_removed_text() -> None:
    document = _document(
        [
            _section("s1", "Keep this paragraph"),
            _section("s2", "\u200b\n\u200b"),
            _section("s3", "Keep another paragraph"),
        ]
    )

    cleaned = DefaultDocumentCleaner().clean(document)

    assert [section.section_id for section in cleaned.sections] == ["s1", "s3"]
    assert cleaned.metadata["removed_empty_section_count"] == 1
    assert "\u200b" not in repr(cleaned.metadata)


def test_cleaner_nfkc_normalization_is_stable_for_full_width_text() -> None:
    document = _document([_section("s1", "ＡＢＣ１２３")])

    cleaned = DefaultDocumentCleaner().clean(document)

    assert cleaned.sections[0].content == "ABC123"


def test_cleaner_raises_stable_error_when_all_sections_are_empty_after_cleaning() -> None:
    document = _document([_section("s1", "\u200b\n\u200b")])

    with pytest.raises(EmptyCleanedDocumentError) as exc_info:
        DefaultDocumentCleaner().clean(document)

    assert exc_info.value.code == DOCUMENT_CLEAN_EMPTY_CONTENT
