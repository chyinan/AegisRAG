"""Surya OCR provider — modern deep-learning OCR with layout analysis."""

from __future__ import annotations

from packages.ingestion.exceptions import GenericDocumentParseError


class SuryaOCRProvider:
    """OCR via Surya (VikParuchuri/surya).

    Surya is a modern deep-learning OCR engine that handles 90+ languages with
    strong accuracy on scanned documents, handwriting, and complex layouts.
    Supports GPU acceleration.

    Install: ``pip install surya-ocr``
    """

    def __init__(
        self,
        *,
        languages: list[str] | None = None,
    ) -> None:
        self._languages = languages or ["en", "zh"]
        self._model: object | None = None

    def _ensure_surya(self) -> object:
        if self._model is not None:
            return self._model

        try:
            from surya.model.detection.model import (  # type: ignore[import-untyped]
                load_model as load_det_model,
            )
            from surya.model.detection.model import (
                load_processor as load_det_processor,
            )
            from surya.model.recognition.model import (  # type: ignore[import-untyped]
                load_model as load_rec_model,
            )
            from surya.model.recognition.processor import (  # type: ignore[import-untyped]
                load_processor as load_rec_processor,
            )
            from surya.ocr import run_ocr  # type: ignore[import-untyped]
        except ImportError as exc:
            raise GenericDocumentParseError(
                details={
                    "reason": "surya_not_installed",
                    "help": "Install Surya: pip install surya-ocr",
                }
            ) from exc

        det_processor = load_det_processor()
        det_model = load_det_model()
        rec_model = load_rec_model()
        rec_processor = load_rec_processor()

        class _SuryaRunner:
            def __init__(self, langs, det_p, det_m, rec_p, rec_m):
                self.langs = langs
                self.det_processor = det_p
                self.det_model = det_m
                self.rec_processor = rec_p
                self.rec_model = rec_m

            def ocr(self, image):
                return run_ocr(
                    [image],
                    [self.langs],
                    det_model=self.det_model,
                    det_processor=self.det_processor,
                    rec_model=self.rec_model,
                    rec_processor=self.rec_processor,
                )

        self._model = _SuryaRunner(
            self._languages,
            det_processor, det_model,
            rec_processor, rec_model,
        )
        return self._model

    def extract_text(
        self,
        *,
        image: object,  # PIL.Image.Image
        lang: str = "en+zh",
    ) -> str:
        runner = self._ensure_surya()

        # Surya can accept PIL Image directly
        results = runner.ocr(image)
        if not results:
            return ""

        lines: list[str] = []
        for page_result in results:
            for line in page_result.text_lines:
                lines.append(line.text)

        return "\n".join(lines)

