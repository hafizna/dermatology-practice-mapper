# Deploy ke Streamlit Community Cloud

Repo ini **privat** (data dokter tidak boleh publik). Database
(`data/processed/derm_mapper.sqlite`) sengaja TIDAK masuk git history —
itu aturan `.gitignore` dari awal proyek (spec §3.8) yang tidak diubah
untuk keperluan deploy ini. Sebagai gantinya, database disimpan sebagai
**GitHub Release asset** terpisah dari commit history, dan diunduh
otomatis oleh dashboard saat start di server (lihat `src/deploy_data.py`).

## Setup sekali di awal

1. **Buat Personal Access Token (fine-grained)** khusus untuk ini:
   - https://github.com/settings/tokens → "Generate new token (fine-grained)"
   - Repository access: pilih repo ini saja (`hafizna/dermatology-practice-mapper`)
   - Permissions → Contents: **Read-only** (cukup, jangan kasih Write)
   - Simpan token yang muncul — cuma ditampilkan sekali

2. **Deploy app di share.streamlit.io**:
   - Login pakai akun GitHub yang sama
   - "New app" → pilih repo `dermatology-practice-mapper`, branch `main`,
     file utama `src/app.py`
   - Sebelum/sesudah deploy, buka Settings → Secrets, isi:
     ```toml
     GITHUB_REPO = "hafizna/dermatology-practice-mapper"
     GITHUB_TOKEN = "github_pat_..."  # token dari langkah 1
     ```
     (lihat `.streamlit/secrets.toml.example` untuk field opsional lain)

3. Tunggu build selesai (~1-2 menit) — dashboard otomatis download
   database dari GitHub Release saat pertama kali start.

## Refresh data (rutin, kapan pun kamu mau)

Alur kerja: **jalankan pipeline lokal seperti biasa → publish database
baru ke GitHub Release → reboot app di Streamlit Cloud**.

```bash
# 1. Refresh data seperti biasa (lokal)
python -m src.cli fetch-registry
python -m src.cli scrape --all
python -m src.cli compute-core

# 2. Upload database yang sudah diperbarui ke GitHub Release
python scripts/publish_database_release.py
```

Lalu di share.streamlit.io, buka app ini → menu "⋮" → **Reboot app**
(atau tunggu — app juga otomatis reboot kalau ada push ke `main`).
Reboot diperlukan karena database cuma di-download SEKALI saat app
start, bukan dicek ulang tiap kali halaman dibuka — konsisten dengan
cara kerja yang kamu mau ("dashboard emang cuma display", bukan live-sync).

## Kalau app tidak menampilkan data setelah deploy

Cek di log app (share.streamlit.io → app ini → "Manage app" → lihat log):
- `deploy_data_missing_config` → secrets `GITHUB_REPO`/`GITHUB_TOKEN` belum
  diisi atau salah nama field
- `deploy_data_asset_not_found` → belum pernah jalankan
  `scripts/publish_database_release.py`, atau nama asset berubah
- `deploy_data_download_failed` → token salah/kadaluarsa, atau permission
  token bukan "Contents: Read-only" ke repo yang benar

## Kode yang terlibat

- `src/deploy_data.py` — logika download, dipanggil di awal `src/app.py`
- `scripts/publish_database_release.py` — upload database ke Release
  (pakai `gh` CLI, harus sudah `gh auth login`)
- `.streamlit/secrets.toml.example` — daftar field secrets yang dibutuhkan
