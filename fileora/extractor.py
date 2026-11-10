"""Ekstraksi teks PDF: PyMuPDF utama, pdfplumber cadangan, OCR (Tesseract) opsional, konkuren terkontrol."""
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import pymupdf

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b\ufeff]")
_SPACES = re.compile(r"[ \t\u00a0]+")
MAX_WORKERS = min(8, (os.cpu_count() or 4))


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
            if doc.is_encrypted and not doc.authenticate(""):
                raise ExtractionError("PDF terenkripsi (terkunci password)")
            if len(doc) == 0:
                raise ExtractionError("PDF kosong (0 halaman)")
            pages = [p.get_text("text") for p in doc]
    except ExtractionError:
        raise
    except Exception:
        pass
    if not any(p.strip() for p in pages):
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    raise ExtractionError("PDF kosong (0 halaman)")
                pages = [p.extract_text() or "" for p in pdf.pages]
        except ExtractionError:
            raise
        except Exception as e:
            if "password" in str(e).lower() or "encrypt" in str(e).lower():
                raise ExtractionError("PDF terenkripsi (terkunci password)") from e
            if not pages:
                raise ExtractionError("PDF rusak / format tidak didukung") from e
    if not any(p.strip() for p in pages):
        if not ocr:
            raise ExtractionError("Tidak ada teks (PDF hasil scan?). Aktifkan OCR.")
        pages = _ocr(path)
    return [normalize(p) for p in pages]


def extract_batch(items, ocr: bool = False, progress=None, workers: int = MAX_WORKERS) -> None:
    """Ekstraksi batch dengan bounded concurrency agar ribuan PDF cepat tanpa kehabisan memori."""
    total = len(items)
    if total == 0:
        return

    def _one(it):
        try:
            it.pages = extract_pages(it.src, ocr)
            it.error = ""
        except Exception as e:
            it.pages = None
            it.error = str(e)

    if total == 1 or ocr:  # OCR memakai banyak CPU per proses
        w = 2 if ocr else 1
    else:
        w = min(workers, total)

    done = 0
    with ThreadPoolExecutor(max_workers=w) as ex:
        futs = [ex.submit(_one, it) for it in items]
        for _ in as_completed(futs):
            done += 1
            if progress:
                progress(done, total)


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
        raise ExtractionError(f"OCR gagal (Tesseract belum terpasang?): {e}") from e
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(pytesseract.image_to_string(img, lang=lang))
    if not any(t.strip() for t in out):
        raise ExtractionError("OCR selesai tetapi tidak menemukan teks")
    return out
