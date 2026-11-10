"""Log aplikasi, log undo, riwayat pemrosesan, dan ekspor laporan CSV/Excel."""
import csv
import json
import logging
import os
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Fileora"
UNDO_FILE = APP_DIR / "undo.json"
HISTORY_FILE = APP_DIR / "history.json"


def setup() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=APP_DIR / "fileora.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")


def save_undo(log: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    UNDO_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=1), "utf-8")
    logging.info("Operasi %s: %s", log["mode"], json.dumps(log, ensure_ascii=False))


def load_undo() -> dict | None:
    return json.loads(UNDO_FILE.read_text("utf-8")) if UNDO_FILE.exists() else None


def clear_undo() -> None:
    UNDO_FILE.unlink(missing_ok=True)


def append_history(entry: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    hist = load_history()
    hist.insert(0, {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), **entry})
    HISTORY_FILE.write_text(json.dumps(hist[:100], ensure_ascii=False, indent=1), "utf-8")


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text("utf-8")) if HISTORY_FILE.exists() else []
    except Exception:
        return []


def export_report(items, fields: list[str], path) -> None:
    header = ["Nama lama", "Nama baru", *(f.replace("_", " ").upper() for f in fields),
              "Confidence (%)", "Status", "Catatan"]
    rows = [[str(Path(it.rel_dir, it.old_name)), it.new_name, *(it.values.get(f, "") for f in fields),
             it.confidence, it.status, "; ".join(it.notes)] for it in items]
    if str(path).lower().endswith(".xlsx"):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Fileora"
        for r in [header, *rows]:
            ws.append(r)
        wb.save(path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows([header, *rows])
