# Dermatology Practice Opportunity Mapper (Jabodetabek)

Alat lokal untuk memetakan **peluang bergabung praktik dermatologi** di rumah
sakit swasta area Jabodetabek. Aplikasi ini **tidak** memprediksi demand pasien
maupun probabilitas diterima bekerja — ia hanya menyusun ranking RS berdasarkan
indikasi ruang praktik (supply dokter kulit dan coverage jadwal) yang dapat
diaudit ke sumber aslinya.

Baca [`PROJECT_SPEC.md`](PROJECT_SPEC.md) untuk konteks, prinsip, dan rencana
fase kerja lengkap sebelum mengubah apa pun di repo ini.

## Prinsip inti

- **Jangan pernah mengarang data.** Field yang tidak ditemukan diisi `None`
  dan dicatat statusnya, bukan ditebak.
- **Setiap angka harus punya provenance** (`source_url`, `source_tier`,
  `scraped_at`) sehingga dapat diverifikasi manual.
- **Skor bukan probabilitas.** `opportunity_score` mengurutkan ruang praktik
  internal RS, bukan peluang diterima kerja.
- **Unknown ≠ zero.** RS tanpa data dokter/jadwal berstatus `unknown`, bukan
  otomatis dianggap kosong atau ramai.
- Detail lengkap ada di §3 `PROJECT_SPEC.md` ("Prinsip yang Tidak Boleh
  Dilanggar") dan Appendix B ("Guiding Decision Rules").

## Setup

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows Git Bash:
source .venv/Scripts/activate

pip install -r requirements.txt

# Hanya diperlukan bila adapter dinamis (mis. Siloam) membutuhkan Playwright:
playwright install chromium
```

## CLI

```bash
python -m src.cli --help
python -m src.cli init-db
python -m src.cli fetch-registry
python -m src.cli scrape --group eka
python -m src.cli scrape --all
python -m src.cli compute-core
python -m src.cli serve
```

## Struktur repo

Lihat §7 `PROJECT_SPEC.md` untuk struktur folder yang diharapkan. Ringkasnya:

- `config/` — semua threshold, bobot skor, prime-time window, preferred
  hospital list, alias/koreksi di `manual_overrides.csv`, dan fasilitas
  terverifikasi yang belum ada di OSM pada `manual_hospitals.csv`.
- `data/raw/`, `data/processed/` — **di-gitignore**; berisi data dokter yang
  tidak boleh masuk repo publik (spec §3.8).
- `data/reference/` — data publik non-sensitif yang boleh di-commit (mis.
  boundary kecamatan).
- `src/registry/` — pengumpulan & dedup master hospital registry (Overpass,
  Kemkes).
- `src/scrapers/` — satu adapter per grup RS + base class umum.
- `src/parsing/` — deteksi kredensial dermatologi, parser jadwal, normalisasi
  nama dokter.
- `src/enrich/` — geocoding dan (fase lanjut) competitive/market enrichment.
- `src/metrics/`, `src/scoring/` — coverage matrix, doctor-hours,
  `opportunity_score`.
- `src/app.py` — dashboard Streamlit.

## Status pengembangan

Proyek berjalan fase-demi-fase sesuai `PROJECT_SPEC.md` §9. Target rilis
saat ini: **V1 — Practice Vacancy Mapper** (Fase 0–8), lihat §17 untuk
definition of done. Layer Competitive Context (V1.5) dan Market
Attractiveness (V2) sengaja belum dikerjakan.

## Data & etika scraping

- `robots.txt` dicek sebelum scraping domain baru.
- Rate limit default: 1 request / 2 detik per domain.
- `User-Agent` mencantumkan kontak, bukan menyamar sebagai browser biasa.
- Tidak ada proxy rotation, CAPTCHA bypass, atau fingerprint evasion.
- Prioritas sumber: **Tier 1** situs resmi RS → **Tier 2** aggregator
  (Alodokter/Halodoc) → **Tier 3** input/override manual.

## Testing

```bash
pytest
```

Parser diuji offline dengan fixture yang dianonimkan — tidak ada network
call di test suite.
