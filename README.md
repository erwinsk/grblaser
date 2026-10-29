# Grblaser

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Perangkat lunak laser untuk **GRBL 1.1** di **Linux** (PyQt5).
Alur kerja **per layer** (warna → mode, kecepatan, daya) + pengolahan **gambar garis-demi-garis** (dither, grayscale, trace) menjadi G-code.

> *English:* Grblaser is an original, MIT-licensed Linux desktop app for GRBL laser engravers: per-layer job setup,
> raster-image-to-G-code (dithering/grayscale/trace), SVG/DXF import, framing, preview and a streaming GRBL sender.

> ⚠️ **Keselamatan.** Laser dapat membakar dan melukai mata/kulit. Pakai kacamata sesuai panjang gelombang, jangan tinggalkan mesin
> saat bekerja, sediakan pemadam, dan **selalu coba Bingkai (laser mati) dulu**. Perangkat lunak ini diberikan *apa adanya* tanpa jaminan (lihat `LICENSE`).

## Pasang & jalankan

Fedora:

```bash
sudo dnf install python3-qt5 python3-pyserial python3-pillow python3-numpy
pip install --user svgelements ezdxf shapely  # impor SVG & DXF; shapely = boolean & offset (opsional)
sudo usermod -aG dialout $USER                # izin port serial, lalu logout/login
```

Lalu: `./grblaser.sh` atau `python3 -m grblaser [file.svg|file.dxf|gambar.png ...]`

Atau dari repositori: `pip install -e .` (opsional `pip install -e ".[shapes]"` untuk boolean & offset), lalu jalankan `grblaser`.

Distro lain: `pip install -r requirements.txt` (di venv, dengan `--system-site-packages` bila PyQt5 dari paket distro).

## Fitur

**Layer (per warna, gaya alur kerja perangkat lunak laser populer)**
- Hingga 30 layer, tiap layer punya warna, mode (**Garis / Isi / Gambar**), kecepatan, daya (maks & min), jumlah pass, air assist, output & tampil.
- Mode Isi: jarak garis, sudut, crosshatch, dua arah, overscan. Mode Garis: potong bagian dalam dulu, jalur digabung & diurutkan terdekat.
- Impor SVG/DXF otomatis dipisah ke layer **berdasarkan warna garis**. Klik warna di palet untuk memindahkan objek ke layer lain.
- Urutan layer di tabel = urutan pengerjaan (▲▼).

**Gambar (garis-demi-garis)**
- Metode: grayscale daya-variabel (M4 dinamis), ambang 1-bit, dither Floyd-Steinberg / Atkinson / Bayer, dan vektorisasi kontur (trace).
- Resolusi garis/mm, sudut pindai, kecerahan, kontras, gamma, ambang, negatif; preset Foto / Logo / Grayscale / Sketsa. Pratinjau dither langsung di kanvas.
- Kirim file G-code (`.gcode/.nc`) langsung ke laser, dengan pratinjau.

**Desain**: persegi, elips, garis, teks (font sistem → kontur), pindah / skala proporsional / putar, cermin, rata tengah, duplikat, undo-redo, seret-lepas file.

**Pekerjaan & mesin**
- Pratinjau hasil (rapat energi), estimasi waktu, **bingkai** (laser mati atau daya kecil), mulai/jeda/henti.
- Titik awal: **absolut** atau **dari posisi laser saat ini** dengan 9 titik jangkar.
- Pengirim GRBL: streaming *character-counting* (buffer 127 byte), status real-time (posisi kerja/mesin, state), jog, home `$H`, buka kunci `$X`, set nol, override kecepatan/daya, konsol perintah, tes laser (harus diizinkan dulu), baca `$30/$130/$131/$32` dari mesin.

## Pengaturan awal yang penting

1. **Laser → Pengaturan perangkat**: isi ukuran area kerja (`$130/$131`), `S` maks (`$30`), gunakan **M4** dengan `$32=1` (mode laser), dan asal mesin (umumnya kiri-bawah). Tombol *Baca dari GRBL* mengisi otomatis.
2. Atur *Set nol di sini* (atau Home) sebelum mulai; mode "dari posisi laser saat ini" tidak butuh homing.
3. Coba **Bingkai** dulu, dan selalu pakai kacamata pelindung sesuai panjang gelombang laser.

## Pintasan

`Ctrl+G` grup · `Ctrl+Shift+G` pisah grup · `Ctrl+Enter` edit teks · `S` pilih · `R` persegi · `E` elips · `L` garis · `T` teks · `F` zoom bed · `F5` pratinjau · `F6` bingkai · `F7` mulai · `F8` jeda · `F9` henti · `Ctrl+I` impor · `Ctrl+E` ekspor G-code · `Ctrl+D` duplikat · panah = geser 1 mm (Shift 10, Ctrl 0,1).
Kanvas: roda = zoom, tombol tengah / Spasi+seret = geser, sudut kotak = skala (Shift = bebas), bulatan atas = putar (Shift = 15°).

## Struktur & tes

```
LICENSE, THIRD_PARTY_NOTICES.md, CHANGELOG.md, pyproject.toml
.github/         CI (pytest), templat isu & PR
docs/            ARCHITECTURE.md
contoh/          berkas contoh (SVG, PNG)
grblaser/        model, toolpath (garis/isi/raster), raster (dither), gcode, sender (GRBL), importers, project
grblaser/ui/     canvas, panels, dialogs, preview, main_window
tests/           pytest; fake_grbl.py = GRBL palsu berbasis pty (tanpa hardware)
```

```bash
python3 -m pytest tests          # QT_QPA_PLATFORM=offscreen sudah diatur otomatis
```

Format proyek: `.glz` (JSON; gambar tersimpan sebagai PNG base64).

## Pembaruan (v0.2)

- **Performa**: pembuatan job, G-code, pratinjau, dan trace berjalan di thread latar (dengan tombol Batal); job dipakai ulang selama desain tidak berubah; pembersihan jutaan baris G-code tidak lagi membekukan jendela; progres pengiriman dibatasi agar UI tetap lancar. Pratinjau gambar di kanvas dihitung async.
- **Layer bawaan**: hanya 2 (Garis, Gambar). Layer lain dibuat otomatis saat impor SVG/DXF berwarna, atau lewat tombol **+**.
- **Posisi & Ukuran**: bar di atas kanvas (X, Y, Lebar, Tinggi, kunci rasio, mm/inci, titik acuan 3×3); sumbu Y mengikuti koordinat mesin.
- **Alat bentuk**: persegi (radius sudut), elips, poligon, bintang, garis, polyline, teks; bentuk siap pakai (hati, roda gigi, cincin, plus, panah, bulan sabit); rata/sebar, array grid & melingkar; boolean (gabung/kurangi/irisan) dan offset kontur.
- **Raster**: dither Floyd-Steinberg, Atkinson, Jarvis, Stucki, Burkes, Sierra Lite, Bayer 4×4/8×8, halftone titik & garis, plus pertajam, haluskan, auto-level, potong (crop) dan info DPI.

## Pembaruan (v0.3)

- **Koordinat sinkron**: penggaris bed, kursor (toolbar), bar Posisi & Ukuran, panel Properti, dan penanda laser memakai satu konversi (`Device.to_user`). Titik 0,0 = asal mesin (kiri-bawah → Y naik ke atas). Titik acuan bawaan bar posisi = pojok objek terdekat asal mesin. Tombol *Pindah ke titik 0,0 mesin* menaruh pojok itu tepat di 0,0 (ditandai "0,0" merah di bed).
- **Grup**: `Ctrl+G` gabungkan objek terpilih, `Ctrl+Shift+G` (atau `Ctrl+U`) pisahkan. Memilih satu anggota memilih seluruh grup; salin/array membuat grup baru; tersimpan di file proyek.
- **Profil perangkat** (manual, mis. dua mesin berbeda): pilih di bagian atas panel Laser; ⚙ membuka Pengaturan perangkat dengan tombol *Muat / Simpan profil / Hapus*. Satu profil = ukuran bed, S maks, asal, port & baud terakhir, titik awal, dll. Tidak bisa ganti profil saat job berjalan.

## Asal-usul, atribusi, dan lisensi

- **Orisinal.** Seluruh kode di `grblaser/` dan `tests/` ditulis untuk proyek ini dan dilisensikan **MIT** (`LICENSE`).
  Kode dibuat dengan bantuan asisten AI (Claude, Anthropic) di bawah arahan dan pengujian pemelihara; status hak cipta atas keluaran AI
  dapat berbeda antar yurisdiksi.
- **Terinspirasi, bukan salinan.** Gagasan alur kerja per layer (LightBurn) dan pengolahan gambar per baris (LaserGRBL) hanyalah *inspirasi fungsional*.
  Tidak ada kode, aset, ikon, atau teks antarmuka dari kedua program (maupun dari GRBL) yang disalin. Algoritma standar (dithering, marching squares,
  Ramer-Douglas-Peucker) diimplementasikan sendiri dari deskripsi publiknya; protokol GRBL mengikuti dokumentasi publiknya.
- **Pustaka pihak ketiga** (PyQt5, pyserial, Pillow, NumPy, svgelements, ezdxf, Shapely) tidak disertakan dalam repositori; daftar lisensinya ada di
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). **Perhatian:** PyQt5 berlisensi GPL v3/komersial, sehingga *biner* yang dikemas bersama PyQt5 harus mematuhi GPL v3.
- **Merek dagang.** LightBurn, LaserGRBL, GRBL, Ortur, dan Linux adalah milik pemiliknya masing-masing. Proyek ini **tidak berafiliasi dengan,
  disponsori, atau didukung oleh** mereka; nama hanya disebut untuk menjelaskan kompatibilitas (mis. diuji pada Ortur Laser Master 3 Pro).
- Gambar contoh di `contoh/` dibuat sendiri (prosedural) dan berlisensi MIT.

## Kontribusi

Lihat [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`SECURITY.md`](SECURITY.md), dan [`CHANGELOG.md`](CHANGELOG.md).
Arsitektur singkat: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Pembaruan (v0.4)

- **Pemeriksaan sebelum jalan**: sebelum *Mulai*, aplikasi memeriksa objek di luar area kerja, daya/kecepatan layer tak valid, dan kecocokan S maks, area kerja, dan `$32` dengan mesin (dibaca dari `$$`). Kesalahan memblokir; peringatan harus disetujui.
- **Simpan otomatis**: salinan berkala di `~/.local/share/grblaser/autosave.glz`; ditawarkan untuk dipulihkan setelah crash.
- **Mesin proyek & profil**: proyek mengingat mesin pembuatnya dan menanyakan bila berbeda dari mesin aktif; perubahan titik awal/jangkar tersimpan otomatis ke profil; profil bisa diganti namanya.

## Pembaruan (v0.5)

- **Rotasi bebas gambar**, **teks yang bisa diedit** (klik ganda / `Ctrl+Enter`), **pustaka bahan** (tombol 📚 Bahan… di panel Layer), **Cermin X/Y** untuk mengukir dari sisi belakang, **bingkai dengan laser menyala** (penunjuk posisi, daya rendah, konfirmasi), dan menu klik kanan kanvas untuk memindahkan laser / menjadikan titik itu awal pekerjaan.

## Batasan saat ini

- Gambar yang diputar bebas hanya bisa diskalakan proporsional dan tidak bisa di-crop (setel putaran ke 0° dulu).
- Belum ada node editing (mengubah titik kurva satu per satu).
- Boolean & offset butuh `shapely`; tanpa itu, menu-nya hanya menampilkan petunjuk pemasangan.
- Hanya sumbu X/Y (tanpa Z/rotary); belum ada kamera/penyejajaran.
- Teks SVG harus diubah ke path dulu (Inkscape: *Path → Object to Path*) atau memakai alat Teks.
