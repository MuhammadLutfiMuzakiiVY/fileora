"""Input ZIP/folder/dokumen dan output ZIP. Ekstraksi aman dari zip-slip dan zip-bomb."""
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fileora.extractor import SUPPORTED_EXTS

MAX_ENTRY_BYTES = 200 * (1 << 20)   # 200 MB per file
MAX_TOTAL_BYTES = 2048 * (1 << 20)  # 2 GB total ekstraksi
MAX_RATIO = 100                     # rasio kompresi maks (anti zip-bomb)


@dataclass
class ArchiveStats:
    name: str
    size_bytes: int
    pdf_count: int
    skipped_count: int


def is_supported(path_or_name: str | Path) -> bool:
    return Path(path_or_name).suffix.lower() in SUPPORTED_EXTS


def extract_zip(zip_path, dest, include_other: bool = False) -> tuple[list[Path], list[str], ArchiveStats]:
    """Ekstrak dokumen yang didukung dari zip_path ke dest (anti zip-slip & anti zip-bomb)."""
    zp = Path(zip_path)
    dest = Path(dest).resolve()
    files, skipped = [], []
    total_written = 0
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or "__MACOSX" in name:
                continue
            supported = is_supported(name)
            if not supported and not include_other:
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(dest):  # zip-slip: ../ atau path absolut
                logging.warning("Lewati entri ZIP mencurigakan (zip-slip): %s", name)
                skipped.append(name)
                continue
            comp = max(1, info.compress_size)
            if info.file_size > MAX_ENTRY_BYTES or (info.file_size > (1 << 20) and info.file_size / comp > MAX_RATIO):
                logging.warning("Lewati entri ZIP melebihi batas ukuran/rasio (zip-bomb): %s", name)
                skipped.append(name)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as s, open(target, "xb") as d:
                    while chunk := s.read(1 << 20):
                        written += len(chunk)
                        total_written += len(chunk)
                        if written > MAX_ENTRY_BYTES or total_written > MAX_TOTAL_BYTES:
                            raise OSError("Melebihi batas ekstraksi aman (zip-bomb)")
                        d.write(chunk)
                if supported:
                    files.append(target)
            except (OSError, zipfile.BadZipFile, RuntimeError) as e:
                target.unlink(missing_ok=True)
                logging.warning("Gagal ekstrak %s: %s", name, e)
                skipped.append(name)
    stats = ArchiveStats(zp.name, zp.stat().st_size if zp.exists() else 0, len(files), len(skipped))
    return files, skipped, stats


def _rel(folder: Path, base: Path) -> str:
    r = folder.relative_to(base).as_posix()
    return "" if r == "." else r


def collect(paths, tmp_root) -> tuple[list[tuple[Path, str, bool]], list[str], list[ArchiveStats]]:
    """ZIP / folder / dokumen -> ([(file, subfolder relatif, dari_zip)], [dilewati], [info_zip])."""
    out, skipped, archives = [], [], []
    for p in map(Path, paths):
        from_zip = False
        if p.is_dir():
            base, files = p, sorted(f for f in p.rglob("*") if is_supported(f) and f.is_file())
        elif p.suffix.lower() == ".zip":
            base, from_zip = Path(tempfile.mkdtemp(dir=tmp_root)).resolve(), True
            try:
                files, bad, st = extract_zip(p, base)
                archives.append(st)
            except (zipfile.BadZipFile, OSError):
                files, bad = [], [p.name]
            skipped += bad
        elif is_supported(p) and p.is_file():
            base, files = p.parent, [p]
        else:
            skipped.append(p.name)
            continue
        out += [(f, _rel(f.parent, base), from_zip) for f in files]
    return out, skipped, archives


def write_zip(entries, target, progress=None) -> None:
    """entries: [(src, arcname)]. Tidak pernah menimpa file yang sudah ada."""
    entries = list(entries)
    zf = zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED)
    try:
        with zf:
            for i, (src, arc) in enumerate(entries, 1):
                zf.write(src, arc)
                if progress:
                    progress(i, len(entries))
    except BaseException:
        Path(target).unlink(missing_ok=True)
        raise
