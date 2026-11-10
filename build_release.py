"""Build seluruh artefak rilis Fileora:
1. Fileora-1.0.0-Setup.exe     (Windows Installer .exe - Wajib)
2. Fileora-1.0.0-Portable.zip  (Portable .zip tanpa instalasi - Wajib)
3. Fileora-1.0.0-Setup.msi     (Windows Installer Package .msi - Disarankan)
4. Fileora-1.0.0-Portable.7z   (Portable archive .7z ukuran lebih kecil - Opsional)
5. Fileora-1.0.0.sha256        (Verifikasi integritas file - Wajib)
6. Fileora-1.0.0.sig           (Verifikasi tanda tangan digital RSA-SHA256 - Opsional)
7. Fileora-1.0.0.blockmap      (Differential update blockmap - Opsional)
"""
import base64
import datetime
import gzip
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import py7zr
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
VER = "1.0.0"
PUBLISHER = "Muhammad Lutfi Muzaki"
APP_EXE = DIST / "Fileora.exe"

SETUP_EXE = DIST / f"Fileora-{VER}-Setup.exe"
PORTABLE_ZIP = DIST / f"Fileora-{VER}-Portable.zip"
SETUP_MSI = DIST / f"Fileora-{VER}-Setup.msi"
PORTABLE_7Z = DIST / f"Fileora-{VER}-Portable.7z"
SHA256_FILE = DIST / f"Fileora-{VER}.sha256"
SIG_FILE = DIST / f"Fileora-{VER}.sig"
BLOCKMAP_FILE = DIST / f"Fileora-{VER}.blockmap"
PUBKEY_FILE = DIST / "Fileora-PublicKey.pem"


def sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def build_portable_zip() -> None:
    print("[1/7] Membuat Portable .zip:", PORTABLE_ZIP.name)
    PORTABLE_ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(PORTABLE_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(APP_EXE, "Fileora-Portable/Fileora.exe")
        zf.write(ROOT / "README.md", "Fileora-Portable/README.md")
        zf.write(ROOT / "assets" / "fileora.ico", "Fileora-Portable/fileora.ico")
        zf.writestr("Fileora-Portable/PORTABLE.txt",
                    f"Fileora v{VER} (Portable Edition)\nPublisher: {PUBLISHER}\n\n"
                    "Cara pakai: Jalankan langsung Fileora.exe tanpa perlu instalasi.\n")


def build_portable_7z() -> None:
    print("[2/7] Membuat Portable .7z:", PORTABLE_7Z.name)
    PORTABLE_7Z.unlink(missing_ok=True)
    with py7zr.SevenZipFile(PORTABLE_7Z, "w") as archive:
        archive.write(APP_EXE, "Fileora-Portable/Fileora.exe")
        archive.write(ROOT / "README.md", "Fileora-Portable/README.md")
        archive.write(ROOT / "assets" / "fileora.ico", "Fileora-Portable/fileora.ico")


def build_setup_exe() -> None:
    """Kompilasi installer GUI Windows mandiri (.exe) yang mengekstrak payload, membuat shortcut & uninstaller."""
    print("[3/7] Membuat Windows Installer .exe:", SETUP_EXE.name)
    SETUP_EXE.unlink(missing_ok=True)
    cs_code = r"""
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Fileora Setup")]
[assembly: AssemblyDescription("Fileora Windows Installer")]
[assembly: AssemblyCompany("Muhammad Lutfi Muzaki")]
[assembly: AssemblyProduct("Fileora")]
[assembly: AssemblyCopyright("Copyright (C) 2026 Muhammad Lutfi Muzaki")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace FileoraSetup {
    static class Program {
        const string MAGIC = "FILEORA_PAYLOAD_V1";

        [STAThread]
        static int Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string targetDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Fileora");

            if (args.Length > 0 && args[0] == "/uninstall") {
                try {
                    string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "Fileora.lnk");
                    string desktop = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Fileora.lnk");
                    if (File.Exists(startMenu)) File.Delete(startMenu);
                    if (File.Exists(desktop)) File.Delete(desktop);
                    Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Fileora", false);
                    MessageBox.Show("Pintasan dan registrasi Fileora telah dihapus.\nFolder aplikasi: " + targetDir, "Uninstall Fileora", MessageBoxButtons.OK, MessageBoxIcon.Information);
                } catch (Exception ex) {
                    MessageBox.Show(ex.Message, "Fileora", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 0;
            }

            bool silent = args.Length > 0 && (args[0] == "/S" || args[0] == "/silent" || args[0] == "/quiet");
            if (!silent) {
                var res = MessageBox.Show(
                    "Pasang Fileora v1.0.0 ke komputer Anda?\n\n" +
                    "Publisher: Muhammad Lutfi Muzaki\n" +
                    "Lokasi: " + targetDir + "\n\n" +
                    "Klik Yes untuk menginstal dan membuat pintasan di Desktop & Start Menu.",
                    "Fileora v1.0.0 Setup — Muhammad Lutfi Muzaki",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (res != DialogResult.Yes) return 0;
            }

            try {
                Directory.CreateDirectory(targetDir);
                ExtractAppendedZip(Application.ExecutablePath, targetDir);
                string installedExe = Path.Combine(targetDir, "Fileora.exe");
                string setupCopy = Path.Combine(targetDir, "Uninstall-Fileora.exe");
                File.Copy(Application.ExecutablePath, setupCopy, true);

                CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Fileora.lnk"), installedExe, targetDir);
                CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "Fileora.lnk"), installedExe, targetDir);

                using (RegistryKey k = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Fileora")) {
                    k.SetValue("DisplayName", "Fileora");
                    k.SetValue("DisplayVersion", "1.0.0");
                    k.SetValue("Publisher", "Muhammad Lutfi Muzaki");
                    k.SetValue("DisplayIcon", installedExe);
                    k.SetValue("InstallLocation", targetDir);
                    k.SetValue("UninstallString", "\"" + setupCopy + "\" /uninstall");
                    k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                }

                if (!silent) {
                    if (MessageBox.Show("Instalasi Fileora v1.0.0 selesai!\n\nJalankan Fileora sekarang?",
                        "Fileora Setup", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes) {
                        Process.Start(new ProcessStartInfo(installedExe) { WorkingDirectory = targetDir });
                    }
                }
                return 0;
            } catch (Exception ex) {
                if (!silent) MessageBox.Show("Gagal menginstal: " + ex.Message, "Fileora Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        static void ExtractAppendedZip(string selfPath, string destDir) {
            byte[] magicBytes = System.Text.Encoding.ASCII.GetBytes(MAGIC);
            using (FileStream fs = new FileStream(selfPath, FileMode.Open, FileAccess.Read, FileShare.Read)) {
                if (fs.Length < magicBytes.Length + 8) throw new InvalidDataException("Payload installer tidak ditemukan.");
                fs.Seek(-(magicBytes.Length + 8), SeekOrigin.End);
                byte[] buf = new byte[8 + magicBytes.Length];
                fs.Read(buf, 0, buf.Length);
                string tail = System.Text.Encoding.ASCII.GetString(buf, 8, magicBytes.Length);
                if (tail != MAGIC) throw new InvalidDataException("Signature payload installer tidak cocok.");
                long zipLen = BitConverter.ToInt64(buf, 0);
                long zipOffset = fs.Length - magicBytes.Length - 8 - zipLen;
                fs.Seek(zipOffset, SeekOrigin.Begin);
                string tmpZip = Path.Combine(Path.GetTempPath(), "fileora_setup_" + Guid.NewGuid().ToString("N") + ".zip");
                try {
                    using (FileStream outZip = File.Create(tmpZip)) {
                        byte[] chunk = new byte[1048576];
                        long rem = zipLen;
                        while (rem > 0) {
                            int r = fs.Read(chunk, 0, (int)Math.Min(chunk.Length, rem));
                            if (r <= 0) break;
                            outZip.Write(chunk, 0, r);
                            rem -= r;
                        }
                    }
                    using (ZipArchive za = ZipFile.OpenRead(tmpZip)) {
                        foreach (ZipArchiveEntry entry in za.Entries) {
                            if (string.IsNullOrEmpty(entry.Name)) continue;
                            string outFile = Path.Combine(destDir, entry.Name);
                            entry.ExtractToFile(outFile, true);
                        }
                    }
                } finally {
                    if (File.Exists(tmpZip)) File.Delete(tmpZip);
                }
            }
        }

        static void CreateShortcut(string lnkPath, string targetExe, string workDir) {
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            if (t == null) return;
            dynamic shell = Activator.CreateInstance(t);
            dynamic sc = shell.CreateShortcut(lnkPath);
            sc.TargetPath = targetExe;
            sc.WorkingDirectory = workDir;
            sc.IconLocation = targetExe + ",0";
            sc.Description = "Fileora — Rename Dokumen Otomatis Berdasarkan Isi";
            sc.Save();
        }
    }
}
"""
    stub_cs = DIST / "_setup_stub.cs"
    stub_exe = DIST / "_setup_stub.exe"
    payload_zip = DIST / "_payload.zip"
    stub_cs.write_text(cs_code, encoding="utf-8")
    csc = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    ico = ROOT / "assets" / "fileora.ico"
    subprocess.run([
        csc, "/nologo", "/target:winexe", "/optimize+",
        f"/out:{stub_exe}", f"/win32icon:{ico}",
        "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll",
        "/r:Microsoft.CSharp.dll", str(stub_cs)
    ], check=True)

    with zipfile.ZipFile(payload_zip, "w", zipfile.ZIP_STORED) as zf:
        zf.write(APP_EXE, "Fileora.exe")
        zf.write(ROOT / "README.md", "README.md")
        zf.write(ico, "fileora.ico")

    zip_size = payload_zip.stat().st_size
    with open(SETUP_EXE, "wb") as out_f:
        out_f.write(stub_exe.read_bytes())
        with open(payload_zip, "rb") as pz:
            while chunk := pz.read(1 << 20):
                out_f.write(chunk)
        out_f.write(zip_size.to_bytes(8, "little"))
        out_f.write(b"FILEORA_PAYLOAD_V1")

    stub_cs.unlink(missing_ok=True)
    stub_exe.unlink(missing_ok=True)
    payload_zip.unlink(missing_ok=True)


def build_msi() -> None:
    print("[4/7] Membuat Windows Installer Package .msi:", SETUP_MSI.name)
    SETUP_MSI.unlink(missing_ok=True)
    wxs = DIST / "_fileora.wxs"
    ico = (ROOT / "assets" / "fileora.ico").resolve()
    exe = APP_EXE.resolve()
    readme = (ROOT / "README.md").resolve()
    wxs.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name="Fileora"
           Manufacturer="{PUBLISHER}"
           Version="{VER}.0"
           UpgradeCode="9854276C-11BA-4E4A-B2EE-33E45A67D97E"
           Scope="perUser"
           Compressed="yes">
    <MajorUpgrade DowngradeErrorMessage="Versi terbaru Fileora sudah terpasang." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Icon Id="FileoraIcon" SourceFile="{ico}" />
    <Property Id="ARPPRODUCTICON" Value="FileoraIcon" />

    <StandardDirectory Id="LocalAppDataFolder">
      <Directory Id="ProgramsDir" Name="Programs">
        <Directory Id="INSTALLFOLDER" Name="Fileora">
          <Component Id="MainExecutable" Guid="A114276C-11BA-4E4A-B2EE-33E45A67D901">
            <RegistryValue Root="HKCU" Key="Software\\MuhammadLutfiMuzaki\\Fileora" Name="installed" Type="integer" Value="1" KeyPath="yes" />
            <RemoveFolder Id="RemoveInstallFolder" On="uninstall" />
            <RemoveFolder Id="RemoveProgramsDir" Directory="ProgramsDir" On="uninstall" />
            <File Id="FileoraExe" Source="{exe}" />
            <File Id="ReadmeMd" Source="{readme}" />
          </Component>
        </Directory>
      </Directory>
    </StandardDirectory>

    <StandardDirectory Id="ProgramMenuFolder">
      <Component Id="StartMenuShortcut" Guid="B224276C-11BA-4E4A-B2EE-33E45A67D902">
        <RegistryValue Root="HKCU" Key="Software\\MuhammadLutfiMuzaki\\Fileora" Name="shortcut" Type="integer" Value="1" KeyPath="yes" />
        <Shortcut Id="ApplicationStartMenuShortcut"
                  Name="Fileora"
                  Description="Fileora — Rename Dokumen Otomatis Berdasarkan Isi"
                  Target="[INSTALLFOLDER]Fileora.exe"
                  WorkingDirectory="INSTALLFOLDER"
                  Icon="FileoraIcon" />
      </Component>
    </StandardDirectory>

    <Feature Id="MainFeature" Title="Fileora" Level="1">
      <ComponentRef Id="MainExecutable" />
      <ComponentRef Id="StartMenuShortcut" />
    </Feature>
  </Package>
</Wix>
""", encoding="utf-8")
    wix_bin = Path.home() / ".dotnet" / "tools" / "wix.exe"
    subprocess.run([str(wix_bin), "build", str(wxs), "-arch", "x64", "-o", str(SETUP_MSI)], check=True)
    wxs.unlink(missing_ok=True)
    (DIST / f"Fileora-{VER}-Setup.wixpdb").unlink(missing_ok=True)


def build_blockmap(target_file: Path) -> None:
    """Buat file .blockmap terkompresi gzip berisi daftar hash SHA-256 per blok 64 KB untuk differential update."""
    print("[5/7] Membuat Differential Update .blockmap:", BLOCKMAP_FILE.name)
    block_size = 64 * 1024
    blocks = []
    with open(target_file, "rb") as f:
        while chunk := f.read(block_size):
            digest = base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")
            blocks.append({"size": len(chunk), "checksum": digest})
    doc = {
        "version": "2",
        "name": target_file.name,
        "publisher": PUBLISHER,
        "blockSize": block_size,
        "files": [{
            "name": target_file.name,
            "size": target_file.stat().st_size,
            "sha512": base64.b64encode(hashlib.sha512(target_file.read_bytes()).digest()).decode("ascii"),
            "blocks": blocks,
        }],
    }
    raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    with gzip.open(BLOCKMAP_FILE, "wb", compresslevel=9) as gz:
        gz.write(raw)


def build_sha256(artifacts: list[Path]) -> str:
    print("[6/7] Membuat Checksum .sha256:", SHA256_FILE.name)
    lines = []
    for p in artifacts:
        lines.append(f"{sha256_hex(p)}  {p.name}")
    content = "\n".join(lines) + "\n"
    SHA256_FILE.write_text(content, encoding="utf-8")
    return content


def build_signature(sha256_content: str, artifacts: list[Path]) -> None:
    """Buat pasangan kunci RSA-3072 + X.509 Publisher Certificate dan tandatangani seluruh artefak ke .sig."""
    print("[7/7] Membuat Digital Signature .sig:", SIG_FILE.name)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ID"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, PUBLISHER),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{PUBLISHER} Code Signing"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    PUBKEY_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def sign_bytes(data: bytes) -> str:
        sig = key.sign(data, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                       hashes.SHA256())
        return base64.b64encode(sig).decode("ascii")

    manifest_sig = sign_bytes(sha256_content.encode("utf-8"))
    per_file = {}
    for p in artifacts:
        digest_bytes = bytes.fromhex(sha256_hex(p))
        per_file[p.name] = {
            "sha256": digest_bytes.hex(),
            "signature_rsa_pss_sha256": sign_bytes(digest_bytes),
        }
    sig_doc = {
        "application": "Fileora",
        "version": VER,
        "publisher": PUBLISHER,
        "algorithm": "RSA-3072-PSS-SHA256",
        "timestamp_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256_manifest_signature": manifest_sig,
        "files": per_file,
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
    }
    SIG_FILE.write_text(json.dumps(sig_doc, indent=2), encoding="utf-8")


def main() -> None:
    assert APP_EXE.exists(), f"Tidak ditemukan: {APP_EXE}"
    build_setup_exe()
    build_portable_zip()
    build_msi()
    build_portable_7z()
    build_blockmap(SETUP_EXE)
    core_files = [SETUP_EXE, PORTABLE_ZIP, SETUP_MSI, PORTABLE_7Z, BLOCKMAP_FILE, APP_EXE]
    sha_text = build_sha256(core_files)
    build_signature(sha_text, core_files)
    print("\n=== Seluruh Artefak Rilis Selesai Dibuat ===")
    for f in [*core_files, SHA256_FILE, SIG_FILE]:
        print(f" - {f.name:32s} ({f.stat().st_size / (1 << 20):6.2f} MB)")


if __name__ == "__main__":
    main()

