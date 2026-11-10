"""Preset aturan: bawaan (BPJS Ketenagakerjaan) + buatan pengguna di %APPDATA%\\Fileora\\presets.json."""
import json
from dataclasses import asdict, dataclass, field

from fileora.logger import APP_DIR
from fileora.rules import Source

FILE = APP_DIR / "presets.json"


@dataclass
class Preset:
    name: str
    sources: list[Source] = field(default_factory=list)
    template: str = "{nama_perusahaan}_{npp}"
    separator: str = "_"
    case: str = "asis"  # asis | upper | lower | title
    max_len: int = 150

    @property
    def fields(self) -> list[str]:
        return list(dict.fromkeys(s.field for s in self.sources))

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        return cls(**{**d, "sources": [Source(**s) for s in d.get("sources", [])]})


BPJS = Preset("BPJS Ketenagakerjaan", [
    # Halaman 1 diprioritaskan (nama di hal. 2 bisa terpotong); hal. 2 jadi pembanding/cadangan.
    Source("nama_perusahaan", "next_line", "Pimpinan", 1),
    Source("nama_perusahaan", "label", "Nama Perusahaan :", 2),
    Source("npp", "label", "NPP :", 1),
    Source("npp", "regex", r"NPP\s*/\s*Divisi\s*:\s*([A-Z0-9]+)", 2),
])


def _user() -> dict:
    return json.loads(FILE.read_text("utf-8")) if FILE.exists() else {}


def _write(data: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def load_all() -> dict[str, Preset]:
    return {BPJS.name: BPJS, **{n: Preset.from_dict(d) for n, d in _user().items()}}


def save(p: Preset) -> None:
    _write({**_user(), p.name: asdict(p)})


def delete(name: str) -> bool:
    data = _user()
    if data.pop(name, None) is None:
        return False
    _write(data)
    return True

