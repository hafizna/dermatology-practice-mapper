"""Fase 8: Streamlit Dashboard MVP — spec §10 Fase 8.

V1 scope only: universe filter, ranking table, schedule heatmap, map,
data-quality tab. Deliberately NO population/affluence/office-density
layer (that's V2's Market Attractiveness, out of scope here). A
hospital-only competitive-context pilot for Bintaro, BSD, and Kuningan
is included as the first V1.5 slice; it stays separate from the V1
opportunity score.

Data freshness: this page reads whatever is currently in
data/processed/derm_mapper.sqlite. It does not re-scrape or recompute
metrics itself — run `python -m src.cli fetch-registry`, `scrape --all`,
and `compute-core` (which chains Fase 6 + Fase 7) beforehand to refresh.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

# Streamlit Cloud runs this file directly (streamlit run src/app.py) without
# necessarily putting the repo root on sys.path first, which breaks the
# `import src.xxx` absolute imports used throughout this codebase (works
# fine locally where the CLI/tests always run from the repo root). Insert
# the repo root explicitly, before any src.* import, so both environments
# behave the same way.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import folium
import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit_folium import st_folium

from src.config import get_competitive_pilot_config
from src.db import get_engine
from src.deploy_data import ensure_database_present
from src.enrich.competition import compute_competitive_pilot
from src.map_categories import (
    MAP_METRICS,
    calculate_category_boundaries,
    classify_marker,
    format_metric_legend,
    metric_value_participates_in_scale,
)
from src.metrics.coverage import (
    DAY_START_MINUTES,
    N_DAYS,
    N_SLOTS_PER_DAY,
    build_matrix_cells,
    usable_slots_for_hospital,
)
from src.models import (
    DataStatus,
    DermatologistCountStatus,
    Doctor,
    Hospital,
    HospitalPracticeMetrics,
    ParseConfidence,
    ScheduleSlot,
    SourceTier,
)

st.set_page_config(page_title="Derm Practice Opportunity Mapper", layout="wide", page_icon="🩺")

_DAY_NAMES_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


@st.cache_resource
def _get_engine():
    return get_engine()


def _load_dashboard_dataframe(universe: str) -> pd.DataFrame:
    """One row per Hospital with its latest HospitalPracticeMetrics
    (left join — a hospital with no Fase 6/7 row yet still appears, with
    every metric column as None/NaN, per spec's "unknown != zero").

    Rows manually confirmed as a duplicate of another Hospital (see
    Hospital.duplicate_of_hospital_id docstring) are excluded entirely —
    the same exclusion applied in src/scoring/core.py's _universe_query,
    kept consistent here since the dashboard reads Hospital directly
    rather than going through that function.
    """
    engine = _get_engine()
    with Session(engine) as session:
        rows = session.execute(
            select(Hospital, HospitalPracticeMetrics)
            .join(HospitalPracticeMetrics, HospitalPracticeMetrics.hospital_id == Hospital.id, isouter=True)
            .where(Hospital.duplicate_of_hospital_id.is_(None))
        ).all()

        records = []
        for hospital, metrics in rows:
            records.append(
                {
                    "hospital_id": hospital.id,
                    # display_alias (config/manual_overrides.csv) is shown
                    # INSTEAD of the raw OSM name when present — e.g.
                    # OSM's "Rumah Sakit Siloam" really is MRCCC Siloam
                    # Semanggi, and showing the recognizable alias alone
                    # reads much easier than "Rumah Sakit Siloam (a.k.a.
                    # MRCCC Siloam Semanggi)" (user feedback 2026-08-09).
                    # Hospital.name itself is UNCHANGED in the database —
                    # this is purely a display-layer substitution, so the
                    # raw OSM provenance is still intact for audit.
                    "Hospital": hospital.display_alias or hospital.name,
                    "Group": hospital.preferred_rank_group or "(bukan target group)",
                    "is_preferred_group": hospital.is_preferred_group,
                    "ownership": hospital.ownership,
                    "kota_kab": hospital.kota_kab or "Tidak diketahui",
                    "hospital_class": hospital.hospital_class or "Tidak diketahui",
                    "data_status": hospital.data_status.value if hospital.data_status else "unknown",
                    "lat": hospital.lat,
                    "lon": hospital.lon,
                    "derm_status": metrics.dermatologist_count_status.value if metrics else "unknown",
                    "Derm": metrics.n_dermatologists_unique if metrics else None,
                    "Sessions/wk": metrics.n_sessions_week if metrics else None,
                    "Derm hrs/wk": metrics.doctor_hours_week if metrics else None,
                    # Ditampilkan sebagai GAP (rasio jam ramai yang masih
                    # KOSONG dari dokter kulit), bukan coverage (rasio
                    # yang sudah terisi) -- supaya arah bacanya konsisten
                    # dengan "Sat/weekend gap" dan "Opportunity" di
                    # tabel yang sama: makin TINGGI = makin besar
                    # peluang, bukan sebaliknya.
                    "Gap jam ramai": metrics.prime_gap_ratio if metrics else None,
                    "Sat/weekend gap": metrics.weekend_gap_ratio if metrics else None,
                    "Opportunity": metrics.opportunity_score if metrics else None,
                    "score_status": metrics.score_status.value if metrics else "insufficient_data",
                    "score_status_reason": metrics.score_status_reason if metrics else "Belum dihitung (jalankan Fase 6/7).",
                    "schedule_completeness": metrics.schedule_completeness if metrics else None,
                    "doctors_with_external_overlap": metrics.doctors_with_external_overlap if metrics else None,
                    "mean_external_hospital_count": metrics.mean_external_hospital_count if metrics else None,
                }
            )
        df = pd.DataFrame.from_records(records)

    if universe == "preferred_private":
        df = df[df["is_preferred_group"] == True]  # noqa: E712
    elif universe == "all_private":
        df = df[df["ownership"] == "swasta"]
    # "all_hospitals": no filter

    return df


def _data_quality_label(row: pd.Series) -> str:
    # spec §8.2 "Data quality" column: complete/partial/unknown.
    #
    # confirmed_zero gets its own branch, checked FIRST and separately
    # from score_status=="ok" — a bug caught via dashboard review
    # 2026-08-09: confirmed_zero hospitals ARE score_status=="ok" (Fase
    # 7 treats "confirmed zero doctors" as complete information, not
    # missing data — see src/scoring/core.py's eligibility gate), but
    # they have NO schedule data to be "complete" about (there's no
    # doctor to have a schedule). Labeling them "complete" the same way
    # as a hospital with actually-parsed doctor schedules is misleading
    # — a confirmed_zero hospital's opportunity_score is a certainty
    # rather than an estimate, which "complete" doesn't quite capture
    # either, so it gets a distinct label instead of borrowing either
    # existing bucket's meaning.
    if row["derm_status"] == "confirmed_zero":
        return "confirmed_zero"
    if row["score_status"] == "ok":
        return "complete"
    if row["derm_status"] == "has_doctors":
        return "partial"
    return "unknown"


# ---------------------------------------------------------------------
# Sidebar — 8.1 Top-level controls
# ---------------------------------------------------------------------

st.title("🩺 Dermatology Practice Opportunity Mapper — Jabodetabek")
st.caption(
    "Decision-support tool untuk memetakan RS dengan indikasi ruang praktik "
    "dermatologi internal yang belum terisi penuh. V1 (Practice Vacancy Mapper) — "
    "tidak memasukkan populasi/affluence/office density (lihat PROJECT_SPEC.md §10)."
)

if not ensure_database_present():
    # Only genuinely blocking on a fresh host with the database missing
    # AND no GITHUB_REPO/GITHUB_TOKEN secrets configured — a normal local
    # run (database already on disk from the CLI pipeline) never reaches
    # this branch, since ensure_database_present() is a no-op when the
    # file already exists. See src/deploy_data.py docstring.
    st.error(
        "Database tidak ditemukan dan tidak berhasil diunduh dari GitHub Release. "
        "Kalau ini jalan lokal: jalankan `python -m src.cli init-db` lalu "
        "`python -m src.cli fetch-registry`. Kalau ini deploy Streamlit Cloud: "
        "cek secrets GITHUB_REPO/GITHUB_TOKEN sudah diisi (lihat DEPLOY.md)."
    )
    st.stop()

try:
    _probe_engine = _get_engine()
    with Session(_probe_engine) as _s:
        _hospital_count = _s.query(Hospital).count()
except Exception as exc:  # pragma: no cover - dev convenience only
    st.error(
        "Database belum bisa diakses. Jalankan `python -m src.cli init-db` lalu "
        f"`python -m src.cli fetch-registry` terlebih dahulu.\n\nDetail: {exc}"
    )
    st.stop()

if _hospital_count == 0:
    st.info("Registry masih kosong. Jalankan `python -m src.cli fetch-registry` (Fase 1) terlebih dahulu.")
    st.stop()

st.sidebar.header("Filter")

universe = st.sidebar.selectbox(
    "Universe",
    options=["preferred_private", "all_private", "all_hospitals"],
    format_func=lambda u: {
        "preferred_private": "Preferred Private (default)",
        "all_private": "All Private",
        "all_hospitals": "All Hospitals",
    }[u],
    index=0,
)

df = _load_dashboard_dataframe(universe)
df["Data quality"] = df.apply(_data_quality_label, axis=1)

kota_options = sorted(df["kota_kab"].dropna().unique().tolist())
selected_kota = st.sidebar.multiselect("Kota/Kabupaten", options=kota_options, default=[])

group_options = sorted(df["Group"].dropna().unique().tolist())
selected_groups = st.sidebar.multiselect("Hospital group", options=group_options, default=[])

# Filter "Kelas RS" (spec §8.1) sengaja TIDAK ditampilkan -- data
# hospital_class tidak tersedia sama sekali dari OSM (sumber Fase 1),
# selalu "Tidak diketahui" untuk semua 554 RS. Filter yang selalu punya
# satu pilihan kosong cuma bikin bingung, bukan berguna.

min_derm = st.sidebar.number_input("Jumlah dokter minimum", min_value=0, value=0, step=1)

quality_options = ["complete", "confirmed_zero", "partial", "unknown"]
selected_quality = st.sidebar.multiselect("Data status", options=quality_options, default=[])

min_completeness = st.sidebar.slider("Minimum schedule completeness", 0.0, 1.0, 0.0, 0.05)

filtered = df.copy()
if selected_kota:
    filtered = filtered[filtered["kota_kab"].isin(selected_kota)]
if selected_groups:
    filtered = filtered[filtered["Group"].isin(selected_groups)]
if min_derm > 0:
    filtered = filtered[filtered["Derm"].fillna(0) >= min_derm]
if selected_quality:
    filtered = filtered[filtered["Data quality"].isin(selected_quality)]
if min_completeness > 0:
    filtered = filtered[filtered["schedule_completeness"].fillna(0) >= min_completeness]

st.sidebar.caption(f"{len(filtered)} dari {len(df)} RS di universe '{universe}' cocok filter.")

tab_ranking, tab_heatmap, tab_map, tab_competitive, tab_quality = st.tabs(
    [
        "📊 Ranking",
        "🗓️ Heatmap Jadwal",
        "🗺️ Peta",
        "Competitive Pilot",
        "🔍 Data Quality",
    ]
)

# ---------------------------------------------------------------------
# 8.2 Ranking table
# ---------------------------------------------------------------------

with tab_ranking:
    st.subheader("Ranking Opportunity")
    st.caption(
        "Opportunity score HANYA menilai ruang praktik internal RS (jumlah dokter, "
        "doctor-hours, gap prime-time/weekend) — bukan indikasi permintaan pasar. "
        "RS dengan Data status 'unknown'/'partial' tidak memiliki skor yang bisa "
        "diandalkan (lihat kolom Data quality dan tab Data Quality)."
    )
    with st.expander("ℹ️ Kenapa ada RS dengan data lengkap tapi Opportunity kosong?"):
        st.markdown(
            """
Kolom **Derm**, **Sessions/wk**, **Derm hrs/wk**, dan **Gap jam ramai** dihitung
dari jadwal yang BERHASIL dibaca dengan yakin (confidence tinggi/medium) — jadi
tetap terisi angka meskipun sebagian jadwal RS itu tidak jelas/ambigu dan tidak
ikut dihitung.

Tapi Opportunity baru dihitung kalau minimal **70% dari seluruh baris jadwal RS
itu** berhasil dibaca dengan yakin. Kalau di bawah itu, angka yang sudah dihitung
(Derm, Sessions/wk, dst) dianggap TIDAK cukup mewakili keseluruhan jadwal RS,
jadi Opportunity sengaja dikosongkan — bukan dianggap 0, tapi "belum bisa
disimpulkan" (lihat kolom **Kenapa Opportunity kosong?** untuk alasan per RS).
Ini supaya rankingnya tidak salah menyimpulkan dari data yang sebagian besar
hilang/tidak terbaca.
            """
        )

    display_cols = [
        "Hospital",
        "Group",
        "Derm",
        "Sessions/wk",
        "Derm hrs/wk",
        "Gap jam ramai",
        "Sat/weekend gap",
        "Opportunity",
        "Data quality",
        "Kenapa Opportunity kosong?",
    ]
    table_df = filtered.copy()
    # Kolom "Kenapa Opportunity kosong?" hanya terisi untuk baris yang
    # memang tidak dapat skor (score_status != ok) — user feedback
    # 2026-08-09: kolom lain (Derm, Sessions/wk, dst) tetap terisi angka
    # untuk RS ini (dihitung dari jadwal yang BERHASIL di-parse), tapi
    # itu dianggap tidak cukup mewakili keseluruhan jadwal RS untuk
    # dijadikan skor -- tanpa penjelasan eksplisit ini terlihat seperti
    # bug ("datanya nyaris lengkap, kok Opportunity-nya kosong?").
    table_df["Kenapa Opportunity kosong?"] = table_df.apply(
        lambda r: r["score_status_reason"] if r["score_status"] != "ok" else "",
        axis=1,
    )
    table_df = table_df[display_cols].sort_values(
        by="Opportunity", ascending=False, na_position="last"
    )

    # Pertahankan dtype NUMERIC di data yang dikirim ke Streamlit supaya
    # sorting interaktif benar secara numerik (1, 2, ..., 10), bukan
    # leksikografis (1, 10, 2). Teks "Tidak ada data" hanya formatter
    # visual via Styler; nilai dasarnya tetap NaN. Ini sekaligus menghindari
    # ArrowInvalid karena tidak ada campuran float+string dalam satu kolom.
    _decimals_by_col = {
        "Derm": 0, "Sessions/wk": 0, "Derm hrs/wk": 1,
        "Gap jam ramai": 4, "Sat/weekend gap": 4, "Opportunity": 4,
    }
    display_df = table_df.style.format(
        {col: f"{{:.{decimals}f}}" for col, decimals in _decimals_by_col.items()},
        na_rep="Tidak ada data",
    )

    st.dataframe(display_df, use_container_width=True, height=500, hide_index=True)

    csv_bytes = table_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export CSV",
        data=csv_bytes,
        file_name=f"opportunity_ranking_{universe}_{dt.date.today().isoformat()}.csv",
        mime="text/csv",
    )

    n_insufficient = (filtered["score_status"] != "ok").sum()
    if n_insufficient > 0:
        st.caption(
            f"⚠️ {n_insufficient} RS di tabel ini TIDAK memiliki opportunity_score "
            "(score_status != ok) — muncul di bagian bawah tabel dengan Opportunity kosong, "
            "bukan dihilangkan (spec §3.5 'unknown != zero')."
        )

# ---------------------------------------------------------------------
# 8.3 Heatmap jadwal
# ---------------------------------------------------------------------

with tab_heatmap:
    st.subheader("Heatmap Jadwal 7 Hari × Slot 30 Menit (07:00–21:00)")
    hospital_options = filtered.sort_values("Hospital")["Hospital"].tolist()
    if not hospital_options:
        st.info("Tidak ada RS yang cocok filter saat ini.")
    else:
        selected_hospital_name = st.selectbox("Pilih RS", options=hospital_options)
        selected_row = filtered[filtered["Hospital"] == selected_hospital_name].iloc[0]
        hospital_id = int(selected_row["hospital_id"])

        engine = _get_engine()
        with Session(engine) as session:
            all_slots = session.execute(
                select(ScheduleSlot).where(ScheduleSlot.hospital_id == hospital_id)
            ).scalars().all()

        if not all_slots:
            st.info(
                "Tidak ada data jadwal untuk RS ini. Ini bisa berarti: (a) sumber/snapshot "
                "tidak menampilkan jadwal untuk dokter atau cabang tersebut, atau (b) RS "
                "ini belum pernah discrape sama sekali. Lihat tab Data Quality untuk "
                "detail per RS."
            )
        else:
            usable = usable_slots_for_hospital(all_slots)
            n_low_confidence = sum(1 for s in all_slots if s.parse_confidence == ParseConfidence.LOW)
            cells = build_matrix_cells(usable)

            # Tampilan per-JAM (gabung 2 slot 30 menit jadi 1 kolom) supaya
            # tabel tidak perlu di-scroll horizontal — data mentah di balik
            # layar tetap per 30 menit (dipakai untuk metrik Fase 6 seperti
            # prime_gap_ratio/longest_prime_gap_minutes yang butuh presisi
            # itu); ini murni pengelompokan untuk tampilan. Nilai tiap jam
            # diambil dari jumlah dokter TERBANYAK di antara 2 slot 30-menit
            # penyusunnya (bukan dijumlah), supaya angka tetap berarti
            # "berapa dokter praktik saat itu", bukan hasil penjumlahan.
            # 0=kosong, 1=1 dokter, 2=2+ dokter. Slot yang dikecualikan
            # karena low confidence TIDAK bisa dibedakan per-sel dari
            # "memang kosong" di grid ini (datanya tidak menyimpan info
            # sedetail itu per sel) — jumlah slot low-confidence
            # ditampilkan terpisah sebagai peringatan di bawah tabel.
            N_HOURS = N_SLOTS_PER_DAY // 2  # 07:00-21:00 dalam 2 slot 30 menit per jam = 14 jam
            grid = []
            for day in range(N_DAYS):
                row_vals = []
                for hour_idx in range(N_HOURS):
                    slot_a = len(cells.get((day, hour_idx * 2), set()))
                    slot_b = len(cells.get((day, hour_idx * 2 + 1), set()))
                    n_doctors = max(slot_a, slot_b)
                    row_vals.append(min(n_doctors, 2))  # cap display at "2+"
                grid.append(row_vals)

            time_labels = [
                f"{(DAY_START_MINUTES + i * 60) // 60:02d}:00"
                for i in range(N_HOURS)
            ]
            heatmap_df = pd.DataFrame(grid, index=_DAY_NAMES_ID, columns=time_labels)

            st.caption(
                "0 = kosong · 1 = 1 dokter · 2 = 2+ dokter. Kolom per jam (07:00-21:00), "
                "diambil dari jumlah dokter terbanyak dalam jam tersebut. Ditampilkan hanya "
                "jadwal yang berhasil di-parse dengan confidence tinggi/medium."
            )
            # Manual 3-color scale (0/1/2+) rather than
            # Styler.background_gradient — that needs matplotlib, an
            # extra dependency this project doesn't otherwise require
            # for a 3-value discrete scale.
            _HEATMAP_COLORS = {0: "#f5f5f5", 1: "#a1d99b", 2: "#238b45"}

            def _color_cell(value: int) -> str:
                return f"background-color: {_HEATMAP_COLORS.get(value, '#f5f5f5')}"

            st.dataframe(
                heatmap_df.style.map(_color_cell),
                use_container_width=True,
            )

            if n_low_confidence > 0:
                st.warning(
                    f"⚠️ {n_low_confidence} dari {len(all_slots)} baris jadwal mentah untuk RS ini "
                    "berstatus LOW confidence (teks jadwal ambigu/tidak terparse) dan TIDAK "
                    "dimasukkan ke heatmap di atas — bukan berarti slot tersebut kosong, "
                    "melainkan tidak diketahui (spec §3.5)."
                )

            st.metric("Schedule completeness", f"{(selected_row['schedule_completeness'] or 0) * 100:.0f}%")

# ---------------------------------------------------------------------
# 8.4 Map
# ---------------------------------------------------------------------

with tab_map:
    st.subheader("Peta RS")
    map_metric = st.selectbox(
        "Metrik warna marker",
        options=list(MAP_METRICS),
        format_func=lambda m: MAP_METRICS[m].label,
    )
    metric_spec = MAP_METRICS[map_metric]
    st.caption(
        "Warna marker cuma mencerminkan kondisi internal RS itu sendiri (jumlah "
        "dokter, jam praktik, jam kosong) — bukan seberapa ramai daerahnya atau "
        "seberapa besar kemungkinan pasien datang ke sana. "
        "🟢 Hijau = kelihatan ada ruang kosong buat praktik · 🟠 Oranye = sedang · "
        "🔴 Merah = kelihatan sudah padat dokter lain. "
        "RS dengan nol dokter yang SUDAH DIPASTIKAN (bukan sekadar belum kecatat) "
        "selalu 🟢, karena itu kekosongan yang paling meyakinkan. "
        "⚪ Abu-abu = datanya belum ada/gagal diambil, jadi belum bisa disimpulkan apa-apa."
    )

    with st.expander("ℹ️ Penjelasan tiap pilihan metrik — nilainya dari mana, dihitung gimana, kenapa penting"):
        st.markdown(
            """
**Skor opportunity** — angka gabungan (0 sampai 1) yang meranking RS berdasarkan
seberapa besar indikasi "ruang praktik yang belum terisi". Dihitung dari 4 hal,
masing-masing dibandingkan RELATIF terhadap RS lain (bukan angka mutlak):

- Jumlah dokter yang langka dibanding RS lain (bobot 20%)
- Jam praktik total per minggu yang sedikit dibanding RS lain (bobot 30%) — ini
  yang paling berat, karena 2 dokter yang jarang praktik ≠ 2 dokter yang sering
  praktik
- Jam "ramai" (lihat definisi di bawah) yang masih kosong dari dokter kulit
  (bobot 35%) — paling besar bobotnya, karena ini yang paling langsung
  menunjukkan ada slot yang bisa diisi
- Jam akhir pekan yang masih kosong (bobot 15%)

Kenapa penting: ini satu-satunya angka yang menggabungkan semua sinyal jadi satu
ranking. Tapi ingat — ini murni dari sisi PASOKAN (berapa dokter, jam berapa
saja), BUKAN dari sisi PERMINTAAN (berapa banyak pasien di daerah situ). RS
dengan skor tinggi belum tentu ramai pasiennya — cuma menunjukkan "kelihatannya
ada slot kosong", bukan "pasti banyak yang butuh".

**Jumlah dokter** — banyaknya dokter kulit berbeda yang terdaftar praktik di RS
itu, dihitung dari data yang berhasil dikumpulkan (bukan sensus resmi RS).
Kenapa penting, tapi juga kenapa HARUS hati-hati: jumlah dokter sendirian tidak
cukup buat menyimpulkan "kosong" atau "penuh" — RS dengan 5 dokter yang jarang
praktik bisa jadi punya lebih banyak slot kosong dibanding RS dengan 2 dokter
yang praktik hampir tiap hari. Makanya di peta ini pakai skala tetap yang sudah
disesuaikan sesuai pengalaman lapangan (1-3 dokter = hijau, 4-5 = oranye, 6+ =
merah), bukan dihitung ulang otomatis dari data.

**Jam dokter/minggu** — total jam praktik SEMUA dokter kulit di RS itu digabung
dalam satu minggu (bukan jam kosong — ini jam yang SUDAH terisi dokter).
Contoh: 3 dokter yang masing-masing praktik 10 jam/minggu = 30 jam dokter/minggu.
Kenapa penting: ini proxy paling langsung untuk "kapasitas yang sudah terpakai" —
makin kecil angkanya, makin besar kemungkinan RS itu punya ruang untuk dokter
baru. Ini kenapa lebih diandalkan dibanding sekadar jumlah dokter (lihat di atas).

**Gap jam ramai** — persentase slot 30 menit di jam "ramai" yang masih KOSONG
dari dokter kulit. "Jam ramai" di sistem ini didefinisikan sebagai sore hari
kerja (Senin–Jumat 17:00–21:00) + Sabtu pagi (08:00–14:00) — bisa diubah di
config/prime_time.yaml, ini asumsi operasional, bukan fakta epidemiologis pasti.
**Penting disadari:** jam siang hari kerja (mis. 12:00–14:00) TIDAK termasuk
definisi "jam ramai" ini — jadi walaupun heatmap jadwal RS tertentu menunjukkan
banyak slot kosong di siang hari, itu tidak ikut mempengaruhi angka ini sama
sekali. Kalau kamu curiga siang hari juga jam ramai pasien datang, definisi ini
perlu didiskusikan ulang, bukan salah hitung.
            """
        )

    map_df = filtered.dropna(subset=["lat", "lon"]).copy()
    map_df["metric_value"] = map_df[metric_spec.dataframe_column]

    if map_df.empty:
        st.info("Tidak ada RS dengan koordinat yang cocok filter saat ini.")
    else:
        center_lat, center_lon = map_df["lat"].mean(), map_df["lon"].mean()
        fmap = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

        scale_values = [
            float(r["metric_value"])
            for _, r in map_df.iterrows()
            if metric_value_participates_in_scale(
                r["metric_value"],
                data_quality=r["Data quality"],
                spec=metric_spec,
            )
        ]
        category_boundaries = calculate_category_boundaries(scale_values)
        st.markdown(format_metric_legend(metric_spec, category_boundaries))

        for _, r in map_df.iterrows():
            value = r["metric_value"]
            category = classify_marker(
                value,
                data_quality=r["Data quality"],
                spec=metric_spec,
                boundaries=category_boundaries,
            )

            _no_data = "Tidak ada data"
            popup_html = (
                f"<b>{r['Hospital']}</b><br>"
                f"Group: {r['Group']}<br>"
                f"Metrik aktif: {metric_spec.label} = "
                f"{value if pd.notna(value) else _no_data}<br>"
                f"Kategori marker: {category.label}<br>"
                f"Derm: {r['Derm'] if pd.notna(r['Derm']) else _no_data}<br>"
                f"Derm hrs/wk: {r['Derm hrs/wk'] if pd.notna(r['Derm hrs/wk']) else _no_data}<br>"
                f"Gap jam ramai: {r['Gap jam ramai'] if pd.notna(r['Gap jam ramai']) else _no_data}<br>"
                f"Opportunity: {r['Opportunity'] if pd.notna(r['Opportunity']) else _no_data}<br>"
                f"Data quality: {r['Data quality']}"
            )
            folium.CircleMarker(
                location=[r["lat"], r["lon"]],
                radius=7,
                color=category.color,
                fill=True,
                fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(fmap)

        st_folium(
            fmap,
            use_container_width=True,
            height=550,
            returned_objects=[],
            key="opportunity_map",
        )

# ---------------------------------------------------------------------
# V1.5 hospital-only competitive pilot
# ---------------------------------------------------------------------

with tab_competitive:
    st.subheader("Competitive context — pilot rumah sakit")
    st.caption(
        "Supply dermatologi RS lain dalam radius garis lurus dari RS anchor. "
        "Klinik kecantikan, klinik umum, dental, dan fasilitas non-RS tidak "
        "dimasukkan. Angka supply adalah batas bawah selama masih ada RS unknown."
    )

    competitive_config = get_competitive_pilot_config()
    engine = _get_engine()
    with Session(engine) as session:
        competitive_results = compute_competitive_pilot(session, competitive_config)

    cluster_key = st.selectbox(
        "Cluster pilot",
        options=list(competitive_config.clusters),
        format_func=lambda key: competitive_config.clusters[key].label,
    )
    radius_km = st.selectbox(
        "Radius pilot (km)",
        options=competitive_config.radii_km,
        index=competitive_config.radii_km.index(competitive_config.default_radius_km),
    )
    cluster_metrics = competitive_results[cluster_key]
    selected_metrics = next(
        row for row in cluster_metrics if row.radius_km == radius_km
    )

    anchor_derm = (
        str(selected_metrics.anchor_dermatologists)
        if selected_metrics.anchor_dermatologists is not None
        else "Unknown"
    )
    anchor_hours = (
        f"{selected_metrics.anchor_doctor_hours_week:g} jam/minggu"
        if selected_metrics.anchor_doctor_hours_week is not None
        else "Unknown"
    )
    st.info(
        f"Anchor: **{selected_metrics.anchor_hospital_name}** — "
        f"{anchor_derm} dermatolog, {anchor_hours}. "
        "Supply anchor tidak ikut angka kompetitor di bawah."
    )

    comparison_rows = [
        {
            "Radius": f"{row.radius_km:g} km",
            "RS registry": row.nearby_hospitals_count,
            "Status diketahui": row.nearby_known_status_count,
            "RS unknown": row.nearby_unknown_hospitals_count,
            "RS dengan dermatolog": row.nearby_derm_hospitals_count,
            "Dermatolog unik (known)": row.nearby_dermatologists_unique,
            "Doctor-hours known": row.nearby_derm_doctor_hours_week,
            "Coverage status": (
                f"{row.known_status_coverage_ratio:.0%}"
                if row.known_status_coverage_ratio is not None
                else "Tidak ada RS"
            ),
            "Confidence": row.data_quality.value,
        }
        for row in cluster_metrics
    ]
    st.dataframe(pd.DataFrame(comparison_rows), hide_index=True, use_container_width=True)

    metric_columns = st.columns(6)
    metric_columns[0].metric("RS sekitar", selected_metrics.nearby_hospitals_count)
    metric_columns[1].metric("Status diketahui", selected_metrics.nearby_known_status_count)
    metric_columns[2].metric("RS unknown", selected_metrics.nearby_unknown_hospitals_count)
    metric_columns[3].metric(
        "RS dengan dermatolog", selected_metrics.nearby_derm_hospitals_count
    )
    metric_columns[4].metric(
        "Dermatolog unik (known)", selected_metrics.nearby_dermatologists_unique
    )
    metric_columns[5].metric(
        "Doctor-hours sekitar", f"{selected_metrics.nearby_derm_doctor_hours_week:g}"
    )

    if selected_metrics.nearby_unknown_hospitals_count:
        st.warning(
            f"{selected_metrics.nearby_unknown_hospitals_count} RS dalam radius ini "
            "masih unknown. Jangan menafsirkan angka dermatolog/doctor-hours sebagai "
            "total pasar; ini baru supply yang berhasil diketahui."
        )

    competitive_map = folium.Map(
        location=[selected_metrics.anchor_lat, selected_metrics.anchor_lon],
        zoom_start=12,
        tiles="OpenStreetMap",
    )
    folium.Circle(
        location=[selected_metrics.anchor_lat, selected_metrics.anchor_lon],
        radius=selected_metrics.radius_km * 1000,
        color="#2563eb",
        weight=2,
        fill=False,
        tooltip=f"Radius {selected_metrics.radius_km:g} km",
    ).add_to(competitive_map)
    folium.Marker(
        location=[selected_metrics.anchor_lat, selected_metrics.anchor_lon],
        tooltip=f"Anchor: {selected_metrics.anchor_hospital_name}",
        popup=selected_metrics.anchor_hospital_name,
        icon=folium.Icon(color="blue", icon="plus-sign"),
    ).add_to(competitive_map)

    status_colors = {
        DermatologistCountStatus.HAS_DOCTORS.value: "purple",
        DermatologistCountStatus.CONFIRMED_ZERO.value: "gray",
        DermatologistCountStatus.UNKNOWN.value: "gray",
    }
    for hospital in selected_metrics.hospitals:
        derm_value = (
            hospital.n_dermatologists
            if hospital.n_dermatologists is not None
            else "Tidak ada data"
        )
        hours_value = (
            f"{hospital.doctor_hours_week:g}"
            if hospital.doctor_hours_week is not None
            else "Tidak ada data"
        )
        popup = (
            f"<b>{hospital.hospital_name}</b><br>"
            f"Jarak: {hospital.distance_km:g} km<br>"
            f"Status: {hospital.dermatologist_status}<br>"
            f"Dermatolog: {derm_value}<br>"
            f"Doctor-hours/minggu: {hours_value}"
        )
        folium.CircleMarker(
            location=[hospital.lat, hospital.lon],
            radius=6,
            color=status_colors.get(hospital.dermatologist_status, "gray"),
            fill=True,
            fill_opacity=0.8,
            tooltip=hospital.hospital_name,
            popup=folium.Popup(popup, max_width=300),
        ).add_to(competitive_map)

    st_folium(
        competitive_map,
        use_container_width=True,
        height=520,
        returned_objects=[],
        key=f"competitive_{cluster_key}_{radius_km:g}",
    )

    detail_rows = [
        {
            "Hospital": hospital.hospital_name,
            "Jarak (km)": hospital.distance_km,
            "Group": hospital.group or "(belum terpetakan)",
            "Status dermatolog": hospital.dermatologist_status,
            "Dermatolog": hospital.n_dermatologists,
            "Doctor-hours/minggu": hospital.doctor_hours_week,
            "Schedule completeness": hospital.schedule_completeness,
        }
        for hospital in selected_metrics.hospitals
    ]
    st.dataframe(pd.DataFrame(detail_rows), hide_index=True, use_container_width=True)
    st.caption(
        "Jumlah RS adalah baris registry rumah sakit dalam radius, bukan jumlah "
        "brand/grup unik. Duplikat registry yang belum terverifikasi mungkin masih "
        "ada dan sengaja tidak digabung otomatis."
    )

# ---------------------------------------------------------------------
# 8.5 Data Quality tab
# ---------------------------------------------------------------------

with tab_quality:
    st.subheader("Data Quality")

    engine = _get_engine()
    with Session(engine) as session:
        total_master = session.query(Hospital).count()
        total_preferred = session.query(Hospital).filter(Hospital.is_preferred_group.is_(True)).count()

        tier_counts = {}
        for tier in SourceTier:
            tier_counts[tier.value] = session.query(Doctor).filter(Doctor.source_tier == tier).count()

        n_manual_hospitals = session.query(Hospital).filter(Hospital.data_status == DataStatus.MANUAL).count()
        n_scrape_failed = session.query(Hospital).filter(Hospital.data_status == DataStatus.SCRAPE_FAILED).count()

        no_data_status_counts = {}
        for status in DermatologistCountStatus:
            no_data_status_counts[status.value] = (
                session.query(HospitalPracticeMetrics)
                .filter(HospitalPracticeMetrics.dermatologist_count_status == status)
                .count()
            )

        parse_conf_counts = {}
        for conf in ParseConfidence:
            parse_conf_counts[conf.value] = (
                session.query(ScheduleSlot).filter(ScheduleSlot.parse_confidence == conf).count()
            )

        latest_scraped = session.query(Doctor.scraped_at).order_by(Doctor.scraped_at.desc()).first()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total RS master registry", total_master)
    col2.metric("Total preferred-private", total_preferred)
    col3.metric(
        "Source freshness (dokter terbaru)",
        latest_scraped[0].strftime("%Y-%m-%d") if latest_scraped and latest_scraped[0] else "N/A",
    )

    st.markdown("**Tier keberhasilan (per baris Doctor)**")
    tier_col1, tier_col2, tier_col3 = st.columns(3)
    tier_col1.metric("Tier 1 (situs resmi RS)", tier_counts.get("tier_1_official", 0))
    tier_col2.metric("Tier 2 (aggregator, fallback)", tier_counts.get("tier_2_aggregator", 0))
    tier_col3.metric("Tier 3 (manual override)", tier_counts.get("tier_3_manual", 0))

    st.markdown("**Status hospital (data_status)**")
    dq1, dq2 = st.columns(2)
    dq1.metric("Manual records (mis. Eka Hospital)", n_manual_hospitals)
    dq2.metric("Scrape failed", n_scrape_failed)

    st.markdown("**Status jumlah dermatologist per RS (spec §7.6 — 3 makna berbeda)**")
    nd1, nd2, nd3, nd4 = st.columns(4)
    nd1.metric("Has doctors", no_data_status_counts.get("has_doctors", 0))
    nd2.metric("Confirmed zero", no_data_status_counts.get("confirmed_zero", 0))
    nd3.metric("No derm service", no_data_status_counts.get("no_derm_service", 0))
    nd4.metric("Unknown (belum discrape)", no_data_status_counts.get("unknown", 0))
    st.caption(
        "Ketiganya SENGAJA tidak disamakan: 'confirmed zero' = layanan ada tapi benar-benar "
        "tidak ada dokter terdaftar; 'unknown' = RS ini belum pernah discrape sama sekali."
    )

    st.markdown("**Schedule parse confidence (per baris ScheduleSlot)**")
    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("High", parse_conf_counts.get("high", 0))
    pc2.metric("Medium", parse_conf_counts.get("medium", 0))
    pc3.metric("Low (dikecualikan dari metrik)", parse_conf_counts.get("low", 0))

    st.markdown("**Scrape failures**")
    st.caption(
        "Kegagalan scrape per-cabang yang ter-log secara eksplisit (bukan dari tabel "
        "scrape_logs yang belum diisi produksi di V1): lihat docstring "
        "src/scrapers/pipeline.py untuk daftar gap registry yang ditemukan manual, dan "
        "src/scrapers/brawijaya.py (satu cabang, Taman Mini rsid=66, gagal HTTP 500 "
        "dari backend RS sendiri — di-skip otomatis tanpa membatalkan seluruh scrape)."
    )

    st.markdown("**Unresolved doctor identity matches**")
    st.caption(
        "Tidak berlaku di V1: setiap baris Doctor sudah hospital-scoped per cabang "
        "(satu dokter di 2 RS = 2 baris Doctor yang berbagi normalized_person_key yang "
        "sama, bukan digabung jadi satu baris) — tidak ada langkah merge lintas-RS yang "
        "bisa 'unresolved'. Lihat kolom 'doctors_with_external_overlap' / "
        "'mean_external_hospital_count' di ranking table sebagai context metric terkait."
    )
