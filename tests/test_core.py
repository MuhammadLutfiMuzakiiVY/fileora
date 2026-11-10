"""Unit test inti Fileora (tanpa UI). Jalankan: python -m unittest -v"""
import hashlib
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import pymupdf

from fileora import extractor, renamer, zip_handler
from fileora.presets import BPJS, Preset
from fileora.renamer import FAIL, OK, WARN, Item
from fileora.rules import Source, extract_fields, find, learn_source


def bpjs_pages(name, npp, name_p2=None):
    p1 = f"Yth.\nPimpinan\n{name}\nJLN. PERMATA NO.3 ...\nNPP : {npp}\nDIV : 000"
    p2 = f"NPP/Divisi        : {npp} / 000\nNama Perusahaan   : {name_p2 or name}"
    return [extractor.normalize(p1), extractor.normalize(p2)]


def make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()


def planned(pages_list, preset=BPJS, **kw):
    items = [Item(Path(f"file{i}.pdf"), pages=p) for i, p in enumerate(pages_list)]
    renamer.plan(items, preset, **kw)
    return items


class TestExtraction(unittest.TestCase):
    def test_contoh_bpjs(self):
        it, = planned([bpjs_pages("SEMANGAT HASRAT JAYA", "15053822")])
        self.assertEqual((it.new_name, it.status), ("SEMANGAT HASRAT JAYA_15053822.pdf", OK))

    def test_npp_huruf_dan_tanda_baca(self):
        a, b = planned([bpjs_pages("HARI MAS (KANTOR)", "DD003627"), bpjs_pages("ANDALAS KARYA MULIA - SP", "1234")])
        self.assertEqual(a.new_name, "HARI MAS (KANTOR)_DD003627.pdf")
        self.assertEqual(b.new_name, "ANDALAS KARYA MULIA - SP_1234.pdf")

    def test_nama_terpotong_di_halaman_2(self):
        it, = planned([bpjs_pages("ANDALAS KARYA MULIA - SP", "1", name_p2="ANDALAS KARYA MUL")])
        self.assertEqual((it.new_name, it.status), ("ANDALAS KARYA MULIA - SP_1.pdf", WARN))

    def test_cadangan_halaman_2(self):
        pages = bpjs_pages("X", "77")
        pages[0] = "Surat tanpa penanda"
        it, = planned([pages])
        self.assertEqual(it.new_name, "X_77.pdf")

    def test_field_tidak_ditemukan(self):
        it, = planned([["kosong", "juga kosong"]])
        self.assertEqual((it.status, it.new_name), (FAIL, "file0.pdf"))

    def test_label_tanpa_spasi_dan_baris_baru(self):
        self.assertEqual(find(Source("n", "label", "NPP :", 0), ["NPP:AB12"]), "AB12")
        self.assertEqual(find(Source("n", "label", "NPP :", 0), ["NPP\n:\nAB12"]), "AB12")
        self.assertIsNone(find(Source("n", "label", "NPP :", 0), ["NPP/Divisi : 1 / 000"]))

    def test_belajar_dari_contoh(self):
        p1, p2 = bpjs_pages("SEMANGAT HASRAT JAYA", "15053822")
        sel = lambda t, s: (t.index(s), t.index(s) + len(s))  # noqa: E731
        name1 = learn_source("nama", p1, 1, *sel(p1, "SEMANGAT HASRAT JAYA"))
        name2 = learn_source("nama", p2, 2, *sel(p2, "SEMANGAT HASRAT JAYA"))
        npp2 = learn_source("npp", p2, 2, *sel(p2, "15053822"))
        self.assertEqual((name1.mode, name1.pattern), ("next_line", "Pimpinan"))
        self.assertEqual((name2.mode, name2.pattern), ("label", "Nama Perusahaan :"))
        self.assertEqual(npp2.mode, "regex")
        other = bpjs_pages("HARI MAS (KANTOR)", "DD003627")
        vals, _, missing = extract_fields(other, [name1, name2, npp2])
        self.assertEqual((vals, missing), ({"nama": "HARI MAS (KANTOR)", "npp": "DD003627"}, []))


class TestNaming(unittest.TestCase):
    def test_karakter_terlarang_dan_reserved(self):
        self.assertEqual(renamer.sanitize('A/B:C*D?"E<F>G|H. .'), "A B C D E F G H")
        self.assertEqual(renamer.sanitize("con"), "_con")
        self.assertEqual(renamer.sanitize("CONTOH"), "CONTOH")

    def test_duplikat(self):
        items = planned([bpjs_pages("PT A", "1")] * 3)
        self.assertEqual([i.new_name for i in items], ["PT A_1.pdf", "PT A_1_2.pdf", "PT A_1_3.pdf"])
        self.assertEqual([i.status for i in items], [OK, WARN, WARN])

    def test_nama_panjang_npp_tetap_utuh(self):
        it, = planned([bpjs_pages("SANGAT PANJANG " * 20, "DD003627")])
        self.assertLessEqual(len(it.new_name), 150)
        self.assertTrue(it.new_name.endswith("_DD003627.pdf"))
        self.assertEqual(it.status, WARN)

    def test_template_case_separator(self):
        p = Preset("x", BPJS.sources, "{npp}_{nama_perusahaan}", "-", "title")
        it, = planned([bpjs_pages("HARI MAS (KANTOR)", "dd1")], p)
        self.assertEqual(it.new_name, "Dd1-Hari Mas (Kantor).pdf")

    def test_manual(self):
        items = [Item(Path("a.pdf"), pages=["kosong"], manual="Nama: Baru"),
                 Item(Path("scan.pdf"), error="Tidak ada teks", manual="Hasil Scan.pdf")]
        renamer.plan(items, BPJS)
        self.assertEqual([(i.new_name, i.status) for i in items], [("Nama Baru.pdf", OK), ("Hasil Scan.pdf", OK)])


class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip_slip_dilewati(self):
        z = self.dir / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../evil.pdf", b"x")
            zf.writestr("ok/a.pdf", b"x")
        out = self.dir / "out"
        out.mkdir()
        files, skipped = zip_handler.extract_zip(z, out)
        self.assertEqual(([f.name for f in files], skipped), (["a.pdf"], ["../evil.pdf"]))
        self.assertFalse((self.dir / "evil.pdf").exists())

    def test_batch_129_zip_end_to_end(self):
        src = self.dir / "src"
        src.mkdir()
        names = ["SEMANGAT HASRAT JAYA", "HARI MAS (KANTOR)", "ANDALAS KARYA MULIA - SP"]
        expected = set()
        for i in range(129):
            name, npp = names[i % 3], f"DD{i:06d}" if i % 2 else f"{15053822 + i}"
            make_pdf(src / f"tagihan_{i}.pdf", bpjs_pages(name, npp, name_p2=name[:15]))
            expected.add(f"{name}_{npp}.pdf")
        make_pdf(src / "rusak.pdf", [""])  # tanpa teks: harus gagal tanpa menghentikan proses
        (src / "bukan_pdf.pdf").write_bytes(b"bukan pdf")
        z = self.dir / "batch.zip"
        zip_handler.write_zip(((f, f.name) for f in src.iterdir()), z)
        digest = hashlib.sha256(z.read_bytes()).hexdigest()

        t0 = time.perf_counter()
        found, _ = zip_handler.collect([z], self.dir)
        items = [Item(p, rel, in_zip) for p, rel, in_zip in found]
        for it in items:
            try:
                it.pages = extractor.extract_pages(it.src)
            except extractor.ExtractionError as e:
                it.error = str(e)
        renamer.plan(items, BPJS)
        out = self.dir / "hasil.zip"
        renamer.execute(items, "zip", out)
        elapsed = time.perf_counter() - t0

        with zipfile.ZipFile(out) as zf:
            got = set(zf.namelist())
        self.assertEqual(len(got), 131)
        self.assertTrue(expected <= got)
        self.assertTrue({"rusak.pdf", "bukan_pdf.pdf"} <= got)
        self.assertEqual(sum(it.status == FAIL for it in items), 2)
        self.assertEqual(hashlib.sha256(z.read_bytes()).hexdigest(), digest)  # ZIP asli utuh
        self.assertLess(elapsed, 30)
        print(f"\n129+2 PDF diproses dalam {elapsed:.2f} detik")

    def test_inplace_tukar_nama_dan_undo(self):
        a, b, keep = self.dir / "B_2.pdf", self.dir / "A_1.pdf", self.dir / "C_3.pdf"
        make_pdf(a, bpjs_pages("A", "1"))  # isi A, nama file milik B -> harus bertukar
        make_pdf(b, bpjs_pages("B", "2"))
        keep.write_bytes(b"lain")  # file di luar batch tak boleh tertimpa
        make_pdf(self.dir / "x.pdf", bpjs_pages("C", "3"))
        items = [Item(p, pages=extractor.extract_pages(p)) for p in (a, b, self.dir / "x.pdf")]
        renamer.plan(items, BPJS, inplace=True)
        self.assertEqual([i.new_name for i in items], ["A_1.pdf", "B_2.pdf", "C_3_2.pdf"])
        log = renamer.execute(items, "inplace")
        self.assertEqual(extractor.extract_pages(self.dir / "A_1.pdf")[0].split("\n")[2], "A")
        self.assertEqual(keep.read_bytes(), b"lain")
        renamer.undo(log)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["A_1.pdf", "B_2.pdf", "C_3.pdf", "x.pdf"])
        self.assertEqual(extractor.extract_pages(self.dir / "B_2.pdf")[0].split("\n")[2], "A")

    def test_folder_mode_dan_undo(self):
        p = self.dir / "a.pdf"
        make_pdf(p, bpjs_pages("A", "1"))
        items = [Item(p, pages=extractor.extract_pages(p))]
        renamer.plan(items, BPJS)
        log = renamer.execute(items, "folder", self.dir / "out")
        self.assertTrue((self.dir / "out" / "A_1.pdf").exists() and p.exists())
        renamer.undo(log)
        self.assertFalse((self.dir / "out").exists())


if __name__ == "__main__":
    unittest.main()
