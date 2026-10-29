# Arsitektur

Ruang desain: mm, (0,0) kiri-atas, Y ke bawah. Koordinat yang tampil di UI = koordinat kerja mesin lewat `Device.to_user` / `from_user`
(asal kiri-bawah → Y naik). Konversi hanya ada di `Device` dan saat G-code dibuat.

| Modul | Peran |
| --- | --- |
| `model.py` | Shape (PathShape, ImageShape), Layer, Device, Project; `Shape.group` untuk grup datar |
| `toolpath.py` | Garis, isi, raster → op `R/F/SF/L/A` dan blok `B` numpy |
| `raster.py` | Dither (error-diffusion, Bayer, halftone), pertajam/haluskan/auto-level |
| `tracing.py` | Marching squares + Ramer-Douglas-Peucker |
| `gcode.py` | `build_job`, `emit_gcode`, `make_transform`, `frame_lines`, `clean_gcode` |
| `sender.py` | Pengirim GRBL *character-counting* (127 byte), banner, status real-time |
| `shapes.py` | Bentuk siap pakai, boolean & offset (Shapely opsional) |
| `preflight.py` | Pemeriksaan murni (tanpa Qt) sebelum job: `check(project, dev, grbl, only_ids)` → daftar `Issue` |
| `autosave.py` | Snapshot proyek ditulis di thread latar; `info/load/clear` untuk pemulihan |
| `materials.py` | Pustaka bahan (preset layer per mesin, ekspor/impor JSON) |
| `profiles.py` | Profil perangkat (QSettings JSON, kunci = nama) |
| `project.py` | Berkas `.glz` (JSON; gambar PNG base64) |
| `ui/` | canvas, panels, dialogs, preview, tasks (thread latar), main_window |

Alur: desain → `build_job` (thread latar, cache per `(rev, only_ids)`) → `emit_gcode` → `clean_gcode` → `start_job(prepared=True)`.
Tes: pytest, Qt offscreen, `tests/fake_grbl.py` (GRBL palsu berbasis pty).
