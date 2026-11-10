"""Ekstraksi teks PDF: PyMuPDF utama, pdfplumber cadangan, OCR (Tesseract) opsional."""
import os
import re
import shutil
from functools import lru_cache

import pymupdf

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b\ufeff]")
_SPACES = re.compile(r"[ \t\u00a0]+")


class ExtractionError(Exception):
    pass


def normalize(text: str) -> str:
    """Buang karakter kontrol, rapikan spasi ganda, trim, buang baris kosong."""
    text = _CTRL.sub(" ", text.replace("\r", "\n"))
    lines = (_SPACES.sub(" ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def extract_pages(path, ocr: bool = False) -> list[str]:
    """Teks per halaman (sudah dinormalisasi)."""
    pages: list[str] = []
    try:
        with pymupdf.open(path) as doc:
            pages = [p.get_text("text") for p in doc]
    except Exception:
        pass
    if not any(p.strip() for p in pages):
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
        except Exception:
            if not pages:
                raise ExtractionError("PDF rusak / tidak bisa dibuka")
    if not any(p.strip() for p in pages):
        if not ocr:
            raise ExtractionError("Tidak ada teks (PDF hasil scan?). Aktifkan OCR.")
        pages = _ocr(path)
    return [normalize(p) for p in pages]


@lru_cache(maxsize=1)
def _tesseract_lang() -> str:
    import pytesseract
    default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not shutil.which("tesseract") and os.path.exists(default):
        pytesseract.pytesseract.tesseract_cmd = default
    return "ind+eng" if "ind" in pytesseract.get_languages() else "eng"


def _ocr(path) -> list[str]:
    try:
        import pytesseract
        from PIL import Image
        lang = _tesseract_lang()
    except Exception as e:
        raise ExtractionError(f"OCR tidak tersedia (Tesseract belum terpasang?): {e}") from e
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(pytesseract.image_to_string(img, lang=lang))
    return out
