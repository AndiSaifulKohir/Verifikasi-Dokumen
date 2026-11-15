#!/usr/bin/env python3
"""Kirim file verifikasi dokumen (hasil unduhan dari aplikasi) ke repositori GitHub Pages.

Hanya memakai pustaka bawaan Python dan perintah git yang sudah ada di komputer Anda.
Login ke GitHub ditangani oleh git (Git Credential Manager atau SSH), skrip ini tidak
menyimpan atau meminta kunci akses apa pun.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Nama file dari aplikasi: 16 karakter acak, kadang diberi akhiran " (1)" oleh browser.
POLA = re.compile(r"^([A-Za-z0-9_-]{16})(?: \(\d+\))?\.json$")

# Batas waktu (detik) untuk setiap pemanggilan git. Mencegah skrip hang tanpa batas
# saat jaringan lambat atau git menunggu input (mis. konfirmasi SSH).
GIT_TIMEOUT = 60


def git(repo, *args, cek=True, timeout=GIT_TIMEOUT):
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} melebihi batas {timeout} detik.")
    if cek and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} gagal:\n{(r.stderr or r.stdout).strip()}")
    return r


def valid(path):
    """Hanya file berstruktur {v, iv, ct}. File Ekspor (data lengkap) tidak akan lolos."""
    try:
        if path.stat().st_size > 20000:
            return False
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (isinstance(d, dict) and set(d) == {"v", "iv", "ct"} and d["v"] == 1
            and isinstance(d["iv"], str) and isinstance(d["ct"], str))


def belum_terkirim(repo):
    r = git(repo, "rev-list", "--count", "@{u}..HEAD", cek=False)
    return r.returncode == 0 and r.stdout.strip() not in ("", "0")


def jalankan(repo, src, arsip):
    sekarang = time.time()
    berkas = sorted(
        (p for p in src.iterdir()
         if p.is_file() and POLA.match(p.name) and sekarang - p.stat().st_mtime >= 2 and valid(p)),
        key=lambda p: p.stat().st_mtime,
    )
    sisa = belum_terkirim(repo)
    if not berkas and not sisa:
        return 0
    git(repo, "pull", "--rebase", "--autostash")
    ids = set()
    if berkas:
        tujuan = repo / "v"
        tujuan.mkdir(exist_ok=True)
        for p in berkas:  # urut dari terlama, jadi yang terbaru menimpa
            idn = POLA.match(p.name).group(1)
            shutil.copyfile(p, tujuan / f"{idn}.json")
            ids.add(idn)
        git(repo, "add", "v")
        if git(repo, "diff", "--cached", "--quiet", cek=False).returncode != 0:
            git(repo, "commit", "-m", f"Perbarui verifikasi dokumen ({len(ids)} file)")
    git(repo, "push")
    arsip.mkdir(parents=True, exist_ok=True)
    for p in berkas:
        shutil.move(str(p), str(arsip / f"{int(time.time())}-{p.name}"))
    return len(ids)


def _coba_lock():
    """Mencegah dua instance berjalan bersamaan.

    Mengembalikan file handle jika berhasil mendapatkan kunci eksklusif,
    atau None jika instance lain sudah berjalan.
    Menggunakan mekanisme yang tersedia di platform masing-masing.
    """
    lock_path = Path(tempfile.gettempdir()) / "kirim_verifikasi.lock"
    try:
        fh = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except (OSError, IOError):
        try:
            fh.close()
        except Exception:
            pass
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="folder hasil git clone repositori halaman verifikasi")
    ap.add_argument("--src", default=str(Path.home() / "Downloads"), help="folder unduhan browser (bawaan: Downloads)")
    ap.add_argument("--arsip", help="folder penampung file yang sudah terkirim (bawaan: <src>/terkirim)")
    ap.add_argument("--watch", type=int, nargs="?", const=10, metavar="DETIK",
                    help="terus berjalan dan memeriksa tiap beberapa detik (bawaan 10)")
    a = ap.parse_args()
    repo, src = Path(a.repo).expanduser().resolve(), Path(a.src).expanduser().resolve()
    arsip = Path(a.arsip).expanduser().resolve() if a.arsip else src / "terkirim"
    if not src.is_dir():
        sys.exit(f"Folder sumber tidak ada: {src}")
    if git(repo, "rev-parse", "--is-inside-work-tree", cek=False).returncode != 0:
        sys.exit(f"Bukan repositori git: {repo}")

    # Temuan 3: cegah dua instance berjalan bersamaan (terutama mode --watch)
    lock_fh = _coba_lock()
    if lock_fh is None:
        sys.exit("Instance lain dari skrip ini sudah berjalan. Hentikan terlebih dahulu.")

    def sekali():
        try:
            n = jalankan(repo, src, arsip)
            if n:
                print(time.strftime("%H:%M:%S"), f"{n} file verifikasi terkirim.")
            return n
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "GAGAL:", e, file=sys.stderr)
            return -1

    if a.watch:
        print(f"Memantau {src} setiap {a.watch} detik. Tekan Ctrl+C untuk berhenti.")
        try:
            while True:
                sekali()
                time.sleep(a.watch)
        except KeyboardInterrupt:
            pass
    else:
        n = sekali()
        if n == 0:
            print("Tidak ada file verifikasi baru.")
        sys.exit(1 if n < 0 else 0)


if __name__ == "__main__":
    main()
