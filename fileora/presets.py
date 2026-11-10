"""Preset aturan: bawaan (BPJS Ketenagakerjaan & NPP_Nama-Perusahaan) + buatan pengguna di %APPDATA%\\Fileora\\presets.json."""
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
    case: str = "asis"      # asis | upper | lower | title
    max_len: int = 150
    space_repl: str = " "   # pengganti spasi di dalam nilai field (mis. " " atau "-")
    flat_zip: bool = False  # True = abaikan subfolder saat ekspor ZIP/folder

    @property
    def fields(self) -> list[str]:
        return list(dict.fromkeys(s.field for s in self.sources))

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "sources"}
        return cls(**known, sources=[Source(**s) for s in d.get("sources", [])])


_DEFAULT_SOURCES = [
    # Halaman 1 diprioritaskan (nama di hal. 2 bisa terpotong); hal. 2 jadi pembanding/cadangan.
    Source("nama_perusahaan", "next_line", "Pimpinan", 1),
    Source("nama_perusahaan", "label", "Nama Perusahaan :", 2),
    Source("nama_perusahaan", "label", "Nama Perusahaan :", 0),
    Source("nama_perusahaan", "label", "Perusahaan :", 0),
    Source("npp", "label", "NPP :", 1),
    Source("npp", "regex", r"NPP\s*/\s*Divisi\s*:\s*([A-Z0-9-]+)", 2),
    Source("npp", "regex", r"(?:NPP|No\.?\s*NPP)\s*[:=-]\s*([A-Z0-9-]+)", 0),
]

BPJS = Preset("BPJS Ketenagakerjaan (Nama_NPP)", list(_DEFAULT_SOURCES[:2] + _DEFAULT_SOURCES[4:6]),
              template="{nama_perusahaan}_{npp}", separator="_", case="asis", space_repl=" ")

NPP_FIRST = Preset("Standar NPP_Nama-Perusahaan", list(_DEFAULT_SOURCES),
                   template="{npp}_{nama_perusahaan}", separator="_", case="title", space_repl="-")

BUILTINS = {BPJS.name: BPJS, NPP_FIRST.name: NPP_FIRST, "BPJS Ketenagakerjaan": BPJS}


def _user() -> dict:
    return json.loads(FILE.read_text("utf-8")) if FILE.exists() else {}


def _write(data: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def load_all() -> dict[str, Preset]:
    return {BPJS.name: BPJS, NPP_FIRST.name: NPP_FIRST, **{n: Preset.from_dict(d) for n, d in _user().items()}}


def save(p: Preset) -> None:
    _write({**_user(), p.name: asdict(p)})


def delete(name: str) -> bool:
    data = _user()
    if data.pop(name, None) is None:
        return False
    _write(data)
    return True
