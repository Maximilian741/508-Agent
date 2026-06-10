"""OCR provider abstraction for scanned-PDF remediation.

A scanned PDF (image-only pages) is the single hardest real-world failure:
there is literally no text for AT to read. When a deployment has OCR enabled
(``OCR_ENABLED=true`` + a working Tesseract install), the pipeline can add an
INVISIBLE, position-matched text layer to each scanned page — making the PDF
searchable and giving the PDF/UA tagger real text to structure.

Design:
- ``OcrProvider`` is a tiny protocol so the writer/executor never import
  pytesseract directly; deployments without Tesseract degrade gracefully
  (the action is SKIPPED with an honest note, never silently claimed).
- ``set_ocr_provider_for_testing`` lets smokes inject a stub provider, so the
  overlay/tagging pipeline is byte-level verified even on hosts without
  Tesseract.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class OcrWord:
    """One recognized word, in IMAGE pixel coordinates (top-left origin)."""

    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrPageResult:
    width_px: float
    height_px: float
    words: List[OcrWord] = field(default_factory=list)


class OcrProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognize(self, image_bytes: bytes) -> Optional[OcrPageResult]: ...


class TesseractOcrProvider:
    """Tesseract via pytesseract. ``available()`` is False unless both the
    Python wrapper AND the tesseract binary are present."""

    name = "tesseract"

    def available(self) -> bool:
        try:
            import pytesseract  # noqa: PLC0415

            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def recognize(self, image_bytes: bytes) -> Optional[OcrPageResult]:
        try:
            import pytesseract  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            img = Image.open(io.BytesIO(image_bytes))
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            words: List[OcrWord] = []
            for i, text in enumerate(data.get("text", [])):
                t = (text or "").strip()
                if not t:
                    continue
                try:
                    conf = float(data["conf"][i])
                except (TypeError, ValueError, KeyError, IndexError):
                    conf = -1.0
                if conf < 40:  # drop low-confidence noise
                    continue
                words.append(
                    OcrWord(
                        text=t,
                        x=float(data["left"][i]),
                        y=float(data["top"][i]),
                        w=float(data["width"][i]),
                        h=float(data["height"][i]),
                    )
                )
            return OcrPageResult(width_px=float(img.width), height_px=float(img.height), words=words)
        except Exception as exc:
            logger.warning("tesseract recognize failed: %s", exc)
            return None


_provider_override: Optional[OcrProvider] = None


def set_ocr_provider_for_testing(provider: Optional[OcrProvider]) -> None:
    """Inject (or clear, with None) a stub provider — tests only."""

    global _provider_override
    _provider_override = provider


def get_ocr_provider() -> Optional[OcrProvider]:
    """The active OCR provider, or None when disabled/unavailable."""

    if _provider_override is not None:
        return _provider_override
    settings = get_settings()
    if not getattr(settings, "ocr_enabled", False):
        return None
    provider = TesseractOcrProvider()
    if not provider.available():
        logger.info("OCR_ENABLED is set but tesseract is not available — OCR off")
        return None
    return provider
