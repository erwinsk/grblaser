# Pemberitahuan pihak ketiga / Third-party notices

Grblaser sendiri berlisensi MIT (lihat `LICENSE`). Repositori ini **tidak menyertakan (bundle)** kode atau
berkas dari pustaka di bawah; semuanya dipasang pengguna lewat `pip` / paket distro dan tunduk pada lisensinya masing-masing.

| Komponen | Dipakai untuk | Lisensi | Catatan |
| --- | --- | --- | --- |
| [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) | Antarmuka grafis | GPL v3 atau lisensi komersial Riverbank | Lihat catatan di bawah |
| [pyserial](https://github.com/pyserial/pyserial) | Komunikasi serial ke GRBL | BSD-3-Clause | |
| [Pillow](https://github.com/python-pillow/Pillow) | Pemuatan & pengolahan gambar | MIT-CMU (HPND) | |
| [NumPy](https://github.com/numpy/numpy) | Komputasi raster | BSD-3-Clause | |
| [svgelements](https://github.com/meerk40t/svgelements) | Impor SVG | MIT | |
| [ezdxf](https://github.com/mozman/ezdxf) | Impor DXF | MIT | |
| [Shapely](https://github.com/shapely/shapely) | Boolean & offset (opsional) | BSD-3-Clause | |
| [pytest](https://github.com/pytest-dev/pytest) | Pengujian (pengembangan) | MIT | |

## Catatan PyQt5 (penting bagi yang mendistribusikan biner)

Kode sumber Grblaser berlisensi MIT, yang kompatibel dengan GPL. PyQt5 berlisensi GPL v3 / komersial, sehingga
**biner atau paket gabungan** (mis. AppImage, PyInstaller, .deb) yang menyertakan PyQt5 harus mematuhi syarat GPL v3 (atau Anda
memegang lisensi komersial PyQt). Pengguna yang hanya menjalankan dari sumber dengan PyQt5 pasang sendiri tidak terpengaruh.
Bila ingin distribusi biner tanpa syarat GPL, port ke PySide6 (LGPL) adalah jalur yang disarankan.
*Ini informasi umum, bukan nasihat hukum.*

## Algoritma dan data standar

Berikut adalah algoritma/metode umum yang diimplementasikan sendiri dari deskripsi publiknya (bukan salinan kode pihak lain):

- Error-diffusion dithering: Floyd & Steinberg (1976), Atkinson, Jarvis-Judice-Ninke, Stucki, Burkes, Sierra Lite; ordered dithering Bayer (matriks 4×4 dan 8×8).
- Marching squares (pelacakan kontur) dan Ramer-Douglas-Peucker (penyederhanaan garis).
- Algoritma Bézier / pendekatan busur untuk perataan kurva SVG diambil dari `svgelements`.
- Protokol GRBL 1.1 (perintah, `$`-setting, laporan status, *character-counting streaming*) mengikuti
  [dokumentasi publik GRBL](https://github.com/gnea/grbl/wiki). Tidak ada kode GRBL (GPL v3) yang disalin; Grblaser hanya berbicara lewat port serial.

## Gambar contoh

`contoh/contoh_foto.png` dan `contoh/contoh_layer_warna.svg` dibuat sendiri secara prosedural oleh pengembang (gradien, lingkaran,
bentuk geometri); tidak berasal dari pihak ketiga dan dilisensikan MIT bersama proyek ini. Font tidak disertakan;
alat Teks memakai font sistem pengguna.
