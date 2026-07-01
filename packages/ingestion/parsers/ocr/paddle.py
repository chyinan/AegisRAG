"""PaddleOCR provider — Baidu open-source, strong Chinese recognition."""

from __future__ import annotations

import numpy as np

from packages.ingestion.exceptions import GenericDocumentParseError


class PaddleOCRProvider:
    """OCR via PaddleOCR.

    PaddleOCR provides state-of-the-art Chinese text recognition and supports
    80+ languages.  It runs locally (CPU/GPU) without network calls.

    Install: ``pip install paddleocr`` (also pulls paddlepaddle).
    """

    def __init__(
        self,
        *,
        lang: str = "ch",
        use_gpu: bool = False,
    ) -> None:
        self._lang = lang
        self._use_gpu = use_gpu
        self._ocr: object | None = None

    def _ensure_paddle(self) -> object:
        if self._ocr is not None:
            return self._ocr

        try:
            from paddleocr import PaddleOCR  # type: ignore[import-untyped]
        except ImportError as exc:
            raise GenericDocumentParseError(
                details={
                    "reason": "paddleocr_not_installed",
                    "help": "Install PaddleOCR: pip install paddleocr",
                }
            ) from exc

        self._ocr = PaddleOCR(
            lang=self._lang,
            use_gpu=self._use_gpu,
            show_log=False,
        )
        return self._ocr

    def extract_text(
        self,
        *,
        image: object,  # PIL.Image.Image or numpy array
        lang: str = "ch",
    ) -> str:
        ocr = self._ensure_paddle()

        # PaddleOCR works best with numpy arrays
        if hasattr(image, "convert"):
            # PIL Image → RGB numpy
            arr = np.array(image.convert("RGB"))
        elif isinstance(image, np.ndarray):
            arr = image
        else:
            arr = np.array(image)

        results = ocr.ocr(arr, cls=True)
        if not results or not results[0]:
            return ""

        lines: list[str] = []
        for line_info in results[0]:
            text = line_info[1][0]  # (bbox, (text, confidence))
            lines.append(text)

        return "\n".join(lines)

    def supports_pdf_render(self) -> bool:
        return False  # caller must render PDF pages via PyMuPDF
