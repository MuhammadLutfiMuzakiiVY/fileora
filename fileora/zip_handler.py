"""Input ZIP/folder/PDF dan output ZIP. Ekstraksi aman dari zip-slip."""
import logging
import tempfile
import zipfile
from pathlib import Path


def extract_zip(zip_path, dest) -> tuple[list[Path], list[str]]:
    """Ekstrak hanya PDF ke dest. Return (pdf, nama yang dilewati)."""
    dest = Path(dest).resolve()
    files, skipped = [], []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or not name.lower().endswith(".pdf") or "__MACOSX" in name:
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(dest):  # zip-slip: ../ atau path absolut
                logging.warning("Lewati entri ZIP mencurigakan: %s", name)
                skipped.append(name)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as s, open(target, "xb") as d:
                    while chunk := s.read(1 << 20):
                        d.write(chunk)
                files.append(target)
            except (OSError, zipfile.BadZipFile) as e:
                logging.warning("Gagal ekstrak %s: %s", name, e)
                skipped.append(name)
    return files, skipped


def _rel(folder: Path, base: Path) -> str:
    r = folder.relative_to(base).as_posix()
    return "" if r == "." else r


def collect(paths, tmp_root) -> tuple[list[tuple[Path, str, bool]], list[str]]:
    """ZIP / folder / PDF -> ([(pdf, subfolder relatif, dari_zip)], [dilewati])."""
    out, skipped = [], []
    for p in map(Path, paths):
        from_zip = False
        if p.is_dir():
            base, files = p, sorted(f for f in p.rglob("*") if f.suffix.lower() == ".pdf" and f.is_file())
        elif p.suffix.lower() == ".zip":
            base, from_zip = Path(tempfile.mkdtemp(dir=tmp_root)).resolve(), True
            try:
                files, bad = extract_zip(p, base)
            except zipfile.BadZipFile:
                files, bad = [], [p.name]
            skipped += bad
        elif p.suffix.lower() == ".pdf" and p.is_file():
            base, files = p.parent, [p]
        else:
            skipped.append(p.name)
            continue
        out += [(f, _rel(f.parent, base), from_zip) for f in files]
    return out, skipped


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
        Path(target).unlink(missing_ok=True)  # hanya ZIP buatan kita sendiri
        raise

