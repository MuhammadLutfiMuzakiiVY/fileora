"""Antarmuka PySide6 Fileora — PDF Bulk Renamer."""
import logging
import os
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from fileora import __version__, extractor, logger, presets, renamer, zip_handler
from fileora.renamer import FAIL, OK, WARN, Item
from fileora.rules import MODES, Source, extract_fields, find, learn_source

COLORS = {OK: "#2e7d32", WARN: "#f9a825", FAIL: "#c62828"}
OUT_MODES = {"zip": "ZIP baru", "folder": "Folder baru", "inplace": "Rename di tempat"}
CASES = {"asis": "Apa adanya", "upper": "HURUF BESAR", "lower": "huruf kecil", "title": "Title Case"}
SPACES = {" ": "Pertahankan spasi ( )", "-": "Ganti strip (-)", "_": "Ganti garis bawah (_)"}


def resource(rel: str) -> str:
    return str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent), rel))


def field_key(text: str) -> str:
    return re.sub(r"\W+", "_", text.strip().lower()).strip("_")


def fmt_bytes(n: int) -> str:
    return f"{n / (1 << 20):.2f} MB" if n >= (1 << 20) else f"{n / 1024:.1f} KB"


def row(*widgets, stretch=None) -> QHBoxLayout:
    lay = QHBoxLayout()
    for w in widgets:
        lay.addWidget(w, 1 if w is stretch else 0)
    return lay


def test_summary(items, sources) -> str:
    scanned = [it for it in items if it.pages]
    ok = sum(not extract_fields(it.pages, sources)[2] for it in scanned)
    return f"Uji ke semua file: berhasil {ok}/{len(scanned)}"


class Worker(QThread):
    """Menjalankan fn(progress) di thread terpisah agar UI tidak freeze."""
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self.fn, self.result, self.error = fn, None, None

    def run(self):
        try:
            self.result = self.fn(self.progress.emit)
        except Exception as e:
            logging.exception("Proses gagal")
            self.error = e


class HistoryDialog(QDialog):
    """Layar Riwayat Pemrosesan (Processing History)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Riwayat Pemrosesan")
        self.resize(840, 460)
        hist = logger.load_history()
        table = QTableWidget(len(hist), 7)
        table.setHorizontalHeaderLabels(["Waktu", "Arsip / Input", "Total", "Berhasil", "Perlu Cek", "Gagal", "Hasil Output"])
        table.verticalHeader().hide()
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, h in enumerate(hist):
            vals = [h.get("timestamp", ""), h.get("source", ""), str(h.get("total", 0)),
                    str(h.get("ok", 0)), str(h.get("warn", 0)), str(h.get("fail", 0)), h.get("output", "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, c, it)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(table, 1)
        lay.addWidget(bb)


class LearnDialog(QDialog):
    """Sorot teks pada PDF contoh -> aturan label/regex dibuat otomatis."""

    def __init__(self, parent, items, fields, ocr):
        super().__init__(parent)
        self.setWindowTitle("Belajar dari contoh")
        self.resize(780, 640)
        self.items, self.ocr, self.sources = [it for it in items if it.pages], ocr, []
        self.pages, self.starts = [], []
        self.pick = QComboBox()
        self.pick.addItems([it.old_name for it in self.items])
        open_btn = QPushButton("Buka PDF lain…")
        self.text = QPlainTextEdit(readOnly=True)
        self.field = QComboBox(editable=True)
        self.field.addItems(fields or ["nama_perusahaan", "npp"])
        make = QPushButton("Buat aturan dari teks yang disorot")
        self.info = QLabel("1) Pilih field  2) Sorot nilainya di teks (mis. nama perusahaan)  3) Klik tombol.")
        self.info.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay = QVBoxLayout(self)
        lay.addLayout(row(QLabel("PDF contoh:"), self.pick, open_btn, stretch=self.pick))
        lay.addWidget(self.text, 1)
        lay.addLayout(row(QLabel("Field:"), self.field, make, stretch=self.field))
        lay.addWidget(self.info)
        lay.addWidget(bb)
        self.pick.currentIndexChanged.connect(lambda i: i >= 0 and self.show_pages(self.items[i].pages))
        open_btn.clicked.connect(self.open_pdf)
        make.clicked.connect(self.make_rule)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        if self.items:
            self.show_pages(self.items[0].pages)

    def show_pages(self, pages):
        self.pages, self.starts, parts, pos = pages, [], [], 0
        for i, p in enumerate(pages, 1):
            head = f"===== Halaman {i} =====\n"
            self.starts.append(pos + len(head))
            parts.append(f"{head}{p}\n\n")
            pos += len(parts[-1])
        self.text.setPlainText("".join(parts))

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pilih PDF contoh", "", "PDF (*.pdf)")
        if path:
            try:
                self.show_pages(extractor.extract_pages(path, self.ocr))
            except Exception as e:
                QMessageBox.warning(self, "Fileora", str(e))

    def make_rule(self):
        cur, key = self.text.textCursor(), field_key(self.field.currentText())
        start, end = cur.selectionStart(), cur.selectionEnd()
        i = max((n for n, s in enumerate(self.starts) if s <= start), default=None)
        if start == end or not key or i is None or end > self.starts[i] + len(self.pages[i]):
            self.info.setText("Pilih nama field dan sorot nilainya (dalam satu halaman) terlebih dahulu.")
            return
        src = learn_source(key, self.pages[i], i + 1, start - self.starts[i], end - self.starts[i])
        if not src:
            self.info.setText("Tidak bisa membuat aturan dari sorotan ini. Coba sorot nilai lain.")
            return
        self.sources.append(src)
        self.info.setText(f"✔ Aturan '{key}': {MODES[src.mode]} «{src.pattern}» (hal. {src.page}) → "
                          f"«{find(src, self.pages)}». {test_summary(self.items, [src])}")


class RulesDialog(QDialog):
    """Layar Aturan Ekstraksi: sumber field, template nama, opsi, simpan preset."""

    def __init__(self, parent, preset, items, ocr):
        super().__init__(parent)
        self.setWindowTitle("Aturan Ekstraksi")
        self.resize(900, 720)
        self.items, self.ocr, self.saved_name = items, ocr, preset.name
        self.name = QLineEdit(preset.name)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Metode", "Label / kata / regex", "Halaman"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeaderItem(3).setToolTip("Nomor halaman sumber, 0 = semua halaman")
        self.table.setMinimumHeight(220)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.fill(preset.sources)
        btns = [QPushButton(t) for t in ("Tambah", "Hapus", "Naik", "Turun", "Belajar dari contoh…", "Uji")]
        for b, fn in zip(btns, (self.add_row, self.remove_row, lambda: self.move(-1), lambda: self.move(1),
                                self.learn, self.test)):
            b.clicked.connect(fn)
        self.template = QLineEdit(preset.template)
        self.sep = QLineEdit(preset.separator, maxLength=3)
        self.case = QComboBox()
        for k, v in CASES.items():
            self.case.addItem(v, k)
        self.case.setCurrentIndex(list(CASES).index(preset.case))
        self.space = QComboBox()
        for k, v in SPACES.items():
            self.space.addItem(v, k)
        self.space.setCurrentIndex(list(SPACES).index(getattr(preset, "space_repl", " ") if getattr(preset, "space_repl", " ") in SPACES else " "))
        self.flat = QCheckBox("Gabungkan semua PDF di satu level (abaikan subfolder di dalam ZIP)")
        self.flat.setChecked(getattr(preset, "flat_zip", False))
        self.max_len = QSpinBox()
        self.max_len.setRange(20, 240)
        self.max_len.setValue(preset.max_len)
        self.info = QLabel()
        hint = QLabel("Baris dengan field yang sama = sumber cadangan/pembanding (urutan = prioritas). "
                      "Jika hasilnya berbeda, dipakai yang terpanjang dan diberi peringatan.\n"
                      "Template: {nama_field}. Contoh: {npp}_{nama_perusahaan} atau {nama_perusahaan}_{npp}.")
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Nama preset:", self.name)
        form2 = QFormLayout()
        form2.addRow("Template nama file:", self.template)
        form2.addRow("Pemisah antar field:", self.sep)
        form2.addRow("Spasi di dalam teks:", self.space)
        form2.addRow("Format huruf:", self.case)
        form2.addRow("Panjang maks. nama:", self.max_len)
        form2.addRow("Struktur folder:", self.flat)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        save = bb.addButton("Simpan && Pakai", QDialogButtonBox.AcceptRole)
        delete = bb.addButton("Hapus Preset", QDialogButtonBox.DestructiveRole)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.table, 1)
        lay.addLayout(row(*btns))
        lay.addWidget(hint)
        lay.addLayout(form2)
        lay.addWidget(self.info)
        lay.addWidget(bb)
        save.clicked.connect(self.save)
        delete.clicked.connect(self.delete)
        bb.rejected.connect(self.reject)
        self.test()

    def add_row(self, s: Source | None = None):
        s = s or Source("field_baru", "label", "", 0)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(s.field))
        combo = QComboBox()
        for k, v in MODES.items():
            combo.addItem(v, k)
        combo.setCurrentIndex(list(MODES).index(s.mode))
        self.table.setCellWidget(r, 1, combo)
        self.table.setItem(r, 2, QTableWidgetItem(s.pattern))
        spin = QSpinBox()
        spin.setRange(0, 9999)
        spin.setValue(s.page)
        self.table.setCellWidget(r, 3, spin)

    def fill(self, sources):
        self.table.setRowCount(0)
        for s in sources:
            self.add_row(s)

    def remove_row(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move(self, d):
        srcs, r = self.sources(), self.table.currentRow()
        if 0 <= r < len(srcs) and 0 <= r + d < len(srcs):
            srcs[r], srcs[r + d] = srcs[r + d], srcs[r]
            self.fill(srcs)
            self.table.selectRow(r + d)

    def sources(self) -> list[Source]:
        t, out = self.table, []
        for r in range(t.rowCount()):
            key, pat = field_key(t.item(r, 0).text()), t.item(r, 2).text()
            if key and pat.strip():
                out.append(Source(key, t.cellWidget(r, 1).currentData(), pat, t.cellWidget(r, 3).value()))
        return out

    def preset(self) -> presets.Preset:
        return presets.Preset(self.name.text().strip(), self.sources(), self.template.text().strip(),
                              self.sep.text(), self.case.currentData(), self.max_len.value(),
                              self.space.currentData(), self.flat.isChecked())

    def learn(self):
        dlg = LearnDialog(self, self.items, list(dict.fromkeys(s.field for s in self.sources())), self.ocr)
        if dlg.exec() and dlg.sources:
            for s in dlg.sources:
                self.add_row(s)
            self.test()

    def test(self):
        self.info.setText(test_summary(self.items, self.sources()) if any(it.pages for it in self.items)
                          else "Muat file PDF dulu untuk menguji aturan.")

    def save(self):
        p = self.preset()
        bad = [s.pattern for s in p.sources if s.mode == "regex" and not _valid_regex(s.pattern)]
        if not p.name or not p.template or not p.sources or bad:
            QMessageBox.warning(self, "Fileora", f"Lengkapi nama preset, template, dan minimal satu aturan."
                                + (f"\nRegex tidak valid: {bad[0]}" if bad else ""))
            return
        presets.save(p)
        self.saved_name = p.name
        self.accept()

    def delete(self):
        name = self.name.text().strip()
        if QMessageBox.question(self, "Fileora", f"Hapus preset «{name}»?") == QMessageBox.Yes:
            if not presets.delete(name):
                QMessageBox.information(self, "Fileora", "Preset bawaan tidak bisa dihapus.")
                return
            self.reject()


def _valid_regex(p: str) -> bool:
    try:
        re.compile(p)
        return True
    except re.error:
        return False


def _card(title: str) -> tuple[QFrame, QLabel]:
    box = QFrame()
    box.setFrameShape(QFrame.StyledPanel)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 6, 10, 6)
    lbl_title = QLabel(title)
    lbl_title.setStyleSheet("font-size: 11px; color: #757575;")
    lbl_val = QLabel("0")
    lbl_val.setStyleSheet("font-size: 15px; font-weight: bold;")
    lay.addWidget(lbl_title)
    lay.addWidget(lbl_val)
    return box, lbl_val


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Fileora {__version__} — PDF Bulk Renamer")
        self.resize(1260, 760)
        self.setAcceptDrops(True)
        self.settings = QSettings("Fileora", "Fileora")
        self.items: list[Item] = []
        self.archives: list[zip_handler.ArchiveStats] = []
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.first_input: Path | None = None
        self.worker: Worker | None = None

        add_files, add_dir, clear = QPushButton("Tambah ZIP/PDF…"), QPushButton("Tambah Folder…"), QPushButton("Bersihkan")
        self.preset_box = QComboBox(minimumWidth=240)
        rules_btn = QPushButton("Aturan Ekstraksi…")
        hist_btn = QPushButton("Riwayat…")
        self.ocr = QCheckBox("Aktifkan OCR")
        self.ocr.setToolTip("Untuk PDF hasil scan (tanpa teks). Proses jauh lebih lambat; butuh Tesseract.")
        self.ocr.setChecked(self.settings.value("ocr", False, bool))
        self.dark = QCheckBox("Mode gelap")

        # Dashboard metrik (Total, Berhasil, Perlu Cek, Gagal, Info Arsip, Kecepatan & ETA)
        c_tot, self.m_total = _card("Total PDF")
        c_ok, self.m_ok = _card("Berhasil (OK)")
        c_warn, self.m_warn = _card("Perlu Cek (Peringatan)")
        c_fail, self.m_fail = _card("Gagal")
        c_zip, self.m_zip = _card("Info Arsip Input")
        c_spd, self.m_speed = _card("Kecepatan & Sisa Waktu")
        self.m_zip.setText("—")
        self.m_speed.setText("—")

        self.filter = QComboBox()
        self.filter.addItems(["Semua", OK, WARN, FAIL])
        self.search = QLineEdit(placeholderText="Cari nama file / NPP / perusahaan…", clearButtonEnabled=True)
        self.summary = QLabel()
        self.table = QTableWidget()
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        hint = QLabel("Tarik & lepas file ZIP (hingga 100MB+ / 1.000+ PDF), PDF, atau folder ke sini\n"
                      "atau klik “Tambah ZIP/PDF…”", alignment=Qt.AlignCenter)
        hint.setFont(QFont(hint.font().family(), 14))
        self.stack = QStackedWidget()
        self.stack.addWidget(hint)
        self.stack.addWidget(self.table)
        self.mode = QComboBox()
        for k, v in OUT_MODES.items():
            self.mode.addItem(v, k)
        run_btn, undo_btn, report = QPushButton("Rename Semua"), QPushButton("Undo"), QPushButton("Ekspor Laporan…")
        run_btn.setStyleSheet("font-weight: bold; padding: 4px 16px;")
        self.progress, self.status = QProgressBar(), QLabel("100% berjalan lokal/offline.")
        self.busy_widgets = [add_files, add_dir, clear, self.preset_box, rules_btn, hist_btn, self.ocr, self.mode,
                             run_btn, undo_btn, report, self.table]

        top = row(add_files, add_dir, clear, QLabel("   Preset:"), self.preset_box, rules_btn, hist_btn)
        top.addStretch()
        top.addWidget(self.ocr)
        top.addWidget(self.dark)
        dash = QHBoxLayout()
        for card in (c_tot, c_ok, c_warn, c_fail, c_zip, c_spd):
            dash.addWidget(card, 1)
        root = QVBoxLayout()
        root.addLayout(top)
        root.addLayout(dash)
        root.addLayout(row(QLabel("Tampilkan:"), self.filter, self.search, self.summary, stretch=self.search))
        root.addWidget(self.stack, 1)
        root.addLayout(row(QLabel("Hasil:"), self.mode, run_btn, undo_btn, report, self.progress, self.status,
                           stretch=self.progress))
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        add_files.clicked.connect(self.pick_files)
        add_dir.clicked.connect(self.pick_dir)
        clear.clicked.connect(self.clear_all)
        self.preset_box.currentTextChanged.connect(self.on_preset)
        rules_btn.clicked.connect(self.open_rules)
        hist_btn.clicked.connect(lambda: HistoryDialog(self).exec())
        self.ocr.toggled.connect(self.on_ocr)
        self.dark.toggled.connect(self.on_dark)
        self.filter.currentIndexChanged.connect(self.apply_filter)
        self.search.textChanged.connect(self.apply_filter)
        self.table.itemChanged.connect(self.on_edit)
        self.mode.currentIndexChanged.connect(lambda: self.items and self.refresh())
        run_btn.clicked.connect(self.rename_all)
        undo_btn.clicked.connect(self.undo)
        report.clicked.connect(self.export_report)

        self.dark.setChecked(self.settings.value("dark", False, bool))
        self.load_presets()

    # ---------- input ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.load_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Pilih ZIP / PDF", "", "ZIP atau PDF (*.zip *.pdf)")
        self.load_paths(paths)

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Pilih folder berisi PDF")
        if d:
            self.load_paths([d])

    def load_paths(self, paths):
        if not paths or self.busy():
            return
        self.tmp = self.tmp or tempfile.TemporaryDirectory(prefix="fileora_")
        found, skipped, arch = zip_handler.collect(paths, self.tmp.name)
        self.archives += arch
        known = {it.src for it in self.items}
        new = [Item(p, rel, z) for p, rel, z in found if p not in known]
        if skipped:
            QMessageBox.warning(self, "Fileora", "Dilewati (bukan PDF, ZIP rusak, atau entri tidak aman):\n"
                                + "\n".join(skipped[:20]))
        if not new:
            return
        if not self.items:
            self.first_input = Path(paths[0])
            self.mode.setCurrentIndex(list(OUT_MODES).index("zip" if any(it.in_zip for it in new) else "folder"))
        self.items += new
        if self.archives:
            tot_sz = sum(a.size_bytes for a in self.archives)
            tot_pdf = sum(a.pdf_count for a in self.archives)
            names = ", ".join(a.name for a in self.archives[:2])
            self.m_zip.setText(f"{names} ({fmt_bytes(tot_sz)} · {tot_pdf} PDF)")
        else:
            self.m_zip.setText(f"{self.first_input.name} ({len(self.items)} PDF)")
        self.scan(new)

    def scan(self, items):
        ocr = self.ocr.isChecked()
        self.run_job(lambda p: extractor.extract_batch(items, ocr, p), "Membaca PDF", lambda _: self.refresh())

    # ---------- worker ----------
    def busy(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def run_job(self, fn, label, done):
        self.job_label, self.job_done_cb, self.t0 = label, done, time.perf_counter()
        self.worker = Worker(fn)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        for w in self.busy_widgets:
            w.setEnabled(False)
        self.worker.start()

    def on_progress(self, i, n):
        self.progress.setMaximum(n)
        self.progress.setValue(i)
        elapsed = max(0.001, time.perf_counter() - self.t0)
        rate = i / elapsed
        eta = (n - i) / rate if rate > 0 else 0
        self.m_speed.setText(f"{rate:.1f} dok/dtk · ETA {eta:.1f} dtk")
        self.status.setText(f"{self.job_label}: {i}/{n} ({i * 100 // max(1, n)}%)")

    def on_finished(self):
        for w in self.busy_widgets:
            w.setEnabled(True)
        w = self.worker
        elapsed = max(0.001, time.perf_counter() - self.t0)
        if w.error:
            self.status.setText(f"{self.job_label} gagal")
            QMessageBox.critical(self, "Fileora", f"{self.job_label} gagal:\n{w.error}")
        else:
            rate = max(1, self.progress.maximum()) / elapsed
            self.m_speed.setText(f"{rate:.1f} dok/dtk · Selesai {elapsed:.2f} dtk")
            self.status.setText(f"{self.job_label} selesai ({elapsed:.2f} dtk)")
            self.job_done_cb(w.result)

    # ---------- preview ----------
    def refresh(self):
        renamer.plan(self.items, self.preset, self.mode.currentData() == "inplace")
        fields = self.preset.fields
        cols = ["Status", "Confidence", "Nama lama", "Nama baru (bisa diedit)",
                *(f.replace("_", " ").upper() for f in fields), "Catatan"]
        t = self.table
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        t.clear()
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            vals = [it.status, f"{it.confidence}%", str(Path(it.rel_dir, it.old_name)), it.new_name,
                    *(it.values.get(f, "") for f in fields), "; ".join(it.notes)]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if c != 3:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, c, cell)
            t.item(r, 0).setBackground(QColor(COLORS[it.status]))
            t.item(r, 0).setForeground(QColor("black" if it.status == WARN else "white"))
        t.resizeColumnsToContents()
        for c in (2, 3):
            t.setColumnWidth(c, min(t.columnWidth(c), 380))
        t.horizontalHeader().setStretchLastSection(True)
        t.blockSignals(False)
        t.setUpdatesEnabled(True)
        self.stack.setCurrentIndex(1 if self.items else 0)
        n = Counter(it.status for it in self.items)
        self.m_total.setText(str(len(self.items)))
        self.m_ok.setText(str(n[OK]))
        self.m_warn.setText(str(n[WARN]))
        self.m_fail.setText(str(n[FAIL]))
        self.summary.setText(f"{len(self.items)} file — OK {n[OK]} · Peringatan {n[WARN]} · Gagal {n[FAIL]}")
        self.apply_filter()

    def apply_filter(self):
        want, q = self.filter.currentText(), self.search.text().strip().lower()
        for r, it in enumerate(self.items):
            hay = " ".join([it.old_name, it.new_name, *it.values.values()]).lower()
            self.table.setRowHidden(r, (want != "Semua" and it.status != want) or (bool(q) and q not in hay))

    def on_edit(self, cell):
        if cell.column() == 3:
            self.items[cell.row()].manual = cell.text().strip()
            QTimer.singleShot(0, self.refresh)

    # ---------- preset & opsi ----------
    def load_presets(self, select=None):
        self.presets = presets.load_all()
        name = next((n for n in (select, self.settings.value("preset"), presets.BPJS.name) if n in self.presets),
                    next(iter(self.presets)))
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItems(list(self.presets))
        self.preset_box.setCurrentText(name)
        self.preset_box.blockSignals(False)
        self.on_preset(name)

    def on_preset(self, name):
        if name in self.presets:
            self.preset = self.presets[name]
            self.settings.setValue("preset", name)
            if self.items:
                self.refresh()

    def open_rules(self):
        dlg = RulesDialog(self, self.preset, self.items, self.ocr.isChecked())
        self.load_presets(dlg.saved_name if dlg.exec() else self.preset.name)

    def on_ocr(self, on):
        self.settings.setValue("ocr", on)
        if on:
            self.status.setText("OCR aktif: PDF hasil scan diproses lebih lambat.")
        redo = [it for it in self.items if it.error] if on else []
        for it in redo:
            it.error, it.pages = "", None
        if redo:
            self.scan(redo)

    def on_dark(self, on):
        self.settings.setValue("dark", on)
        QApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark if on else Qt.ColorScheme.Light)

    # ---------- eksekusi ----------
    def rename_all(self):
        if not self.items or self.busy():
            return
        mode = self.mode.currentData()
        if mode == "inplace" and any(it.in_zip for it in self.items):
            QMessageBox.warning(self, "Fileora", "File dari ZIP tidak bisa di-rename di tempat.\n"
                                                 "Pilih hasil “ZIP baru” atau “Folder baru”.")
            return
        self.refresh()
        base, target = self.first_input, None
        if mode == "zip":
            path, _ = QFileDialog.getSaveFileName(self, "Simpan ZIP hasil", str(base.with_name(base.stem + "_fileora.zip")),
                                                  "ZIP (*.zip)", options=QFileDialog.DontConfirmOverwrite)
            if not path:
                return
            if Path(path).exists():
                QMessageBox.warning(self, "Fileora", "File sudah ada. Pilih nama lain (Fileora tidak menimpa file).")
                return
            target = Path(path)
        elif mode == "folder":
            parent = QFileDialog.getExistingDirectory(self, "Pilih lokasi folder hasil", str(base.parent))
            if not parent:
                return
            target = Path(parent, f"Fileora_{time.strftime('%Y%m%d_%H%M%S')}")
        fail = sum(it.status == FAIL for it in self.items)
        msg = (f"{len(self.items)} file akan diproses → {OUT_MODES[mode]}.\n"
               f"{fail} file gagal dibaca tetap memakai nama lama.")
        if mode == "inplace":
            msg += "\n\nNama file asli akan diubah (bisa dikembalikan dengan Undo)."
        if QMessageBox.question(self, "Fileora", msg + "\n\nLanjutkan?") != QMessageBox.Yes:
            return
        items = list(self.items)
        flat = getattr(self.preset, "flat_zip", False)
        self.run_job(lambda p: renamer.execute(items, mode, target, p, flat=flat), "Rename", self.rename_done)

    def rename_done(self, log):
        logger.save_undo(log)
        if log["mode"] == "inplace":
            self.remap(dict(log["moves"]))
        where = Path(log.get("root") or (log["created"][0] if log["mode"] == "zip" else self.items[0].src))
        n = Counter(it.status for it in self.items)
        logger.append_history({
            "source": self.first_input.name if self.first_input else "",
            "total": len(self.items), "ok": n[OK], "warn": n[WARN], "fail": n[FAIL],
            "output": str(where),
        })
        if QMessageBox.question(self, "Fileora", f"Selesai: {len(self.items)} file.\nHasil: {where}\n\n"
                                                 "Buka lokasi hasil?") == QMessageBox.Yes:
            os.startfile(where if where.is_dir() else where.parent)

    def remap(self, moves: dict[str, str]):
        for it in self.items:
            it.src, it.manual = Path(moves.get(str(it.src), it.src)), ""
        self.refresh()

    def undo(self):
        log = logger.load_undo()
        if not log:
            QMessageBox.information(self, "Fileora", "Tidak ada operasi yang bisa di-undo.")
            return
        q = {"inplace": f"Kembalikan {len(log.get('moves', []))} file ke nama lamanya?",
             "zip": f"Hapus ZIP hasil?\n{log['created'][0]}",
             "folder": f"Hapus {len(log.get('created', []))} file hasil di\n{log.get('root')}?"}[log["mode"]]
        if QMessageBox.question(self, "Fileora", q + "\n\nFile asli tidak terpengaruh.") != QMessageBox.Yes:
            return
        try:
            renamer.undo(log)
        except Exception as e:
            logging.exception("Undo gagal")
            QMessageBox.critical(self, "Fileora", f"Undo gagal (tidak ada file yang diubah):\n{e}")
            return
        logger.clear_undo()
        if log["mode"] == "inplace":
            self.remap({b: a for a, b in log["moves"]})
        QMessageBox.information(self, "Fileora", "Undo selesai.")

    def export_report(self):
        if not self.items:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor laporan", str(self.first_input.with_name("laporan_fileora.xlsx")),
                                              "Excel (*.xlsx);;CSV (*.csv)")
        if path:
            try:
                logger.export_report(self.items, self.preset.fields, path)
                self.status.setText(f"Laporan disimpan: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Fileora", f"Gagal menyimpan laporan:\n{e}")

    def clear_all(self):
        self.items, self.archives, self.first_input = [], [], None
        self.m_zip.setText("—")
        self.m_speed.setText("—")
        if self.tmp:
            self.tmp.cleanup()
            self.tmp = None
        self.refresh()

    def closeEvent(self, e):
        if self.busy():
            QMessageBox.information(self, "Fileora", "Tunggu proses selesai sebelum menutup.")
            e.ignore()
            return
        if self.tmp:
            self.tmp.cleanup()
        e.accept()


def main():
    logger.setup()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource("assets/fileora.ico")))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
