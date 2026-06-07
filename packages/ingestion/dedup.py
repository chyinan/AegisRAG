from __future__ import annotations

import json
import unicodedata

from packages.ingestion.cleaner import stable_content_checksum
from packages.ingestion.domain import ParsedDocument, Section


class ExactSectionDeduplicator:
    def deduplicate(self, document: ParsedDocument) -> ParsedDocument:
        seen_keys: set[tuple[str, tuple[str, ...], str]] = set()
        kept_sections: list[Section] = []
        dropped_section_ids: list[str] = []

        for section in document.sections:
            content_checksum = _section_content_checksum(section)
            dedup_key = (
                content_checksum,
                _normalized_title_path(section.title_path),
                _normalized_acl(section.acl),
            )
            if dedup_key in seen_keys:
                dropped_section_ids.append(section.section_id)
                continue

            seen_keys.add(dedup_key)
            if section.metadata.get("content_checksum") == content_checksum:
                kept_sections.append(section)
            else:
                kept_sections.append(
                    section.model_copy(
                        update={
                            "metadata": {
                                **section.metadata,
                                "content_checksum": content_checksum,
                            }
                        }
                    )
                )

        return document.model_copy(
            update={
                "sections": kept_sections,
                "metadata": {
                    **document.metadata,
                    "cleaning_stage": "deduped",
                    "duplicate_section_count": len(dropped_section_ids),
                    "deduped_section_count": len(kept_sections),
                    "dropped_duplicate_section_ids": dropped_section_ids,
                    "kept_section_ids": [section.section_id for section in kept_sections],
                },
            }
        )


def _section_content_checksum(section: Section) -> str:
    return stable_content_checksum(section.content)


def _normalized_title_path(title_path: list[str]) -> tuple[str, ...]:
    return tuple(
        " ".join(unicodedata.normalize("NFKC", title).casefold().split())
        for title in title_path
    )


def _normalized_acl(acl: dict[str, object]) -> str:
    return json.dumps(acl, sort_keys=True, separators=(",", ":"), default=str)
