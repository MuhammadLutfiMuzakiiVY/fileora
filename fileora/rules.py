"""Aturan ekstraksi field dari teks PDF (label, baris-setelah-kata, regex)."""
import re
from dataclasses import dataclass

MODES = {"label": "Label penanda", "next_line": "Baris setelah kata", "regex": "Regex (lanjutan)"}


@dataclass
class Source:
    """Satu sumber nilai untuk sebuah field. Beberapa Source dgn field sama = sumber cadangan/pembanding."""
    field: str
    mode: str
    pattern: str
    page: int = 0  # 1-based; 0 = semua halaman


def _flex(text: str) -> str:
    """Escape teks, spasi/titik dua jadi fleksibel ('NPP :' cocok dgn 'NPP:' / 'NPP  :')."""
    return r"\s*".join(re.escape(t) for t in text.replace(":", " : ").split())


def find(src: Source, pages: list[str]) -> str | None:
    if src.page == 0:
        text = "\n".join(pages)
    else:
        text = pages[src.page - 1] if 0 < src.page <= len(pages) else ""
    pat = src.pattern.strip()
    if not text or not pat:
        return None
    val = None
    if src.mode == "next_line":
        lines, anchor = text.split("\n"), pat.lower()
        low = [ln.lower() for ln in lines[:-1]]
        idx = next((i for i, ln in enumerate(low) if ln == anchor), None)
        if idx is None:
            idx = next((i for i, ln in enumerate(low) if anchor in ln), None)
        val = None if idx is None else lines[idx + 1]
    else:
        if src.mode == "label":
            colon = r"\s*:\s*" if pat.endswith(":") else r"\s*:?\s*"
            pat = rf"(?<!\w){_flex(pat.rstrip(':'))}(?!\w){colon}([^\n]+)"
        try:
            m = re.search(pat, text, re.I | re.M)
        except re.error:
            return None
        val = m and (m.group(1) if m.re.groups else m.group(0))
    val = " ".join((val or "").split()).strip(" :")
    return val or None


def extract_fields(pages: list[str], sources: list[Source]) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (nilai per field, catatan peringatan, field yang tidak ditemukan)."""
    values, notes, missing = {}, [], []
    for f in dict.fromkeys(s.field for s in sources):
        found = [v for s in sources if s.field == f and (v := find(s, pages))]
        if not found:
            missing.append(f)
            continue
        values[f] = max(found, key=len)  # seri -> sumber pertama (prioritas)
        if len({v.upper() for v in found}) > 1:
            notes.append(f"{f}: sumber berbeda ({' | '.join(dict.fromkeys(found))}), dipakai yang terpanjang")
    return values, notes, missing


def learn_source(field: str, page_text: str, page_no: int, start: int, end: int) -> Source | None:
    """Buat aturan dari teks yang disorot pengguna pada satu halaman."""
    while start < end and page_text[start].isspace():
        start += 1
    while end > start and page_text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    line_start = page_text.rfind("\n", 0, start) + 1
    line_end = page_text.find("\n", end)
    prefix = page_text[line_start:start].strip()
    suffix = page_text[end:len(page_text) if line_end < 0 else line_end].strip()
    if not prefix:
        prev = page_text[:line_start].rstrip("\n").rsplit("\n", 1)[-1].strip()
        return Source(field, "next_line", prev, page_no) if prev else None
    if not suffix:
        label = " ".join(prefix.split())
        if label.endswith(":"):
            label = label[:-1].rstrip() + " :"
        return Source(field, "label", label, page_no)
    # Nilai di tengah baris -> regex yg berhenti sebelum sisa baris
    sel = page_text[start:end]
    grp = r"(\S+)" if not any(c.isspace() for c in sel) else rf"(.+?)(?=\s*{re.escape(suffix.split()[0])})"
    return Source(field, "regex", rf"(?<!\w){_flex(prefix)}\s*{grp}", page_no)

