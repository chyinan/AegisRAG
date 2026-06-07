from __future__ import annotations

import re

from packages.ingestion.domain import ParsedDocument, ParseRequest, Section
from packages.ingestion.parsers._common import decode_utf8_strict, section_metadata

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_UNTITLED = "Untitled"


class MarkdownParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        text = decode_utf8_strict(request.content)
        sections: list[Section] = []
        heading_stack: list[str | None] = [None] * 6
        current_title_path = [_UNTITLED]
        current_lines: list[str] = []
        section_index = 1
        in_fence = False

        def flush(*, force_heading: bool = False) -> None:
            nonlocal section_index, current_lines
            content = "\n".join(current_lines).strip()
            if not content:
                if force_heading and current_title_path != [_UNTITLED]:
                    title_path = list(current_title_path)
                    sections.append(
                        Section(
                            section_id=f"{request.version_id}:section-{section_index}",
                            tenant_id=request.tenant_id,
                            document_id=request.document_id,
                            version_id=request.version_id,
                            source_type="markdown",
                            source_uri=request.source_uri,
                            title=title_path[-1],
                            title_path=title_path,
                            content=title_path[-1],
                            acl=request.acl,
                            metadata=section_metadata(request, title_path=title_path),
                        )
                    )
                    section_index += 1
                current_lines = []
                return
            title_path = list(current_title_path)
            sections.append(
                Section(
                    section_id=f"{request.version_id}:section-{section_index}",
                    tenant_id=request.tenant_id,
                    document_id=request.document_id,
                    version_id=request.version_id,
                    source_type="markdown",
                    source_uri=request.source_uri,
                    title=title_path[-1],
                    title_path=title_path,
                    content=content,
                    acl=request.acl,
                    metadata=section_metadata(request, title_path=title_path),
                )
            )
            section_index += 1
            current_lines = []

        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                current_lines.append(line)
                continue
            match = None if in_fence else _HEADING_PATTERN.match(line)
            if match is None:
                current_lines.append(line)
                continue
            flush(force_heading=current_title_path != [_UNTITLED])
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack[level - 1] = title
            for index in range(level, len(heading_stack)):
                heading_stack[index] = None
            current_title_path = [item for item in heading_stack[:level] if item]
            if not current_title_path:
                current_title_path = [title]

        flush(force_heading=current_title_path != [_UNTITLED])
        if not sections:
            title_path = [_UNTITLED]
            sections.append(
                Section(
                    section_id=f"{request.version_id}:section-1",
                    tenant_id=request.tenant_id,
                    document_id=request.document_id,
                    version_id=request.version_id,
                    source_type="markdown",
                    source_uri=request.source_uri,
                    title=_UNTITLED,
                    title_path=title_path,
                    content=text.strip(),
                    acl=request.acl,
                    metadata=section_metadata(request, title_path=title_path),
                )
            )
        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type="markdown",
            source_uri=request.source_uri,
            sections=sections,
            acl=request.acl,
            checksum=request.checksum,
            metadata={
                "filename": request.filename,
                "source_uri": request.source_uri,
                "section_count": len(sections),
            },
        )
