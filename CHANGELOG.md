# Changelog

Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/); versi mengikuti [SemVer](https://semver.org/lang/id/).

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
