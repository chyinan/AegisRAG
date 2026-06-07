from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter

from packages.ingestion.domain import ParsedDocument, Section
from packages.ingestion.exceptions import EmptyCleanedDocumentError

_MULTIPLE_BLANK_LINES = re.compile(r"\n{3,}")
_DIGITS = re.compile(r"\d+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
_MAX_HEADER_FOOTER_LINE_LENGTH = 80
_MIN_PAGE_SECTIONS_FOR_HEADER_FOOTER = 3
_MARGIN_LINE_COUNT = 1


class DefaultDocumentCleaner:
    def clean(self, document: ParsedDocument) -> ParsedDocument:
        header_footer_keys = _detect_repeated_pdf_line_keys(document.sections)
        cleaned_sections: list[Section] = []
        removed_empty_section_count = 0
        removed_header_footer_line_count = 0

        for section in document.sections:
            cleaned_content, removed_line_count = _clean_section_content(
                section.content,
                header_footer_keys=header_footer_keys,
            )
            removed_header_footer_line_count += removed_line_count
            if not cleaned_content:
                removed_empty_section_count += 1
                continue
            cleaned_sections.append(
                section.model_copy(
                    update={
                        "content": cleaned_content,
                        "metadata": {
                            **section.metadata,
                            "content_checksum": stable_content_checksum(cleaned_content),
                        },
                    }
                )
            )

        if not cleaned_sections:
            raise EmptyCleanedDocumentError()

        removed_section_count = len(document.sections) - len(cleaned_sections)
        return document.model_copy(
            update={
                "sections": cleaned_sections,
                "metadata": {
                    **document.metadata,
                    "cleaning_stage": "cleaned",
                    "cleaned_section_count": len(cleaned_sections),
                    "removed_section_count": removed_section_count,
                    "removed_empty_section_count": removed_empty_section_count,
                    "removed_header_footer_line_count": removed_header_footer_line_count,
                    "deduped_section_count": len(cleaned_sections),
                },
            }
        )


def stable_content_checksum(content: str) -> str:
    canonical = canonicalize_content(content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.translate(_ZERO_WIDTH)
    lines = [line.rstrip() for line in normalized.split("\n")]
    compacted = "\n".join(lines).strip()
    return _MULTIPLE_BLANK_LINES.sub("\n\n", compacted)


def _clean_section_content(
    content: str,
    *,
    header_footer_keys: set[str],
) -> tuple[str, int]:
    canonical = canonicalize_content(content)
    if not canonical:
        return "", 0

    lines = canonical.split("\n")
    margin_indexes = _margin_indexes(lines)
    kept_lines: list[str] = []
    removed_line_count = 0
    for index, line in enumerate(lines):
        if index in margin_indexes and _line_key(line) in header_footer_keys:
            removed_line_count += 1
            continue
        kept_lines.append(line)

    return canonicalize_content("\n".join(kept_lines)), removed_line_count


def _detect_repeated_pdf_line_keys(sections: list[Section]) -> set[str]:
    page_sections = [
        section
        for section in sections
        if (
            section.source_type == "pdf"
            and section.page_start is not None
            and section.page_end is not None
        )
    ]
    if len(page_sections) < _MIN_PAGE_SECTIONS_FOR_HEADER_FOOTER:
        return set()

    page_count = len(page_sections)
    key_counts: Counter[str] = Counter()
    for section in page_sections:
        lines = canonicalize_content(section.content).split("\n")
        page_keys = {
            _line_key(line)
            for index, line in enumerate(lines)
            if index in _margin_indexes(lines)
            if _is_header_footer_candidate(line)
        }
        key_counts.update(page_keys)

    minimum_repeats = max(2, int(page_count * 0.6))
    return {key for key, count in key_counts.items() if count >= minimum_repeats}


def _is_header_footer_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > _MAX_HEADER_FOOTER_LINE_LENGTH:
        return False
    if stripped.endswith((".", "。", "!", "！", "?", "？", ";", "；")):
        return False
    return True


def _line_key(line: str) -> str:
    normalized = unicodedata.normalize("NFKC", line).strip().casefold()
    normalized = _DIGITS.sub("#", normalized)
    return " ".join(normalized.split())


def _margin_indexes(lines: list[str]) -> set[int]:
    if not lines:
        return set()
    last_margin_start = max(0, len(lines) - _MARGIN_LINE_COUNT)
    return {
        index
        for index in range(len(lines))
        if index < _MARGIN_LINE_COUNT or index >= last_margin_start
    }
