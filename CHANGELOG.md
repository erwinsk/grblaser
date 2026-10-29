# Changelog

Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/); versi mengikuti [SemVer](https://semver.org/lang/id/).

## [0.5.0] - 2026-10-29
### Ditambahkan
- **Rotasi bebas gambar raster**: sudut bebas lewat bulatan putar di kanvas (Shift = 15°) atau kolom "Putar gambar"; sudut kosong tidak ikut dibakar (juga pada mode negatif). Skala gambar yang diputar bersifat proporsional; crop tidak tersedia saat diputar.
- **Teks bisa diedit**: teks dari alat Teks menyimpan parameternya (isi, font, ukuran, gaya, jarak) dan transformasinya; klik ganda atau `Ctrl+Enter` untuk mengedit tanpa kehilangan posisi/skala/putaran. Tersimpan di berkas proyek.
- **Pustaka bahan** (`materials.py`): simpan/terapkan/hapus preset layer (mode, kecepatan, daya, pass, isian, air assist) per mesin; ekspor/impor JSON. Tanpa nilai bawaan (aman bergantung pada laser Anda).
- **Cermin job** (Cermin X / Cermin Y) di panel Pekerjaan untuk mengukir dari sisi belakang; kotak pekerjaan tetap di tempat, pratinjau ikut tercermin, judul jendela menandai `[CERMIN]`, dan pemeriksaan sebelum jalan memberi peringatan. Tersimpan di proyek.
- **Bingkai dengan laser menyala** (penunjuk posisi): opsi + daya (maks 20% di panel) di panel Pekerjaan; konfirmasi setiap kali; tersimpan di profil.
- **Klik kanan di kanvas**: "Pindahkan laser ke sini" kini memakai `$J=` (dapat dibatalkan, semua mode awal), "…& jadikan titik awal pekerjaan", dan "Set nol mesin di posisi laser sekarang…" (dengan konfirmasi).
### Diubah
- Berkas/profil lama dengan `frame_power > 0` otomatis menyalakan opsi bingkai-laser agar perilaku lama tetap sama.

## [0.4.0] - 2026-10-29
### Ditambahkan
- **Pemeriksaan sebelum jalan** (`preflight.py`): kesalahan memblokir (kecepatan ≤ 0, daya di luar 0–100%), peringatan (objek di luar area kerja, daya 0%, isian terlalu rapat,
  S maks / area kerja / `$32` berbeda dari mesin). `$$` dibaca otomatis sebelum mulai. Dialog mulai bernilai bawaan *Tidak* bila ada peringatan; ekspor G-code mencatatnya di konsol.
- **Simpan otomatis & pemulihan** (`autosave.py`): salinan tiap 45 detik di thread latar ke `~/.local/share/grblaser/` bila ada perubahan; ditawarkan saat aplikasi dibuka lagi setelah crash. Dihapus saat simpan atau keluar normal.
- **Mesin proyek**: bila proyek dibuat untuk mesin berbeda dari mesin aktif, aplikasi bertanya (pakai mesin proyek atau tetap mesin aktif); profil baru dibuat otomatis dari proyek.
- **Profil**: perubahan titik awal, jangkar, dan "kembali ke asal" langsung tersimpan ke profil aktif; tombol *Ganti nama* di dialog Pengaturan perangkat.

## [0.3.0] - 2026-10-28
### Ditambahkan
- Grup objek: `Ctrl+G` (grup), `Ctrl+Shift+G` / `Ctrl+U` (pisah); tersimpan di berkas proyek.
- Profil perangkat manual (beberapa mesin), dipilih di panel Laser; Muat/Simpan/Hapus di dialog Pengaturan perangkat.
### Diperbaiki
- Titik 0,0 kini sinkron di penggaris bed, kursor, bar Posisi & Ukuran, Properti, dan penanda laser (`Device.to_user`/`from_user`).
- "Pindah ke titik 0,0 mesin" memindahkan pojok objek terdekat asal mesin ke 0,0.

## [0.2.0]
### Ditambahkan
- Pembuatan job/G-code/pratinjau di thread latar dengan tombol batal; bar Posisi & Ukuran; alat bentuk (poligon, bintang, pustaka bentuk),
  array, boolean & offset (shapely); dither tambahan, halftone, pertajam/haluskan/auto-level, crop.
### Diubah
- Layer bawaan hanya 2 (Garis, Gambar).
### Diperbaiki
- UI membeku saat membuat G-code raster besar; toolbar meluap; kanvas tidak menyesuaikan setelah dock berubah.

## [0.1.0]
- Rilis awal: layer ala LightBurn, raster ala LaserGRBL, impor SVG/DXF, pengirim GRBL, bingkai, pratinjau.
