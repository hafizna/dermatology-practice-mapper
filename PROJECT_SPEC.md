# Prompt untuk Claude Code — Dermatology Practice Opportunity Mapper (Jabodetabek)

> **Cara pakai:** simpan file ini sebagai `PROJECT_SPEC.md` di root repo, lalu buka Claude Code dan mulai dengan:
>
> *"Baca `PROJECT_SPEC.md` sampai selesai. Kerjakan hanya Fase 0 dan Fase 1. Jangan lanjut ke fase berikutnya sebelum saya konfirmasi. Kalau menemukan hambatan data atau asumsi yang material, laporkan apa adanya—jangan mengarang solusi atau data."*
>
> **Jangan minta Claude Code mengerjakan semua fase sekaligus.** Bagian tersulit adalah registry, scraping dokter/jadwal, normalisasi, dan audit kualitas data. Market enrichment baru berguna setelah core data tersebut dapat dipercaya.

---

## 1. Konteks, Objective, dan Prinsip Produk

Saya seorang dokter spesialis kulit (Sp.KK / Sp.DVE) dan ingin membangun alat untuk memetakan **peluang bergabung praktik di rumah sakit Jabodetabek**.

Tujuan utama aplikasi ini **bukan** menghitung demand dermatologi secara sempurna dan **bukan** memprediksi probabilitas diterima bekerja di sebuah RS. Tujuan utamanya adalah menjawab pertanyaan praktis:

> **“Di rumah sakit swasta yang memang layak saya pertimbangkan, mana yang supply dokter kulitnya relatif tipis dan coverage jadwalnya masih menyisakan ruang praktik yang menarik?”**

Karena itu sistem dibangun dalam tiga lapisan yang harus tetap terpisah secara konseptual:

### Layer A — Core Practice Opportunity

Menilai **ruang praktik di dalam RS itu sendiri** berdasarkan:

- jumlah dokter kulit unik;
- total jam praktik dermatologi per minggu;
- jumlah sesi per minggu;
- coverage jadwal mingguan;
- gap pada prime-time;
- gap akhir pekan;
- pola dokter yang sama praktik di banyak RS, bila datanya tersedia.

Ini adalah **MVP dan prioritas tertinggi**.

### Layer B — Competitive Context

Menilai apakah RS dengan internal supply rendah benar-benar berada di area yang relatif underserved atau justru dikelilingi banyak alternatif dermatologi:

- jumlah RS lain dalam radius tertentu;
- jumlah dokter kulit unik di area sekitar;
- total doctor-hours dermatologi di area sekitar;
- klinik dermatologi/aesthetic clinic yang dapat diidentifikasi secara andal;
- overlap dokter antar-RS.

Layer ini dikembangkan **setelah Layer A stabil**.

### Layer C — Market Attractiveness

Menilai konteks komersial wilayah, bukan vacancy internal RS:

- populasi catchment;
- residential affluence proxy;
- office/daytime population density;
- premium residential / apartment / retail clusters;
- healthcare referral ecosystem;
- indikator lifestyle yang relevan bila sumber datanya dapat dipertanggungjawabkan.

Layer ini adalah **enrichment**, bukan syarat MVP dan tidak boleh diam-diam dicampur ke `opportunity_score` utama.

### Output akhir

Aplikasi Streamlit lokal, dijalankan on-demand, dengan:

- peta Folium berbasis OpenStreetMap;
- ranking RS;
- heatmap coverage jadwal;
- panel detail dokter dan sesi;
- breakdown komponen skor;
- competitive-context panel;
- market-attractiveness panel opsional;
- data-quality dashboard;
- export CSV.

---

## 2. Scope dan Non-Goals

### Scope utama

1. Jabodetabek.
2. Semua RS tetap boleh masuk ke **master registry**.
3. Ranking utama default difokuskan ke **preferred private hospitals / bonafide private hospitals** yang dikonfigurasi eksplisit.
4. Situs resmi RS adalah sumber utama dokter dan jadwal.
5. Aggregator hanya fallback.
6. Input manual selalu tersedia untuk koreksi kasus yang tidak dapat diotomasi dengan andal.

### Non-goals untuk MVP

Jangan terlebih dahulu membangun:

- model machine learning;
- prediksi jumlah pasien;
- prediksi revenue;
- driving-time isochrone berbayar;
- scraping agresif atau anti-bot evasion;
- "AI recommendation" yang tidak dapat diaudit;
- satu skor gabungan besar yang mencampur vacancy, demografi, income, dan personal preference.

MVP harus tetap **explainable, auditable, dan actionable**.

---

## 3. Prinsip yang Tidak Boleh Dilanggar

Baca bagian ini dulu dan patuhi di seluruh fase.

### 3.1 Jangan pernah mengarang data

Kalau scraper gagal atau field tidak ditemukan:

- isi `None`;
- simpan error/status;
- catat di `data_quality`;
- jangan mengisi koordinat, jumlah dokter, jadwal, kelas RS, atau demographic proxy dengan tebakan.

Data kosong lebih baik daripada data salah.

### 3.2 Setiap record wajib punya provenance

Minimal simpan:

- `source_url`;
- `source_tier`;
- `scraped_at`;
- `scraper_version`;
- bila relevan `raw_payload_path` / cache key.

Saya harus bisa memverifikasi manual angka penting yang tampil di dashboard.

### 3.3 Skor bukan probabilitas

Gunakan nama:

- `opportunity_score` untuk Layer A;
- `competitive_context_score` bila benar-benar dibutuhkan;
- `market_attractiveness_score` untuk Layer C;
- `practice_fit_score` hanya pada tahap akhir jika personal preference ikut dihitung.

Jangan pernah menamai skor tersebut `probability` atau menyiratkan peluang diterima oleh RS.

### 3.4 Jangan menyembunyikan komponen skor

Semua komponen dan nilai mentah harus dapat dilihat di UI. Jangan tampilkan hanya angka akhir.

### 3.5 Unknown bukan zero

RS tanpa data dokter/jadwal:

- **bukan** berarti `0 dokter`;
- **bukan** berarti `100% gap`;
- **tidak boleh** otomatis mendapat opportunity score tinggi.

Gunakan status `unknown` sampai terdapat bukti yang memadai.

### 3.6 Hormati sumber data

- cek `robots.txt` sebelum scraping domain baru;
- rate limit default 1 request / 2 detik per domain;
- set `User-Agent` yang jujur dan berisi kontak;
- kalau path dilarang, berhenti dan laporkan;
- jangan pakai proxy rotation, CAPTCHA bypass, fingerprint evasion, atau teknik agresif lain.

### 3.7 Prioritaskan API / JSON di atas HTML

Sebelum membuat parser HTML:

- cek DevTools Network;
- cek `/api/`;
- cek `_next/data/` untuk Next.js;
- cek `/wp-json/` untuk WordPress;
- cek embedded JSON / structured data.

Gunakan HTML parsing hanya jika sumber terstruktur tidak tersedia.

### 3.8 Data dokter tidak masuk repository publik

Masukkan ke `.gitignore` sejak commit pertama:

- `data/raw/`;
- `data/processed/`;
- cache scraping yang berisi nama dokter.

Fixture test yang di-commit harus dianonimkan.

---

## 4. Product Architecture

Gunakan pemisahan berikut:

```text
Hospital Registry
      ↓
Doctor & Schedule Collection
      ↓
Parsing / Normalization / Dedup
      ↓
CORE PRACTICE OPPORTUNITY  ← MVP
      ↓
Competitive Context        ← V1.5
      ↓
Market Attractiveness      ← V2
      ↓
Personal Practice Fit      ← V3
      ↓
Dashboard / Shortlist / Export
```

**Penting:** Layer yang lebih tinggi tidak boleh menghalangi deliverable layer sebelumnya.

Contoh: kegagalan memperoleh data high-income households tidak boleh membuat dashboard dokter/jadwal tidak bisa digunakan.

---

## 5. Preferred Hospital Universe

Semua RS tetap dikumpulkan ke master registry, tetapi aplikasi harus mempunyai mode ranking:

1. `Preferred Private` — default;
2. `All Private`;
3. `All Hospitals`.

Daftar preferred group disimpan di config, bukan hardcoded.

Contoh:

```yaml
# config/hospital_preferences.yaml
preferred_groups:
  - Eka Hospital
  - Siloam
  - Mitra Keluarga
  - RS Pondok Indah
  - Mayapada
  - EMC
  - Primaya
  - Bethsaida
  - Brawijaya
  - Hermina

include_ownership:
  - swasta

exclude_hospital_types: []

# Opsional dan SUBJEKTIF. Jangan perlakukan sebagai fakta pasar.
manual_preference:
  Eka Hospital: 1.00
  RS Pondok Indah: 1.00
  Siloam: 0.95
  Mayapada: 0.95
```

`manual_preference` hanya digunakan untuk `practice_fit_score` pada V3. Jangan dimasukkan ke `opportunity_score` Layer A.

---

## 6. Stack

| Lapisan | Pilihan | Alasan |
|---|---|---|
| Scraping statis | `httpx` + `selectolax` | cepat dan ringan |
| Scraping dinamis | `playwright` Chromium | hanya jika situs benar-benar butuh JS |
| Storage | SQLite via `sqlite3` / `sqlalchemy` | zero-config, cukup untuk skala ini |
| Data wrangling | `pandas`, `geopandas` | tabular + geospatial |
| String matching | `rapidfuzz` | dedup RS dan nama |
| Geospatial | `shapely`, Overpass API, Nominatim fallback | registry + spatial enrichment |
| Peta | `folium` + `streamlit-folium` | sederhana dan lokal |
| UI | `streamlit` | cepat untuk decision-support tool |
| Config | `pydantic-settings` + YAML | semua threshold/bobot editable |
| Test | `pytest` + golden fixtures | parser dapat diuji offline |

Python 3.11+. Gunakan `uv` kalau tersedia; fallback ke `pip` + `requirements.txt`.

---

## 7. Struktur Repo yang Diharapkan

```text
.
├── PROJECT_SPEC.md
├── README.md
├── config/
│   ├── sources.yaml
│   ├── scoring.yaml
│   ├── prime_time.yaml
│   ├── hospital_preferences.yaml
│   └── manual_overrides.csv
├── data/
│   ├── raw/                         # .gitignore
│   ├── processed/                   # .gitignore
│   └── reference/                   # data publik yang boleh di-commit
├── src/
│   ├── cli.py
│   ├── models.py
│   ├── registry/
│   │   ├── osm.py
│   │   ├── kemkes.py
│   │   └── merge.py
│   ├── scrapers/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── manual.py
│   │   ├── eka.py
│   │   ├── siloam.py
│   │   ├── mitra_keluarga.py
│   │   └── ...
│   ├── parsing/
│   │   ├── credentials.py
│   │   ├── schedule.py
│   │   └── names.py
│   ├── enrich/
│   │   ├── geocode.py
│   │   ├── competition.py            # V1.5
│   │   ├── population.py             # V2
│   │   ├── affluence.py              # V2
│   │   └── office_density.py         # V2
│   ├── metrics/
│   │   ├── coverage.py
│   │   ├── supply.py
│   │   └── opportunity.py
│   ├── scoring/
│   │   ├── core.py
│   │   ├── competitive.py
│   │   ├── market.py
│   │   └── fit.py
│   └── app.py
├── scripts/
│   └── check_sources.py
└── tests/
    ├── fixtures/
    ├── test_credentials.py
    ├── test_schedule.py
    ├── test_names.py
    └── test_opportunity.py
```

---

## 8. Data Model

Gunakan schema relasional yang tetap audit-friendly.

### 8.1 Hospital

```text
Hospital:
    id
    name
    name_normalized
    aliases[]
    group
    ownership
    hospital_class
    hospital_type
    address
    kelurahan
    kecamatan
    kota_kab
    lat
    lon
    geocode_source
    geocode_confidence
    website
    source_url
    source_tier
    scraped_at
    data_status
```

`data_status` minimal:

- `complete`;
- `partial`;
- `unknown`;
- `scrape_failed`;
- `manual`.

### 8.2 Doctor

```text
Doctor:
    id
    hospital_id
    raw_name
    clean_name
    normalized_person_key
    credentials[]
    is_dermatologist
    subspecialty
    source_url
    source_tier
    scraped_at
```

`normalized_person_key` dipakai untuk mendeteksi dokter yang sama di beberapa RS. Jangan menganggap dedup 100% pasti; simpan confidence bila matching fuzzy digunakan.

### 8.3 ScheduleSlot

```text
ScheduleSlot:
    id
    doctor_id
    hospital_id
    day_of_week           # 0=Senin ... 6=Minggu
    start_time
    end_time
    raw_text
    parse_confidence      # high / medium / low
    source_url
    scraped_at
```

`raw_name` dan `raw_text` wajib disimpan apa adanya untuk audit.

### 8.4 HospitalPracticeMetrics

```text
HospitalPracticeMetrics:
    hospital_id
    n_dermatologists_unique
    n_sessions_week
    doctor_hours_week
    prime_time_doctor_hours
    weekend_doctor_hours
    coverage_ratio_all
    coverage_ratio_prime
    coverage_ratio_weekend
    prime_gap_ratio
    weekend_gap_ratio
    longest_prime_gap_minutes
    doctors_with_external_overlap
    opportunity_score
    metrics_version
    calculated_at
```

### 8.5 CompetitiveContextMetrics — V1.5

```text
CompetitiveContextMetrics:
    hospital_id
    radius_km
    nearby_hospitals_count
    nearby_derm_hospitals_count
    nearby_dermatologists_unique
    nearby_derm_doctor_hours_week
    nearby_derm_clinics_count
    local_supply_index
    data_quality
    calculated_at
```

### 8.6 MarketAttractivenessMetrics — V2

```text
MarketAttractivenessMetrics:
    hospital_id
    catchment_population
    population_year
    residential_affluence_proxy
    office_density_proxy
    premium_residential_proxy
    premium_retail_proxy
    healthcare_ecosystem_proxy
    market_attractiveness_score
    data_quality
    calculated_at
```

Setiap proxy harus menyimpan `source`, `year`, dan definisinya. Jangan memakai nama seperti `income` bila data yang dipakai sebenarnya hanya proxy.

---

# 9. Fase Kerja

Setiap fase mempunyai **STOP GATE**. Setelah deliverable selesai, tampilkan hasil kepada saya dan jangan lanjut sebelum saya konfirmasi.

---

## Fase 0 — Bootstrap

Scaffold:

- repo;
- dependency;
- `.gitignore`;
- SQLite schema;
- config loader;
- structured logging;
- CLI entrypoint.

CLI minimal:

```bash
python -m src.cli fetch-registry
python -m src.cli scrape --group eka
python -m src.cli scrape --all
python -m src.cli compute-core
python -m src.cli serve
```

Tambahkan placeholder command untuk fase selanjutnya, tetapi jangan implementasikan enrichment sebelum waktunya.

**Deliverable:** tree repo + schema database + CLI help.

**STOP GATE 0:** berhenti dan laporkan.

---

## Fase 1 — Hospital Master Registry

Ini fondasi. Jangan mulai scraping jadwal sebelum registry cukup bersih.

### 1. Overpass API

Query `amenity=hospital` untuk bbox Jabodetabek sekitar:

```text
-6.50,106.55,-5.95,107.10
```

Verifikasi bbox sendiri. Jangan menganggap koordinat ini final bila area relevan terpotong.

Ambil minimal:

- nama;
- koordinat;
- alamat/tag yang tersedia;
- website bila ada.

### 2. rs.kemkes.go.id

Cari endpoint API / structured endpoint terlebih dahulu.

Target field:

- nama RS;
- kelas;
- kepemilikan;
- alamat;
- kemungkinan metadata layanan.

Jangan mengandalkan Kemkes sebagai satu-satunya sumber.

### 3. Merge & dedup

Gunakan `rapidfuzz` setelah normalisasi:

- lowercase;
- hapus `RS`, `RSU`, `RSUD`, `Rumah Sakit` untuk matching;
- buang tanda baca;
- normalisasi whitespace;
- simpan nama asli.

Threshold awal sekitar `85`, tetapi jangan auto-merge kasus borderline tanpa audit.

Simpan semua nama alternatif di `aliases[]`.

### 4. Preferred hospital flag

Join dengan `hospital_preferences.yaml` dan tambahkan:

- `is_preferred_group`;
- `preferred_rank_group` bila diperlukan.

Jangan menghapus RS non-preferred dari registry.

**Deliverable Fase 1:**

- tabel `hospitals`;
- jumlah RS total;
- breakdown private/public;
- jumlah preferred-private;
- jumlah dengan/tanpa koordinat;
- kandidat duplicate yang unresolved;
- sampel 20 baris.

**STOP GATE 1:** berhenti dan tampilkan hasil.

---

## Fase 2 — Scraper Framework + Satu Adapter Pilot

Buat `BaseScraper`:

```python
class BaseScraper(ABC):
    group_name: str
    base_urls: list[str]
    requires_js: bool = False

    @abstractmethod
    def discover_hospitals(self) -> list[HospitalRef]: ...

    @abstractmethod
    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]: ...
```

Base class wajib menyediakan:

- retry + exponential backoff;
- per-domain rate limiter;
- raw response cache;
- structured logging;
- provenance metadata;
- cache replay untuk development;
- error classification.

Simpan raw response ke pola:

```text
data/raw/{group}/{YYYY-MM-DD}/{hospital_slug}/...
```

### Caching wajib

Saat mengembangkan parser, gunakan cache. Jangan hit server berulang kali hanya untuk mengubah selector.

### Adapter pilot

Implementasikan **Eka Hospital terlebih dahulu** bila reconnaissance masih valid dan sumbernya tetap paling bersih.

Sebelum coding parser:

1. periksa endpoint JSON;
2. dokumentasikan struktur sumber;
3. baru pilih JSON vs HTML vs Playwright.

Buat fixture anonim dan test offline.

**Deliverable Fase 2:** satu adapter end-to-end + fixture + test + contoh data terparse.

**STOP GATE 2:** berhenti untuk review pola adapter.

---

## Fase 3 — Scale Adapter ke Target Groups

Target awal:

- Eka Hospital;
- Siloam;
- Mitra Keluarga;
- Hermina;
- Primaya;
- EMC;
- Bethsaida;
- RS Pondok Indah;
- Mayapada;
- Brawijaya.

Tambahkan aggregator fallback untuk RS yang tidak punya sumber resmi memadai:

- Alodokter;
- Halodoc;
- sumber lain hanya setelah dinilai legal/teknis dan provenance-nya jelas.

### Source priority

```text
Tier 1 = situs resmi RS / grup RS
Tier 2 = aggregator
Tier 3 = manual override
```

**Catatan:** `manual_override` memiliki priority tertinggi untuk nilai yang secara eksplisit dioverride, tetapi tetap simpan nilai hasil scraper untuk audit.

Jangan mengganti Tier 1 dengan Tier 2 hanya karena parsing Tier 2 lebih mudah.

**Deliverable Fase 3:** coverage report per grup dan error report.

**STOP GATE 3.**

---

## Fase 4 — Parsing dan Identity Resolution

Ini bagian paling rawan salah.

### 4.1 `parsing/credentials.py`

Deteksi dermatologis.

Valid examples:

- `Sp.KK`;
- `Sp.DV`;
- `Sp.DVE`;
- `SpKK`;
- `Sp. K.K.`;
- `Sp.DVE(K)`;
- `Sp.KK(K)`;
- `Dermatologi dan Venereologi`;
- `Dermatovenereologi`;
- `Kulit dan Kelamin`.

False positive yang harus dihindari:

- `Sp.KKLP`;
- `Sp.KJ`;
- `Sp.KFR`;
- `Sp.KL`;
- `Sp.KO`;
- `Sp.KN`;
- `Sp.KG`;
- `Sp.KKV`.

Regex polos `Sp\.?\s*KK` tidak boleh digunakan sendiri.

Minimal starting point:

```regex
(?<![A-Za-z])Sp\.?\s*K\.?\s*K\.?(?!LP|V)(?![A-Za-z])
```

Tulis minimal 30 unit tests.

Untuk Tier 1 yang URL-nya sudah spesifik dermatologi, credential parser berperan sebagai **validator/cross-check**, bukan satu-satunya filter.

### 4.2 `parsing/schedule.py`

Contoh variasi:

- `Senin, Rabu, Jumat 08.00 - 12.00`;
- `Sen-Jum: 17:00-20:00`;
- `Selasa & Kamis 13.00-15.00 WIB`;
- `Sabtu 09.00 - selesai`;
- `Dengan Perjanjian`.

Parser berlapis:

1. exact known patterns;
2. normalized patterns;
3. cautious fallback.

Output harus punya:

- parsed days;
- start/end;
- raw text;
- confidence.

Kalau tidak dapat diparsing:

- simpan raw text;
- `parse_confidence = low`;
- **jangan** dipakai menghitung gap;
- tampilkan di Data Quality.

`selesai` → `end_time = None`, jangan ditebak.

### 4.3 `parsing/names.py`

Normalisasi nama dokter untuk mendeteksi orang yang sama di beberapa RS:

- buang gelar depan/belakang untuk matching;
- lowercase;
- normalisasi whitespace;
- pertahankan raw name;
- fuzzy match hanya bila perlu;
- simpan match confidence.

Jangan merge dua dokter hanya berdasarkan nama belakang atau token pendek.

**Deliverable Fase 4:** test report + sampel edge cases + unresolved cases.

**STOP GATE 4.**

---

## Fase 5 — Geocoding dan Spatial Integrity

Mayoritas koordinat idealnya sudah dari OSM/registry.

Untuk sisanya gunakan Nominatim fallback dengan:

- rate limit maksimum 1 request/detik;
- `User-Agent` berisi kontak;
- persistent cache;
- `geocode_confidence`;
- jangan geocode ulang alamat yang sama tanpa alasan.

Kalau hasil hanya level kecamatan/kota, tandai jelas.

Jangan menggunakan koordinat centroid kecamatan seolah koordinat RS presisi.

**Deliverable Fase 5:** geocode-quality report.

**STOP GATE 5.**

---

# 10. Core Practice Opportunity — MVP

## Fase 6 — Coverage Matrix dan Supply Metrics

Bangun matriks coverage per RS:

```text
7 hari × slot 30 menit × 07:00–21:00
```

Setiap cell = jumlah dokter kulit yang sedang praktik pada slot tersebut.

### Prime-time

Prime-time harus configurable.

Default awal:

```yaml
# config/prime_time.yaml
weekday_evening:
  days: [0, 1, 2, 3, 4]
  start: "17:00"
  end: "21:00"

saturday:
  days: [5]
  start: "08:00"
  end: "14:00"
```

Jangan menganggap definisi ini fakta epidemiologis. Ini adalah operational assumption dan harus mudah diubah.

### Metric wajib

Hitung minimal:

```text
n_dermatologists_unique
n_sessions_week
doctor_hours_week
prime_time_doctor_hours
weekend_doctor_hours
coverage_ratio_all
coverage_ratio_prime
coverage_ratio_weekend
prime_gap_ratio
weekend_gap_ratio
longest_prime_gap_minutes
```

### Doctor count tidak cukup

Contoh interpretasi yang harus didukung sistem:

```text
RS A: 3 dokter, tetapi hanya 6 doctor-hours/week
RS B: 2 dokter, tetapi 30 doctor-hours/week
```

Jangan menyimpulkan supply RS B lebih rendah hanya karena jumlah dokter lebih sedikit.

### Cross-hospital overlap

Jika identity resolution cukup baik, tambahkan:

```text
doctors_with_external_overlap
mean_external_hospital_count
```

Ini **context metric**, bukan otomatis hukuman/bonus. Dokter yang praktik di banyak RS bisa berarti supply lokal terfragmentasi, tetapi interpretasinya harus transparan.

**Deliverable Fase 6:** metric table + heatmap contoh minimal 5 RS.

**STOP GATE 6.**

---

## Fase 7 — Core Opportunity Score

Tujuan skor ini hanya:

> mengurutkan RS berdasarkan indikasi **ruang praktik internal yang belum terisi penuh**.

Jangan memasukkan populasi, household income, office density, atau brand preference ke skor ini.

### 7.1 Normalisasi

Gunakan peer-relative normalization di dalam universe aktif (`Preferred Private`, `All Private`, atau `All Hospitals`) agar threshold tidak terlalu arbitrary.

Gunakan winsorization / percentile normalization bila outlier ekstrem mengganggu.

Simpan nilai mentah dan nilai normalized.

### 7.2 Komponen awal

Contoh konfigurasi awal:

```yaml
# config/scoring.yaml
core_opportunity:
  dermatologist_count_scarcity: 0.20
  doctor_hours_scarcity: 0.30
  prime_time_gap: 0.35
  weekend_gap: 0.15
```

Bobot harus configurable.

### 7.3 Definisi konseptual

#### `dermatologist_count_scarcity`

Semakin sedikit dokter unik dibanding peer group, semakin tinggi scarcity.

Jangan hardcode "<4 pasti opportunity" sebagai satu-satunya definisi. Threshold `<4` boleh tetap ditampilkan sebagai descriptive flag.

#### `doctor_hours_scarcity`

Semakin rendah `doctor_hours_week` dibanding peer group, semakin tinggi scarcity.

Metric ini lebih penting daripada count mentah.

#### `prime_time_gap`

Proporsi slot prime-time yang tidak memiliki dokter kulit.

#### `weekend_gap`

Proporsi slot akhir pekan configurable yang kosong.

### 7.4 Jangan double-count secara tersembunyi

Karena doctor count dan doctor-hours berkorelasi:

- tampilkan korelasi sederhana antar-komponen;
- pertahankan bobot moderat;
- jangan menambahkan banyak turunan coverage yang mengukur hal yang sama ke skor.

### 7.5 Status data minimum

`opportunity_score` hanya dihitung bila:

- status layanan dermatologi diketahui;
- doctor list cukup reliable;
- schedule coverage melewati minimum completeness threshold.

Contoh:

```yaml
minimum_schedule_completeness: 0.70
```

Jika tidak memenuhi, tampilkan `score_status = insufficient_data`.

### 7.6 Hospital tanpa dokter kulit

Kasus `n_dermatologists_unique = 0` harus dibedakan:

1. **Confirmed zero** — layanan tersedia tetapi benar-benar tidak ada dokter terdaftar;
2. **No dermatology service** — mungkin bukan target opportunity;
3. **Unknown** — scraper/data tidak cukup.

Jangan menyamakan ketiganya.

**Deliverable Fase 7:** ranking core opportunity + breakdown per komponen.

**STOP GATE 7.**

---

## Fase 8 — Dashboard MVP

Dashboard V1 harus sudah berguna **tanpa** population/affluence layer.

### 8.1 Top-level controls

- Universe: `Preferred Private / All Private / All Hospitals`;
- kota/kabupaten;
- hospital group;
- kelas RS;
- jumlah dokter;
- data status;
- minimum schedule completeness.

### 8.2 Ranking table

Kolom minimal:

| Field | Keterangan |
|---|---|
| Hospital | nama RS |
| Group | grup RS |
| Derm | dokter kulit unik |
| Sessions/wk | sesi per minggu |
| Derm hrs/wk | doctor-hours/minggu |
| Prime coverage | coverage prime-time |
| Sat/weekend gap | gap akhir pekan |
| Opportunity | skor Layer A |
| Data quality | complete/partial/unknown |

Sortable + export CSV.

### 8.3 Heatmap jadwal

Klik RS → tampilkan 7 × slot waktu.

Bedakan:

- kosong;
- 1 dokter;
- 2+ dokter;
- jadwal unstructured/unknown.

Unknown jangan divisualisasikan sama dengan kosong.

### 8.4 Map

Marker per RS.

Default map metric bisa dipilih:

- `opportunity_score`;
- doctor count;
- doctor-hours/week;
- prime-time gap.

Jangan gunakan ukuran marker berdasarkan populasi sebelum V2 tersedia.

Popup:

- nama RS;
- group;
- kelas;
- dokter kulit;
- doctor-hours/week;
- prime-time coverage;
- opportunity breakdown;
- source link;
- last scraped;
- data-quality status.

### 8.5 Data Quality tab

Wajib tampilkan:

- total RS master;
- total preferred-private;
- Tier 1 success;
- Tier 2 fallback;
- manual records;
- no-data hospitals;
- schedule parse high/medium/low;
- source freshness;
- scrape failures;
- unresolved doctor identity matches.

**Deliverable Fase 8:** usable MVP.

**STOP GATE 8.**

---

# 11. Competitive Context — V1.5

Jangan mulai bagian ini sebelum MVP Layer A dapat digunakan.

## Fase 9 — Nearby Dermatology Supply

Pertanyaan:

> **“RS ini tampak punya internal vacancy, tetapi apakah area sekitarnya juga relatif kurang terlayani?”**

### Radius

Buat configurable, contoh:

```yaml
competition_radius_km: [3, 5, 10]
```

Mulai dengan Euclidean/haversine radius. Jangan langsung pakai paid routing API.

### Metric prioritas

1. `nearby_dermatologists_unique`;
2. `nearby_derm_doctor_hours_week`;
3. `nearby_derm_hospitals_count`;
4. `nearby_hospitals_count`;
5. `nearby_derm_clinics_count` bila sumbernya reliable;
6. doctor overlap across nearby hospitals.

### Kenapa ini lebih penting daripada income proxy di tahap awal

RS dengan 1 dokter kulit tetapi dikelilingi 15–20 dermatologis di radius kecil adalah konteks yang berbeda dari RS dengan 2 dokter tetapi hampir tidak ada supply dermatologi lain di area tersebut.

Karena itu **nearby dermatologist supply harus dikembangkan sebelum household-income atau office-density enrichment**.

### Competitive score

Kalau dibuat, tampilkan terpisah. Jangan mengubah historical `opportunity_score`.

Contoh output:

```text
Core Opportunity          84/100
Nearby Derm Supply        Low
Competitive Pressure      Low
```

**Deliverable Fase 9:** competitive-context panel + map layer.

**STOP GATE 9.**

---

# 12. Market Attractiveness — V2

Market layer menjawab:

> **“Kalau ada ruang praktik, apakah surrounding market cukup menarik secara demografis/komersial?”**

Ini **bukan** pengganti core opportunity.

## Fase 10 — Population Catchment

### Sumber

Populasi per kecamatan dari BPS atau sumber resmi lain yang dapat diaudit.

Simpan:

- tahun data;
- source URL/file;
- geographic level.

### Metode MVP

- buffer 5 km configurable;
- intersect dengan polygon kecamatan;
- alokasikan populasi proporsional terhadap area irisan.

Catat keterbatasan:

- mengasumsikan distribusi penduduk uniform di dalam kecamatan;
- tidak memperhitungkan jalan/travel time;
- catchment antar-RS overlap;
- bukan estimasi pasien aktual.

Jangan menyebut hasil sebagai "true catchment".

Gunakan nama seperti:

```text
estimated_population_within_5km
```

---

## Fase 11 — Residential Affluence Proxy

Jangan menggunakan istilah `high_income_households` kecuali benar-benar punya data household income.

Jika data income langsung tidak tersedia, gunakan **proxy yang diberi nama sesuai sumber**.

Contoh kandidat bila legal dan tersedia:

- expenditure per capita tingkat wilayah;
- property / land-value proxy;
- premium residential clusters;
- premium apartment density;
- private/international school density;
- premium retail presence.

Setiap proxy wajib menyimpan:

```text
metric_name
raw_value
normalization_method
source
source_year
geographic_resolution
confidence
```

Jangan gabungkan proxy yang sangat berkorelasi tanpa memeriksa redundancy.

---

## Fase 12 — Office / Daytime Population Proxy

Office density relevan terutama untuk praktik setelah jam kerja dan catchment pekerja.

Possible signals:

- office tower count;
- office floor-area bila tersedia;
- commercial POI density;
- CBD/business-district classification;
- daytime population dataset bila tersedia.

Jangan menganggap "banyak kantor = pasti banyak pasien". Tampilkan sebagai context.

Untuk dermatologi medis versus estetika, interpretasi signal dapat berbeda.

---

## Fase 13 — Healthcare & Lifestyle Ecosystem

Opsional.

Potential context:

- premium hospitals;
- pediatricians;
- obstetricians;
- plastic surgeons;
- endocrinologists;
- aesthetic clinics;
- malls;
- fitness centers;
- premium retail clusters.

Jangan scrape kategori luas tanpa source strategy dan definisi yang jelas.

Tujuan layer ini adalah **exploration**, bukan menciptakan kesan precision yang tidak ada.

---

## Fase 14 — Market Attractiveness Score

Hanya buat setelah data V2 cukup matang.

Contoh komponen:

```yaml
market_attractiveness:
  catchment_population: 0.30
  residential_affluence_proxy: 0.30
  office_density_proxy: 0.20
  healthcare_ecosystem_proxy: 0.20
```

Bobot awal bersifat hipotesis dan harus ditampilkan sebagai editable.

### Important

Jangan membuat:

```text
final_score = opportunity + income + population + hospital_class
```

sebagai satu angka tanpa breakdown.

Lebih baik tampilkan dua dimensi:

```text
X = Core Practice Opportunity
Y = Market Attractiveness
```

Interpretasi:

```text
Market Attractiveness
HIGH
  ↑
  │ investigate           ★ PRIORITY TARGET
  │
  │
  ├────────────────────────────────────→ Core Practice Opportunity
  │
  │ low priority          competitive / saturated
  ↓
LOW
```

Target utama = kanan-atas.

**Deliverable Fase 14:** quadrant/scatter + market panel.

**STOP GATE 14.**

---

# 13. Personal Practice Fit — V3

Tahap ini boleh memasukkan preferensi pribadi yang memang subjektif.

Possible inputs:

- hospital brand preference;
- maximum travel time;
- preferred practice days;
- preferred evening/weekend slots;
- preference RSIA vs general hospital;
- desired practice intensity;
- geographic preference.

Ini bukan data pasar. Simpan sebagai user config.

Contoh:

```yaml
practice_fit:
  max_travel_minutes: 45
  preferred_days: [1, 3, 5]
  preferred_time_windows:
    - ["17:00", "21:00"]
```

Baru di fase ini boleh dibuat `practice_fit_score` yang menggabungkan:

- Core Opportunity;
- Market Attractiveness;
- personal preference.

Tetap tampilkan semua komponennya.

---

# 14. Testing

## Parser tests

- fixture HTML/JSON per adapter;
- nama dokter dianonimkan;
- no network calls di test suite;
- minimal 30 credential parser tests;
- schedule parser edge cases;
- cross-hospital name dedup edge cases.

## Metric tests

Test minimal:

- satu dokter dua sesi sehari;
- jadwal overlapping;
- jadwal `end_time=None`;
- low-confidence schedule tidak dihitung sebagai empty gap;
- doctor count dan doctor-hours menghasilkan ranking yang berbeda pada fixture yang disengaja;
- unknown hospital tidak mendapat opportunity score.

## Golden tests

Simpan expected normalized output untuk beberapa fixture agar perubahan parser tidak diam-diam mengubah ranking.

## Source drift

`scripts/check_sources.py` harus:

- memeriksa selector / expected field;
- membedakan network failure vs structural change;
- melaporkan adapter yang kemungkinan rusak.

---

# 15. Data Quality dan Confidence

Dashboard tidak boleh memberi false precision.

Untuk setiap RS, tampilkan minimal:

```text
registry_confidence
specialist_list_confidence
schedule_completeness
schedule_parse_confidence
geocode_confidence
competitive_data_confidence
market_data_confidence
```

Boleh disederhanakan di UI menjadi:

- High;
- Medium;
- Low;
- Unknown.

Tetapi nilai underlying tetap disimpan.

### Score eligibility

Jangan calculate score bila minimum input tidak terpenuhi.

Contoh:

```text
Opportunity: insufficient data
Reason: only 40% of listed dermatologists have parsable schedules
```

Lebih baik daripada angka seperti `92/100` yang tidak bisa dipercaya.

---

# 16. Yang Sebaiknya Ditanyakan ke Saya

Tanya, jangan asumsikan, bila menemukan:

- situs butuh login;
- bot protection yang tidak dapat dilewati secara normal;
- robots.txt melarang path;
- format jadwal ambigu;
- satu dokter yang mungkin duplicate tetapi fuzzy match tidak pasti;
- definisi Jabodetabek yang ambigu;
- klasifikasi hospital group yang tidak jelas;
- source data affluence / office density yang memerlukan pilihan metodologis material;
- data manual yang konflik dengan official source.

Kalau ragu antara menebak dan bertanya: **bertanya**.

Untuk detail minor yang tidak mempengaruhi correctness, pilih default yang sederhana dan dokumentasikan.

---

# 17. Definition of Done per Product Stage

## V1 — Practice Vacancy Mapper

Selesai jika aplikasi dapat menjawab dengan cukup reliable:

> “Dari preferred private hospitals, mana yang internal dermatology supply dan schedule coverage-nya paling tipis?”

Wajib tersedia:

- master registry;
- dokter kulit;
- jadwal;
- doctor-hours;
- heatmap;
- core opportunity ranking;
- provenance;
- data quality.

**Population dan affluence belum diperlukan.**

## V1.5 — Competitive Context

Selesai jika aplikasi dapat menjawab:

> “RS ini tampak kosong secara internal, tetapi seberapa padat supply dermatologi di sekitarnya?”

## V2 — Market Attractiveness

Selesai jika aplikasi dapat menjawab:

> “Dari opportunity yang ada, area mana yang surrounding market-nya paling menarik?”

## V3 — Personal Shortlist

Selesai jika aplikasi dapat menjawab:

> “Mana target yang paling cocok untuk saya approach terlebih dahulu?”

---

# Appendix A — Hasil Reconnaissance Awal (per Agustus 2026)

> **Verifikasi ulang sebelum menulis adapter.** Struktur situs dapat berubah tanpa pemberitahuan. Catatan di bawah adalah reconnaissance, bukan kontrak API.

## Strategi utama — scraping langsung ke situs grup RS

Aggregator seperti Alodokter/Halodoc hanya untuk RS yang tidak mempunyai sumber resmi memadai.

Source precedence default:

```text
Tier 1 = situs resmi RS
Tier 2 = aggregator
Tier 3 = input manual / override terverifikasi
```

Untuk field yang dioverride secara manual, manual override menang tetapi historical/source values tetap disimpan.

---

## Eka Hospital — kandidat adapter pertama

Reconnaissance awal:

- halaman per RS mengikuti pola `https://booking.ekahospital.com/hospital/{slug-rs}/{slug-spesialisasi}`;
- halaman agregat spesialisasi pernah tersedia di `https://booking.ekahospital.com/speciality/kulit-dan-kelamin`;
- jadwal per dokter relatif terstruktur dan dapat memiliki lebih dari satu sesi per hari;
- slug spesialisasi dapat tidak konsisten antar-cabang, misalnya `kulit-dan-kelamin` vs `dermatologi-venerologi`.

Jangan hardcode slug. Enumerasi dari structured navigation / endpoint bila tersedia.

Cabang Jabodetabek yang pernah teridentifikasi:

- BSD;
- Depok;
- Bekasi;
- Cibubur;
- Permata Hijau;
- MT Haryono;
- Grand Family;
- RSIA PIK;
- RSIA Pluit.

Filter cabang di luar scope.

---

## Siloam Hospitals — kemungkinan butuh browser rendering

Reconnaissance awal:

- pola URL pernah mengikuti `https://www.siloamhospitals.com/cari-dokter/dermatologi-kulit/rumah-sakit/{slug-rs}`;
- dapat terdapat slug bahasa Inggris pada sebagian halaman;
- HTTP sederhana pernah terkena bot detection.

Strategy:

1. cari endpoint JSON dahulu;
2. bila halaman butuh JS, gunakan Playwright secara normal;
3. bila masih diblokir, berhenti;
4. jangan menggunakan evasion;
5. gunakan Tier 2 fallback bila diperlukan dan tandai jelas.

---

## Implikasi desain penting

Jika source resmi sudah difilter berdasarkan spesialisasi:

1. `credentials.py` berfungsi sebagai validator/cross-check;
2. tetap crawl daftar dokter lengkap bila memungkinkan untuk mendeteksi dokter kulit yang masuk label seperti `Estetika` / `Bedah Kulit` / kategori lain;
3. laporkan selisih specialist-page vs credential-based discovery di Data Quality.

---

## Ekspektasi cakupan

Jangan menganggap target 10 grup besar mewakili seluruh RS Jabodetabek.

Dashboard wajib melaporkan:

- total RS master list;
- preferred-private count;
- Tier 1 coverage;
- Tier 2 coverage;
- manual coverage;
- no-data count.

RS tanpa data harus tampil sebagai **unknown**, bukan seolah tidak mempunyai kompetitor.

---

# Appendix B — Guiding Decision Rules

Gunakan aturan ini bila ragu saat implementasi.

### Rule 1

**Scraper accuracy > feature count.**

Lebih baik 20 RS reliable daripada 100 RS dengan parsing jadwal meragukan.

### Rule 2

**Doctor-hours > doctor count** untuk memahami effective clinical supply.

Doctor count tetap penting, tetapi tidak boleh menjadi satu-satunya ranking input.

### Rule 3

**Nearby dermatologist supply > affluence proxy** sebagai enrichment pertama setelah MVP.

### Rule 4

**Opportunity ≠ market attractiveness ≠ personal preference.**

Jangan mencampur ketiganya tanpa label terpisah.

### Rule 5

**Unknown ≠ zero.**

Ini berlaku untuk dokter, jadwal, population proxy, dan competitive data.

### Rule 6

**Every score must be explainable from raw metrics.**

Saya harus dapat melihat mengapa satu RS berada di atas RS lain.

### Rule 7

**Do not over-engineer before a useful shortlist exists.**

V1 dianggap sukses bila saya sudah dapat membuka dashboard dan menemukan beberapa RS yang layak diperiksa/di-approach secara manual, meskipun V2 belum dibangun.
