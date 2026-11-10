"""Rencana nama baru (template, sanitasi Windows, duplikat, panjang) + eksekusi & undo."""
import os
import re
import shutil
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
    rel_dir: str = ""          # subfolder relatif untuk output
    in_zip: bool = False
    pages: list[str] | None = None  # None = belum dibaca
    error: str = ""
    manual: str = ""           # nama hasil edit manual di preview
    values: dict[str, str] = field(default_factory=dict)
    new_name: str = ""
    status: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def old_name(self) -> str:
        return self.src.name


def sanitize(stem: str) -> str:
    s = " ".join(_FORBIDDEN.sub(" ", stem).split()).rstrip(". ")
    return "_" + s if s.split(".")[0].strip().upper() in _RESERVED else s


def apply_case(v: str, case: str) -> str:
    return {"upper": v.upper(), "lower": v.lower(), "title": v.title()}.get(case, v)


def template_fields(preset) -> list[str]:
    return re.findall(r"{(\w+)}", preset.template)


def build_stem(values: dict[str, str], preset) -> tuple[str, bool]:
    """Return (nama tanpa ekstensi, dipotong?). Jika kepanjangan, field terpanjang yang dipotong."""
    tpl = preset.template.replace("}_{", "}" + preset.separator + "{")
    vals = {k: apply_case(v, preset.case) for k, v in values.items()}
    room = preset.max_len - len(EXT)
    stem = sanitize(tpl.format_map(vals))
    if len(stem) <= room:
        return stem, False
    k = max(template_fields(preset) or vals, key=lambda k: len(vals.get(k, "")))
    vals[k] = vals[k][:max(1, len(vals[k]) - (len(stem) - room))].rstrip(_TRIM)
    return sanitize(tpl.format_map(vals))[:room].rstrip(_TRIM), True


def _propose(it: Item, preset) -> str | None:
    it.status, it.notes, it.values = OK, [], {}
    notes, missing = [], []
    if it.pages:
        it.values, notes, missing = extract_fields(it.pages, preset.sources)
    manual = sanitize(it.manual[:-len(EXT)] if it.manual.lower().endswith(EXT) else it.manual)
    manual = manual[:preset.max_len - len(EXT)].rstrip(_TRIM)
    if manual:
        it.notes = [*notes, "Nama diedit manual"]
        return manual
    if it.error or it.pages is None:
        it.status, it.notes = FAIL, [it.error or "Belum dibaca"]
        return None
    it.notes += notes
    missing = [f for f in template_fields(preset) if f not in it.values]
    if missing:
        it.status, it.notes = FAIL, [f"Perlu cek: field tidak ditemukan ({', '.join(missing)})", *notes]
        return None
    stem, cut = build_stem(it.values, preset)
    if cut:
        it.notes.append(f"Nama dipotong (maks {preset.max_len} karakter)")
    if not stem:
        it.status, it.notes = FAIL, ["Nama hasil kosong"]
        return None
    if it.notes:
        it.status = WARN
    return stem


def _unique(stem: str, taken: set[str], max_len: int, ext: str = EXT) -> tuple[str, bool]:
    name, n = stem + ext, 1
    while name.lower() in taken:
        n += 1
        suf = f"_{n}"
        name = stem[:max_len - len(ext) - len(suf)].rstrip(_TRIM) + suf + ext
    taken.add(name.lower())
    return name, n > 1


def plan(items: list[Item], preset, inplace: bool = False) -> None:
    """Isi new_name/status/notes tiap item. Gagal -> nama lama dipertahankan."""
    taken: dict[str, set[str]] = {}
    if inplace:  # file lain di folder yg sama (di luar batch) tidak boleh tertimpa
        batch = {str(it.src).lower() for it in items}
        for d in {it.src.parent for it in items}:
            taken[str(d).lower()] = {p.name.lower() for p in d.iterdir() if str(p).lower() not in batch}

    def key(it):
        return str(it.src.parent).lower() if inplace else it.rel_dir.lower()

    stems = [_propose(it, preset) for it in items]
    for it, stem in zip(items, stems):  # yang gagal dipesan dulu dgn nama lamanya
        if stem is None:
            it.new_name, _ = _unique(it.src.stem, taken.setdefault(key(it), set()), 10**6, it.src.suffix)
    for it, stem in zip(items, stems):
        if stem is not None:
            it.new_name, dup = _unique(stem, taken.setdefault(key(it), set()), preset.max_len)
            if dup:
                it.status = WARN
                it.notes.append("Nama kembar, diberi akhiran")


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


def execute(items: list[Item], mode: str, target=None, progress=None) -> dict:
    """mode: 'zip' (target=file .zip), 'folder' (target=folder baru), 'inplace'. Return log undo."""
    if mode == "zip":
        write_zip(((it.src, str(PurePosixPath(it.rel_dir, it.new_name))) for it in items), target, progress)
        return {"mode": "zip", "created": [str(target)]}
    if mode == "folder":
        root = Path(target)
        root.mkdir(parents=True, exist_ok=False)
        created = []
        for i, it in enumerate(items, 1):
            dst = root / it.rel_dir / it.new_name
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
    for f in log["created"]:  # hanya hapus file buatan Fileora; file asli tidak disentuh
        Path(f).unlink(missing_ok=True)
    if log["mode"] == "folder":
        dirs = {Path(f).parent for f in log["created"]} | {Path(log["root"])}
        for d in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
            try:
                d.rmdir()  # hanya jika kosong
            except OSError:
                pass
