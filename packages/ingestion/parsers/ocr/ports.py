"""OCR provider ports — protocol + base types.

Follows the same provider-neutral pattern as LLM / Embedding / VectorStore.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol


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
    ) -> str: ...

    def supports_pdf_render(self) -> bool:
        """Whether this provider can handle PDF rendering internally.

        If False, the caller must render PDF pages to images via PyMuPDF before
        passing individual page images here.
        """
        ...


# Shared executor for CPU-bound OCR (keeps event loop free)
_ocr_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr")


async def ocr_extract(
    provider: OCRProvider,
    *,
    image: object,
    lang: str = "eng+chi_sim",
    timeout_seconds: float = 60.0,
) -> str:
    """Async wrapper that runs OCR in a thread pool."""

    def _run() -> str:
        return provider.extract_text(image=image, lang=lang)

    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_ocr_executor, _run),
        timeout=timeout_seconds,
    )
