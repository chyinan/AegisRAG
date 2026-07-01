"""OCR provider ports — protocol + base types.

Follows the same provider-neutral pattern as LLM / Embedding / VectorStore.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from packages.common.config import AppSettings


class OCRProvider(Protocol):
    """Protocol for OCR engines.

    All OCR is CPU-intensive sync work.  Implementations return extracted text
    from a single image (PIL.Image or raw bytes).  Callers should wrap calls in
    ``run_in_executor`` to avoid blocking the event loop.
    """

    def extract_text(
        self,
        *,
        image: object,  # PIL.Image.Image
        lang: str = "eng+chi_sim",
    ) -> str:
        """Extract text from an image.

        ``lang`` is a hint for language selection (e.g. ``"eng+chi_sim"``,
        ``"ch"``, ``"en"``).  Providers MAY ignore this hint if their language
        is fixed at construction time — check the provider's documentation.
        """
        ...

    def supports_pdf_render(self) -> bool:
        """Whether this provider can handle PDF rendering internally.

        If False, the caller must render PDF pages to images via PyMuPDF before
        passing individual page images here.
        """
        ...


# Lazy executor — created on first use, not at module import time.
_ocr_executor: ThreadPoolExecutor | None = None


def _get_ocr_executor() -> ThreadPoolExecutor:
    """Return the shared OCR thread-pool executor, creating it lazily."""
    global _ocr_executor
    if _ocr_executor is None:
        settings = AppSettings()
        _ocr_executor = ThreadPoolExecutor(
            max_workers=settings.ocr_executor_max_workers,
            thread_name_prefix="ocr",
        )
    return _ocr_executor


async def ocr_extract(
    provider: OCRProvider,
    *,
    image: object,
    lang: str = "eng+chi_sim",
) -> str:
    """Async wrapper that runs OCR in a thread pool.

    Timeout is read from ``settings.ocr_timeout_seconds``.
    """

    def _run() -> str:
        return provider.extract_text(image=image, lang=lang)

    settings = AppSettings()
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_get_ocr_executor(), _run),
        timeout=settings.ocr_timeout_seconds,
    )
