"""Ekstraksi teks multi-format: PDF, Word (.docx/.doc), RTF, ODT, dan Gambar (.jpg/.png/.tiff/.bmp)."""
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import pymupdf

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b\ufeff]")
_SPACES = re.compile(r"[ \t\u00a0]+")
MAX_WORKERS = min(8, (os.cpu_count() or 4))

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".rtf", ".odt"}
SUPPORTED_EXTS = DOC_EXTS | IMAGE_EXTS

FORMAT_LABELS = {
    ".pdf": "Surat PDF",
    ".doc": "Surat Microsoft Word lama",
    ".docx": "Surat Microsoft Word",
    ".rtf": "Rich Text Format",
    ".odt": "OpenDocument Text",
    ".jpg": "Foto atau scan surat",
    ".jpeg": "Foto atau scan surat",
    ".png": "Scan surat",
    ".tiff": "Dokumen hasil scan",
    ".tif": "Dokumen hasil scan",
    ".bmp": "Gambar surat",
}


class ExtractionError(Exception):
    pass


def normalize(text: str) -> str:
    """Buang karakter kontrol, rapikan spasi ganda, trim, buang baris kosong."""
    text = _CTRL.sub(" ", text.replace("\r", "\n"))
    lines = (_SPACES.sub(" ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def extract_pages(path, ocr: bool = False) -> list[str]:
    """Ekstrak teks per halaman dari PDF, Word (.docx/.doc), RTF, ODT, atau gambar scan."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return _extract_docx(p)
    if ext == ".odt":
        return _extract_odt(p)
    if ext == ".rtf":
        return _extract_rtf(p)
    if ext == ".doc":
        return _extract_doc(p)
    if ext in IMAGE_EXTS:
        if not ocr:
            raise ExtractionError(f"File gambar ({ext}) membutuhkan OCR. Aktifkan OCR.")
        return [normalize(t) for t in _ocr_image(p)]
    return _extract_pdf(p, ocr)


def _extract_pdf(path: Path, ocr: bool) -> list[str]:
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
        pages = _ocr_pdf(path)
    return [normalize(p) for p in pages]


def _xml_lines(xml_bytes: bytes, para_tags: set[str], break_tags: set[str] = frozenset()) -> list[str]:
    """Ambil teks dari XML (.docx / .odt) per paragraf/baris tabel."""
    root = ET.fromstring(xml_bytes)
    lines: list[str] = []
    buf: list[str] = []

    def walk(el):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in break_tags:
            buf.append("\n")
        if el.text:
            buf.append(el.text)
        for child in el:
            walk(child)
        if tag in para_tags:
            lines.append("".join(buf))
            buf.clear()
        if el.tail:
            buf.append(el.tail)

    walk(root)
    if buf:
        lines.append("".join(buf))
    return lines


def _extract_docx(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            xml_bytes = zf.read("word/document.xml")
        raw = "\n".join(_xml_lines(xml_bytes, {"p"}, {"br", "cr"}))
    except Exception as e:
        raise ExtractionError(f"File .docx rusak / tidak bisa dibuka: {e}") from e
    pages = [normalize(part) for part in raw.split("\f") if normalize(part)]
    if not pages:
        raise ExtractionError("Dokumen .docx kosong")
    return pages


def _extract_odt(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            xml_bytes = zf.read("content.xml")
        raw = "\n".join(_xml_lines(xml_bytes, {"p", "h"}, {"line-break"}))
    except Exception as e:
        raise ExtractionError(f"File .odt rusak / tidak bisa dibuka: {e}") from e
    text = normalize(raw)
    if not text:
        raise ExtractionError("Dokumen .odt kosong")
    return [text]


def _extract_rtf(path: Path) -> list[str]:
    try:
        data = path.read_bytes().decode("latin-1", errors="ignore")
    except Exception as e:
        raise ExtractionError(f"File .rtf tidak bisa dibaca: {e}") from e
    if not data.lstrip().startswith("{\\rtf"):
        raise ExtractionError("File .rtf tidak valid")
    # Buang grup metadata RTF (fonttbl, colortbl, stylesheet, info, pict)
    s = re.sub(r"\{\\\*?[^{}]*(?:fonttbl|colortbl|stylesheet|info|pict|header|footer)[^{}]*\}", "", data)
    s = re.sub(r"\\(par|pard|line|row|cell)\b[ ]?", "\n", s)
    s = re.sub(r"\\tab\b[ ]?", " ", s)
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="ignore"), s)
    s = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) & 0xFFFF), s)
    s = re.sub(r"\\[a-zA-Z]+-?\d*[ ]?", "", s)
    s = s.replace("{", "").replace("}", "")
    text = normalize(s)
    if not text:
        raise ExtractionError("Dokumen .rtf kosong")
    return [text]


def _extract_doc(path: Path) -> list[str]:
    """Ekstraksi teks dari Microsoft Word lama (.doc binary / RTF-in-.doc / DOCX-in-.doc)."""
    try:
        raw = path.read_bytes()
    except Exception as e:
        raise ExtractionError(f"File .doc tidak bisa dibaca: {e}") from e
    if raw[:4] == b"PK\x03\x04":
        return _extract_docx(path)
    if raw.lstrip()[:5] == b"{\\rtf":
        return _extract_rtf(path)
    if len(raw) < 512 or raw[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ExtractionError("File .doc rusak / format tidak dikenali")
    # Ekstrak urutan teks UTF-16LE dan ASCII yang dapat dibaca dari stream WordDocument
    ascii_runs = [m.group(0).decode("cp1252", errors="ignore")
                  for m in re.finditer(rb"[\x20-\x7e\r\n]{4,}", raw)]
    utf16_runs = [m.group(0).decode("utf-16le", errors="ignore")
                  for m in re.finditer(rb"(?:[\x20-\x7e\r\n]\x00){4,}", raw)]
    noise = {"Root Entry", "WordDocument", "CompObj", "SummaryInformation",
             "DocumentSummaryInformation", "1Table", "0Table", "Normal.dotm", "MSWordDoc", "Word.Document"}
    lines = []
    for chunk in (*utf16_runs, *ascii_runs):
        for ln in chunk.replace("\r", "\n").split("\n"):
            ln = ln.strip()
            if len(ln) >= 3 and ln not in noise and not ln.startswith(("HYPERLINK", "EMBED", "_PID_")):
                lines.append(ln)
    text = normalize("\n".join(lines))
    if not text:
        raise ExtractionError("Dokumen .doc kosong / tidak memiliki teks yang dapat dibaca")
    return [text]


def extract_batch(items, ocr: bool = False, progress=None, workers: int = MAX_WORKERS) -> None:
    """Ekstraksi batch dengan bounded concurrency agar ribuan dokumen cepat tanpa kehabisan memori."""
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

    w = (2 if ocr else 1) if (total == 1 or ocr) else min(workers, total)
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


def _ocr_pdf(path: Path) -> list[str]:
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


def _ocr_image(path: Path) -> list[str]:
    try:
        import pytesseract
        from PIL import Image, ImageSequence
        lang = _tesseract_lang()
    except Exception as e:
        raise ExtractionError(f"OCR gagal (Tesseract belum terpasang?): {e}") from e
    out = []
    try:
        with Image.open(path) as im:
            for frame in ImageSequence.Iterator(im):
                out.append(pytesseract.image_to_string(frame.convert("RGB"), lang=lang))
    except Exception as e:
        raise ExtractionError(f"Gambar rusak / tidak bisa dibuka: {e}") from e
    if not any(t.strip() for t in out):
        raise ExtractionError("OCR selesai tetapi tidak menemukan teks pada gambar")
    return out
