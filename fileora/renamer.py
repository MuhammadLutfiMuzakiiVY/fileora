"""Rencana nama baru (template, sanitasi Windows, duplikat, panjang, confidence) + eksekusi & undo."""
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from fileora.rules import extract_fields
from fileora.zip_handler import write_zip

OK, WARN, FAIL = "OK", "Peringatan", "Gagal"
EXT = ".pdf"
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"{d}{i}" for d in ("COM", "LPT") for i in range(1, 10))}
_TRIM = " .-_"


@dataclass
class Item:
    src: Path
    rel_dir: str = ""               # subfolder relatif untuk output
    in_zip: bool = False
    pages: list[str] | None = None  # None = belum dibaca
    error: str = ""
    manual: str = ""                # nama hasil edit manual di preview
    values: dict[str, str] = field(default_factory=dict)
    new_name: str = ""
    status: str = ""
    confidence: int = 0             # 0..100 (%)
    notes: list[str] = field(default_factory=list)

    @property
    def old_name(self) -> str:
        return self.src.name


def sanitize(stem: str) -> str:
    s = " ".join(_FORBIDDEN.sub(" ", stem).split()).rstrip(". ")
    return "_" + s if s.split(".")[0].strip().upper() in _RESERVED else s


def apply_case(v: str, case: str) -> str:
    return {"upper": v.upper(), "lower": v.lower(), "title": v.title()}.get(case, v)


def format_val(v: str, preset) -> str:
    s = sanitize(apply_case(v, preset.case))
    repl = getattr(preset, "space_repl", " ")
    if repl and repl != " ":
        s = re.sub(r"\s+", repl, s)
    return s


def template_fields(preset) -> list[str]:
    return re.findall(r"{(\w+)}", preset.template)


def build_stem(values: dict[str, str], preset, ext: str = EXT) -> tuple[str, bool]:
    """Return (nama tanpa ekstensi, dipotong?). Jika kepanjangan, field terpanjang yang dipotong."""
    tpl = preset.template.replace("}_{", "}" + preset.separator + "{")
    vals = {k: format_val(v, preset) for k, v in values.items()}
    room = preset.max_len - len(ext or EXT)
    stem = sanitize(tpl.format_map(vals))
    if len(stem) <= room:
        return stem, False
    k = max(template_fields(preset) or vals, key=lambda k: len(vals.get(k, "")))
    vals[k] = vals[k][:max(1, len(vals[k]) - (len(stem) - room))].rstrip(_TRIM)
    return sanitize(tpl.format_map(vals))[:room].rstrip(_TRIM), True


def _propose(it: Item, preset) -> str | None:
    ext = it.src.suffix or EXT
    it.status, it.notes, it.values, it.confidence = OK, [], {}, 100
    notes, missing = [], []
    if it.pages:
        it.values, notes, missing = extract_fields(it.pages, preset.sources)
    raw_manual = it.manual
    if raw_manual:
        for sfx in (ext, EXT):
            if sfx and raw_manual.lower().endswith(sfx.lower()):
                raw_manual = raw_manual[:-len(sfx)]
                break
    manual = sanitize(raw_manual)[:preset.max_len - len(ext)].rstrip(_TRIM)
    if manual:
        it.notes = [*notes, "Nama diedit manual"]
        it.confidence = 100
        return manual
    if it.error or it.pages is None:
        it.status, it.notes, it.confidence = FAIL, [it.error or "Belum dibaca"], 0
        return None
    it.notes += notes
    req = template_fields(preset)
    missing = [f for f in req if f not in it.values]
    if missing:
        found_ratio = (len(req) - len(missing)) / max(1, len(req))
        it.confidence = int(found_ratio * 40)
        it.status, it.notes = FAIL, [f"Perlu cek: field tidak ditemukan ({', '.join(missing)})", *notes]
        return None
    for f, v in it.values.items():
        if len(v.strip()) < 2:
            it.notes.append(f"Perlu cek: nilai {f} terlalu pendek («{v}»)")
            it.confidence = min(it.confidence, 60)
    if notes:  # sumber berbeda antar halaman
        it.confidence = min(it.confidence, 80)
    stem, cut = build_stem(it.values, preset, ext=ext)
    if cut:
        it.notes.append(f"Nama dipotong (maks {preset.max_len} karakter)")
        it.confidence = min(it.confidence, 85)
    if not stem:
        it.status, it.notes, it.confidence = FAIL, ["Nama hasil kosong"], 0
        return None
    if it.notes:
        it.status = WARN
    return stem


def _unique(stem: str, taken: set[str], max_len: int, ext: str = EXT) -> tuple[str, bool]:
    ext = ext or EXT
    name, n = stem + ext, 1
    while name.lower() in taken:
        n += 1
        suf = f"_{n}"
        name = stem[:max_len - len(ext) - len(suf)].rstrip(_TRIM) + suf + ext
    taken.add(name.lower())
    return name, n > 1


def plan(items: list[Item], preset, inplace: bool = False) -> None:
    """Isi new_name/status/notes/confidence tiap item. Gagal -> nama lama dipertahankan."""
    taken: dict[str, set[str]] = {}
    flat = getattr(preset, "flat_zip", False) and not inplace
    if inplace:
        batch = {str(it.src).lower() for it in items}
        for d in {it.src.parent for it in items}:
            taken[str(d).lower()] = {p.name.lower() for p in d.iterdir() if str(p).lower() not in batch}

    def key(it):
        if inplace:
            return str(it.src.parent).lower()
        return "" if flat else it.rel_dir.lower()

    stems = [_propose(it, preset) for it in items]
    npp_counts = Counter(it.values.get("npp", "").upper() for it in items if it.values.get("npp"))
    for it, stem in zip(items, stems):
        if stem is None:
            it.new_name, _ = _unique(it.src.stem, taken.setdefault(key(it), set()), 10**6, it.src.suffix or EXT)
    for it, stem in zip(items, stems):
        if stem is not None:
            it.new_name, dup = _unique(stem, taken.setdefault(key(it), set()), preset.max_len, it.src.suffix or EXT)
            npp = it.values.get("npp", "").upper()
            if dup:
                it.status = WARN
                it.confidence = min(it.confidence, 75)
                it.notes.append("Nama kembar, diberi akhiran")
            elif npp and npp_counts[npp] > 1:
                it.status = WARN
                it.confidence = min(it.confidence, 80)
                it.notes.append(f"NPP duplikat ({npp_counts[npp]} dokumen)")


def safe_moves(pairs: list[tuple[Path, Path]]) -> None:
    """Rename 2 tahap (src->tmp->dst) supaya tukar nama aman; rollback total jika ada yang gagal."""
    done: list[tuple[Path, Path]] = []
    try:
        staged = []
        for i, (src, dst) in enumerate(pairs):
            tmp = src.with_name(f".fileora_{os.getpid()}_{i}.tmp")
            os.rename(src, tmp)
            done.append((src, tmp))
            staged.append((tmp, dst))
        for tmp, dst in staged:
            if dst.exists():
                raise FileExistsError(f"Sudah ada: {dst}")
            os.rename(tmp, dst)
            done.append((tmp, dst))
    except Exception:
        for a, b in reversed(done):
            os.rename(b, a)
        raise


def execute(items: list[Item], mode: str, target=None, progress=None, flat: bool = False) -> dict:
    """mode: 'zip' (target=file .zip), 'folder' (target=folder baru), 'inplace'. Return log undo."""
    if mode == "zip":
        entries = ((it.src, it.new_name if flat else str(PurePosixPath(it.rel_dir, it.new_name))) for it in items)
        write_zip(entries, target, progress)
        return {"mode": "zip", "created": [str(target)]}
    if mode == "folder":
        root = Path(target)
        root.mkdir(parents=True, exist_ok=False)
        created = []
        for i, it in enumerate(items, 1):
            dst = (root / it.new_name) if flat else (root / it.rel_dir / it.new_name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(it.src, dst)
            created.append(str(dst))
            if progress:
                progress(i, len(items))
        return {"mode": "folder", "root": str(root), "created": created}
    pairs = [(it.src, it.src.with_name(it.new_name)) for it in items if it.src.name != it.new_name]
    safe_moves(pairs)
    return {"mode": "inplace", "moves": [[str(a), str(b)] for a, b in pairs]}


def undo(log: dict) -> None:
    if log["mode"] == "inplace":
        safe_moves([(Path(b), Path(a)) for a, b in log["moves"]])
        return
    for f in log["created"]:
        Path(f).unlink(missing_ok=True)
    if log["mode"] == "folder":
        dirs = {Path(f).parent for f in log["created"]} | {Path(log["root"])}
        for d in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
