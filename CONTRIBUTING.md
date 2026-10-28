# Berkontribusi

Terima kasih! Kontribusi (isu, perbaikan, fitur, terjemahan) disambut.

1. Fork dan buat cabang: `git checkout -b fitur/nama-singkat`.
2. Pasang: `pip install -e ".[dev]"` (PyQt5 bisa dari paket distro).
3. Jalankan tes: `python3 -m pytest tests` (Qt otomatis *offscreen*; tidak butuh perangkat keras).
4. Sertakan tes untuk perubahan perilaku. Jaga kode berkomentar ringkas dan konsisten dengan gaya yang ada.
5. Buka Pull Request dengan penjelasan *apa* dan *mengapa*.

## Aturan hak cipta
- Kirim hanya kode yang Anda tulis sendiri atau yang lisensinya kompatibel dengan MIT, dan cantumkan sumber/lisensinya di PR
  serta di `THIRD_PARTY_NOTICES.md`.
- **Jangan** menyalin kode dari LightBurn (proprietari), LaserGRBL, GRBL, atau proyek GPL lain ke repositori ini.
- Dengan mengirim kontribusi, Anda setuju kontribusi itu dilisensikan MIT (DCO: tambahkan `Signed-off-by` bila memungkinkan).

## Keselamatan
Perubahan pada pembuatan G-code, pengirim, atau koordinat wajib diuji dengan `tests/fake_grbl.py` dan, bila mungkin, dibingkai dulu (laser mati)
di mesin nyata. Laporkan kerentanan lewat `SECURITY.md`.
