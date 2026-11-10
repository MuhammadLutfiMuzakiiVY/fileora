# Fileora — Rename Dokumen Otomatis Berdasarkan Isi

**Publisher / Author:** Muhammad Lutfi Muzaki

Aplikasi desktop Windows (100% offline) untuk me-rename ratusan hingga ribuan file dokumen/surat sekaligus berdasarkan teks di dalam dokumen.

## Format File yang Didukung

| Format | Keterangan |
| :--- | :--- |
| `.pdf` | Surat PDF |
| `.doc` | Surat Microsoft Word lama |
| `.docx` | Surat Microsoft Word |
| `.rtf` | Rich Text Format |
| `.odt` | OpenDocument Text |
| `.jpg` / `.jpeg` | Foto atau scan surat |
| `.png` | Scan surat |
| `.tiff` / `.tif` | Dokumen hasil scan |
| `.bmp` | Gambar surat |

## 1. Menjalankan dari Kode Sumber

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Menjalankan unit test:
```powershell
.\.venv\Scripts\python -m unittest -v
```

## 2. Build Menjadi `Fileora.exe` (Tanpa Perlu Python)

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name Fileora --icon assets\fileora.ico --version-file version_info.txt --add-data "assets;assets" --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia --exclude-module onnxruntime --exclude-module pymupdf4llm --exclude-module pymupdf_layout main.py
```
File hasil build tersedia di `dist\Fileora.exe`. Untuk membuat installer Windows (`dist\Fileora_Setup.exe`), buka `installer.iss` dengan **Inno Setup** lalu klik **Compile** (atau jalankan `ISCC.exe installer.iss`).

## 3. Instalasi OCR (Opsional, Hanya untuk PDF Hasil Scan)

PDF yang memiliki text layer (seperti dokumen BPJS Ketenagakerjaan) **tidak membutuhkan OCR**. Jika ada PDF hasil scan gambar:
1. Unduh dan pasang **Tesseract OCR Windows** dari UB Mannheim (`tesseract-ocr-w64-setup.exe`) ke lokasi default `C:\Program Files\Tesseract-OCR\`.
2. Saat instalasi, centang *Additional language data* → **Indonesian (`ind`)** dan **English (`eng`)**.
3. Di Fileora, centang **Aktifkan OCR**.

## 4. Cara Pakai

### A. Menggunakan Preset Bawaan "BPJS Ketenagakerjaan"
1. Buka **Fileora**, pastikan **Preset** terpilih `BPJS Ketenagakerjaan`.
2. Drag & drop file `.zip`, banyak file `.pdf`, atau satu folder ke jendela Fileora.
3. Periksa tabel **Preview**:
   - **OK (hijau)**: Nama perusahaan dan NPP berhasil diambil.
   - **Peringatan (kuning)**: Nama di halaman 2 terpotong/berbeda (Fileora otomatis memakai nama terpanjang dari halaman 1), nama terlalu panjang dipotong aman, atau ada nama kembar (`_2`, `_3`).
   - **Gagal (merah)**: Field tidak ditemukan; nama file asli dipertahankan dan file lain tetap diproses.
   - Kolom **Nama baru** dapat diklik dua kali untuk diedit manual sebelum dieksekusi.
4. Pilih bentuk **Hasil**: `ZIP baru` (default untuk input ZIP), `Folder baru`, atau `Rename di tempat`.
5. Klik **Rename Semua**. Gunakan tombol **Undo** jika ingin mengembalikan rename terakhir, atau **Ekspor Laporan…** untuk menyimpan tabel ke Excel (`.xlsx`) / `.csv`.

### B. Membuat Aturan / Preset Baru (Tanpa Coding)
1. Klik **Aturan Ekstraksi…** lalu isi **Nama preset** baru.
2. Tambahkan sumber field dengan memilih:
   - **Label penanda** (contoh: `NPP :` atau `Nama Perusahaan :`)
   - **Baris setelah kata** (contoh: `Pimpinan`)
   - **Regex (lanjutan)** (contoh: `NPP\s*/\s*Divisi\s*:\s*([A-Z0-9]+)`)
   - **Halaman**: nomor halaman sumber (`1`, `2`, dst., atau `0` untuk semua halaman). Beberapa baris dengan nama field yang sama menjadi sumber cadangan/pembanding sesuai urutan.
3. Atau gunakan **Belajar dari contoh…**: pilih PDF contoh, sorot teks nilai pada tampilan isi PDF, pilih nama field, lalu klik **Buat aturan dari teks yang disorot** — Fileora otomatis membuat aturan dan mengujinya ke seluruh PDF yang sedang dimuat.
4. Atur **Template nama file** (mis. `{nama_perusahaan}_{npp}`), **Pemisah**, **Format huruf**, dan **Panjang maks. nama**, lalu klik **Simpan & Pakai**.

