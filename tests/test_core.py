"""Unit test inti Fileora (tanpa UI). Jalankan: python -m unittest -v"""
import hashlib
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pymupdf

from fileora import extractor, renamer, zip_handler
from fileora.presets import BPJS, NPP_FIRST, Preset
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
        self.assertEqual((it.new_name, it.status, it.confidence), ("SEMANGAT HASRAT JAYA_15053822.pdf", OK, 100))

    def test_format_npp_nama_perusahaan_hyphen(self):
        items = planned([bpjs_pages("PUTRA JAYA", "274937937"),
                         bpjs_pages("MAJU SEJAHTERA", "284738291"),
                         bpjs_pages("SINAR ABADI", "294837192")], preset=NPP_FIRST)
        self.assertEqual([i.new_name for i in items], [
            "274937937_Putra-Jaya.pdf",
            "284738291_Maju-Sejahtera.pdf",
            "294837192_Sinar-Abadi.pdf",
        ])

    def test_npp_huruf_dan_tanda_baca(self):
        a, b = planned([bpjs_pages("HARI MAS (KANTOR)", "DD003627"), bpjs_pages("ANDALAS KARYA MULIA - SP", "1234")])
        self.assertEqual(a.new_name, "HARI MAS (KANTOR)_DD003627.pdf")
        self.assertEqual(b.new_name, "ANDALAS KARYA MULIA - SP_1234.pdf")

    def test_nama_terpotong_di_halaman_2(self):
        it, = planned([bpjs_pages("ANDALAS KARYA MULIA - SP", "12345", name_p2="ANDALAS KARYA MUL")])
        self.assertEqual((it.new_name, it.status), ("ANDALAS KARYA MULIA - SP_12345.pdf", WARN))
        self.assertLess(it.confidence, 100)

    def test_cadangan_dan_layout_berbeda(self):
        pages = bpjs_pages("PT MAKMUR", "7788")
        pages[0] = "Surat tanpa penanda"
        layout2 = ["Dokumen Tagihan\nPerusahaan : CV SENTOSA JAYA\nNo. NPP : 99887766"]
        a, b = planned([pages, layout2], preset=NPP_FIRST)
        self.assertEqual(a.new_name, "7788_Pt-Makmur.pdf")
        self.assertEqual(b.new_name, "99887766_Cv-Sentosa-Jaya.pdf")

    def test_missing_npp_atau_missing_company(self):
        no_npp = ["Yth.\nPimpinan\nPT TANPA NPP\nJLN. RAYA"]
        no_comp = ["NPP : 15053822\nDIV : 000"]
        empty = ["kosong", "juga kosong"]
        a, b, c = planned([no_npp, no_comp, empty])
        self.assertEqual((a.status, a.new_name), (FAIL, "file0.pdf"))
        self.assertIn("npp", a.notes[0])
        self.assertEqual((b.status, b.new_name), (FAIL, "file1.pdf"))
        self.assertIn("nama_perusahaan", b.notes[0])
        self.assertEqual((c.status, c.new_name), (FAIL, "file2.pdf"))

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

    def test_duplikat_nama_dan_duplikat_npp(self):
        items = planned([bpjs_pages("PT A", "11")] * 3)
        self.assertEqual([i.new_name for i in items], ["PT A_11.pdf", "PT A_11_2.pdf", "PT A_11_3.pdf"])
        self.assertEqual([i.status for i in items], [WARN, WARN, WARN])
        self.assertIn("NPP duplikat", items[0].notes[0])

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

    def test_zip_slip_dan_corrupted_zip(self):
        z = self.dir / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../evil.pdf", b"x")
            zf.writestr("nested/ok/a.pdf", b"x")
            zf.writestr("nested/catatan.txt", b"abaikan non-pdf")
        out = self.dir / "out"
        out.mkdir()
        files, skipped, stats = zip_handler.extract_zip(z, out)
        self.assertEqual(([f.name for f in files], skipped), (["a.pdf"], ["../evil.pdf"]))
        self.assertEqual(stats.pdf_count, 1)
        self.assertFalse((self.dir / "evil.pdf").exists())

        bad_zip = self.dir / "corrupt.zip"
        bad_zip.write_bytes(b"bukan zip valid")
        found, bad, _ = zip_handler.collect([bad_zip], self.dir)
        self.assertEqual((found, bad), ([], ["corrupt.zip"]))

    def test_encrypted_empty_scanned_ocr_pdf(self):
        # Encrypted PDF
        enc = self.dir / "enc.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Rahasia")
        doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="secret")
        doc.close()
        with self.assertRaises(extractor.ExtractionError) as ctx:
            extractor.extract_pages(enc)
        self.assertIn("enkripsi", str(ctx.exception))

        # Scanned PDF (tanpa text layer) -> gagal jika OCR mati, berhasil jika OCR hidup
        scan = self.dir / "scan.pdf"
        make_pdf(scan, [""])
        with self.assertRaises(extractor.ExtractionError):
            extractor.extract_pages(scan, ocr=False)
        with patch("fileora.extractor._ocr", return_value=["NPP : 274937937\nNama Perusahaan : PUTRA JAYA"]):
            pages = extractor.extract_pages(scan, ocr=True)
            it, = planned([pages], preset=NPP_FIRST)
            self.assertEqual(it.new_name, "274937937_Putra-Jaya.pdf")

    def test_zip_10_dan_100_pdfs(self):
        for count in (10, 100):
            sub = self.dir / f"run_{count}"
            sub.mkdir()
            z = sub / f"in_{count}.zip"
            with zipfile.ZipFile(z, "w") as zf:
                for i in range(count):
                    p = sub / f"doc_{i}.pdf"
                    if i == 0:
                        make_pdf(p, bpjs_pages("PUTRA JAYA", f"2749{i:05d}"))
                    else:
                        zf.writestr(f"folder_{i % 3}/doc_{i}.pdf", (sub / "doc_0.pdf").read_bytes())
                        continue
                    zf.writestr(f"folder_{i % 3}/doc_{i}.pdf", p.read_bytes())
            found, skipped, arch = zip_handler.collect([z], sub)
            self.assertEqual((len(found), skipped, arch[0].pdf_count), (count, [], count))
            items = [Item(p, rel, in_zip) for p, rel, in_zip in found]
            extractor.extract_batch(items)
            renamer.plan(items, NPP_FIRST)
            out = sub / f"out_{count}.zip"
            renamer.execute(items, "zip", out)
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(len(set(zf.namelist())), count)

    def test_zip_1000_pdfs_dan_skala_100mb(self):
        """Uji performa 1.000 PDF di dalam ZIP + verifikasi streaming ZIP ~100 MB."""
        template_pdf = self.dir / "tpl.pdf"
        make_pdf(template_pdf, bpjs_pages("MAJU SEJAHTERA", "284738291"))
        pdf_bytes = template_pdf.read_bytes()

        z1000 = self.dir / "batch_1000.zip"
        with zipfile.ZipFile(z1000, "w", zipfile.ZIP_STORED) as zf:
            for i in range(1000):
                zf.writestr(f"sub_{i % 5}/random_{i}.pdf", pdf_bytes)

        t0 = time.perf_counter()
        found, skipped, arch = zip_handler.collect([z1000], self.dir)
        items = [Item(p, rel, in_zip) for p, rel, in_zip in found]
        extractor.extract_batch(items)
        from dataclasses import replace
        flat_preset = replace(NPP_FIRST, flat_zip=True)
        renamer.plan(items, flat_preset)
        out_zip = self.dir / "out_1000.zip"
        renamer.execute(items, "zip", out_zip, flat=flat_preset.flat_zip)
        elapsed = time.perf_counter() - t0

        self.assertEqual((len(items), skipped, arch[0].pdf_count), (1000, [], 1000))
        with zipfile.ZipFile(out_zip) as zf:
            names = set(zf.namelist())
            self.assertEqual(len(names), 1000)
            self.assertIn("284738291_Maju-Sejahtera.pdf", names)
            self.assertIn("284738291_Maju-Sejahtera_1000.pdf", names)
            self.assertEqual(zf.read("284738291_Maju-Sejahtera.pdf"), pdf_bytes)
        print(f"\n1.000 PDF dalam ZIP selesai diproses & dipaketkan dalam {elapsed:.2f} detik")

        # Uji arsip ~100 MB (10 PDF x ~10 MB) memastikan batas & streaming berjalan lancar
        big_zip = self.dir / "100mb.zip"
        pad = b"%Rows" + (b"0123456789ABCDEF" * 655360)  # ~10.4 MB komentar PDF
        big_pdf = pdf_bytes + b"\n" + pad
        with zipfile.ZipFile(big_zip, "w", zipfile.ZIP_STORED) as zf:
            for i in range(10):
                zf.writestr(f"big_{i}.pdf", big_pdf)
        self.assertGreaterEqual(big_zip.stat().st_size, 100 * (1 << 20))
        found_big, skipped_big, arch_big = zip_handler.collect([big_zip], self.dir)
        self.assertEqual((len(found_big), skipped_big), (10, []))
        self.assertGreaterEqual(arch_big[0].size_bytes, 100 * (1 << 20))

    def test_batch_129_zip_end_to_end(self):
        src = self.dir / "src"
        src.mkdir()
        names = ["SEMANGAT HASRAT JAYA", "HARI MAS (KANTOR)", "ANDALAS KARYA MULIA - SP"]
        expected = set()
        for i in range(129):
            name, npp = names[i % 3], f"DD{i:06d}" if i % 2 else f"{15053822 + i}"
            make_pdf(src / f"tagihan_{i}.pdf", bpjs_pages(name, npp, name_p2=name[:15]))
            expected.add(f"{name}_{npp}.pdf")
        make_pdf(src / "rusak.pdf", [""])
        (src / "bukan_pdf.pdf").write_bytes(b"bukan pdf")
        z = self.dir / "batch.zip"
        zip_handler.write_zip(((f, f.name) for f in src.iterdir()), z)
        digest = hashlib.sha256(z.read_bytes()).hexdigest()

        t0 = time.perf_counter()
        found, _, _ = zip_handler.collect([z], self.dir)
        items = [Item(p, rel, in_zip) for p, rel, in_zip in found]
        extractor.extract_batch(items)
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
        self.assertEqual(hashlib.sha256(z.read_bytes()).hexdigest(), digest)
        self.assertLess(elapsed, 30)

    def test_inplace_tukar_nama_dan_undo(self):
        a, b, keep = self.dir / "B_22.pdf", self.dir / "A_11.pdf", self.dir / "C_33.pdf"
        make_pdf(a, bpjs_pages("PT A", "11"))
        make_pdf(b, bpjs_pages("PT B", "22"))
        keep.write_bytes(b"lain")
        make_pdf(self.dir / "x.pdf", bpjs_pages("C", "33"))
        items = [Item(p, pages=extractor.extract_pages(p)) for p in (a, b, self.dir / "x.pdf")]
        renamer.plan(items, BPJS, inplace=True)
        self.assertEqual([i.new_name for i in items], ["PT A_11.pdf", "PT B_22.pdf", "C_33_2.pdf"])
        log = renamer.execute(items, "inplace")
        self.assertEqual(extractor.extract_pages(self.dir / "PT A_11.pdf")[0].split("\n")[2], "PT A")
        self.assertEqual(keep.read_bytes(), b"lain")
        renamer.undo(log)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["A_11.pdf", "B_22.pdf", "C_33.pdf", "x.pdf"])

    def test_folder_mode_dan_undo(self):
        p = self.dir / "a.pdf"
        make_pdf(p, bpjs_pages("PT A", "11"))
        items = [Item(p, pages=extractor.extract_pages(p))]
        renamer.plan(items, BPJS)
        log = renamer.execute(items, "folder", self.dir / "out")
        self.assertTrue((self.dir / "out" / "PT A_11.pdf").exists() and p.exists())
        renamer.undo(log)
        self.assertFalse((self.dir / "out").exists())

    def test_multi_format_docx_doc_rtf_odt_dan_gambar(self):
        from PIL import Image
        lines = ["Yth.", "Pimpinan", "PT MULTI FORMAT JAYA", "NPP : 88776655"]

        # 1. .docx (Office Open XML)
        docx_path = self.dir / "surat.docx"
        w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        body = "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in lines)
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/document.xml", f'<w:document xmlns:w="{w_ns}"><w:body>{body}</w:body></w:document>')

        # 2. .odt (OpenDocument Text)
        odt_path = self.dir / "surat.odt"
        t_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        o_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        obody = "".join(f"<text:p>{ln}</text:p>" for ln in lines)
        with zipfile.ZipFile(odt_path, "w") as zf:
            zf.writestr("content.xml", f'<office:document-content xmlns:office="{o_ns}" xmlns:text="{t_ns}"><office:body><office:text>{obody}</office:text></office:body></office:document-content>')

        # 3. .rtf (Rich Text Format)
        rtf_path = self.dir / "surat.rtf"
        rtf_body = r"\par ".join(lines)
        rtf_path.write_text(r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0\fs22 " + rtf_body + "}", encoding="latin-1")

        # 4. .doc (Microsoft Word 97-2003 binary OLE2 header + UTF-16LE text stream)
        doc_path = self.dir / "surat.doc"
        ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + (b"\x00" * 512)
        utf16_stream = "\r\n".join(lines).encode("utf-16le")
        doc_path.write_bytes(ole_header + utf16_stream + (b"\x00" * 128))

        # 5. Gambar (.jpg, .jpeg, .png, .tiff, .tif, .bmp)
        img_paths = []
        for ext, fmt in ((".jpg", "JPEG"), (".jpeg", "JPEG"), (".png", "PNG"),
                         (".tiff", "TIFF"), (".tif", "TIFF"), (".bmp", "BMP")):
            ip = self.dir / f"scan{ext}"
            Image.new("RGB", (64, 32), "white").save(ip, format=fmt)
            img_paths.append(ip)

        # Kemas seluruh format ke dalam satu ZIP dan uji ekstraksi + rename
        mix_zip = self.dir / "semua_format.zip"
        all_inputs = [docx_path, odt_path, rtf_path, doc_path, *img_paths]
        zip_handler.write_zip(((f, f.name) for f in all_inputs), mix_zip)

        found, skipped, arch = zip_handler.collect([mix_zip], self.dir)
        self.assertEqual((len(found), skipped, arch[0].pdf_count), (10, [], 10))
        items = [Item(p, rel, in_zip) for p, rel, in_zip in found]
        with patch("fileora.extractor._ocr_image", return_value=["\n".join(lines)]):
            extractor.extract_batch(items, ocr=True)
        renamer.plan(items, NPP_FIRST)
        by_ext = {it.src.suffix.lower(): it.new_name for it in items}
        for ext in (".docx", ".odt", ".rtf", ".doc", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
            self.assertTrue(by_ext[ext].startswith("88776655_Pt-Multi-Format-Jaya"), f"Gagal pada {ext}: {by_ext[ext]}")
            self.assertTrue(by_ext[ext].endswith(ext), f"Ekstensi berubah pada {ext}: {by_ext[ext]}")


if __name__ == "__main__":
    unittest.main()
