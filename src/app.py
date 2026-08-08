"""Fase 8: Streamlit Dashboard MVP — spec §10 Fase 8.

V1 scope only: universe filter, ranking table, schedule heatmap, map,
data-quality tab. Deliberately NO population/affluence/office-density
layer (that's V2's Market Attractiveness, out of scope here) and NO
competitive-context panel (V1.5, out of scope here) — spec explicitly
states "Dashboard V1 harus sudah berguna tanpa population/affluence
layer."

Data freshness: this page reads whatever is currently in
data/processed/derm_mapper.sqlite. It does not re-scrape or recompute
metrics itself — run `python -m src.cli fetch-registry`, `scrape --all`,
and `compute-core` (which chains Fase 6 + Fase 7) beforehand to refresh.
"""

from __future__ import annotations

import datetime as dt

import folium
import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit_folium import st_folium

from src.db import get_engine
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
                    # display_alias (config/manual_overrides.csv) is
                    # shown ALONGSIDE the raw OSM name, never replacing
                    # it — e.g. OSM's "Rumah Sakit Siloam" really is
                    # MRCCC Siloam Semanggi, but that alias is a human
                    # annotation, not the source-of-record name.
                    "Hospital": (
                        f"{hospital.name} (a.k.a. {hospital.display_alias})"
                        if hospital.display_alias
                        else hospital.name
                    ),
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

tab_ranking, tab_heatmap, tab_map, tab_quality = st.tabs(
    ["📊 Ranking", "🗓️ Heatmap Jadwal", "🗺️ Peta", "🔍 Data Quality"]
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
    ]
    table_df = filtered[display_cols].sort_values(
        by="Opportunity", ascending=False, na_position="last"
    )
    st.dataframe(table_df, use_container_width=True, height=500, hide_index=True)

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
                "Tidak ada data jadwal untuk RS ini. Ini bisa berarti: (a) sumber ini "
                "memang tidak pernah menyediakan data jadwal (mis. Eka Hospital — "
                "snapshot manual, listing dokter saja), atau (b) RS ini belum pernah "
                "discrape sama sekali. Lihat tab Data Quality untuk detail per RS."
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
        "Ukuran/warna marker TIDAK berdasarkan populasi atau demand proxy apa pun — "
        "murni metrik supply internal RS (spec §10 Fase 8.4, sebelum V2). "
        "🟢 Hijau = peluang besar · 🟠 Oranye = sedang · 🔴 Merah = peluang kecil · "
        "Confirmed zero dokter selalu 🟢 karena merupakan scarcity yang terverifikasi. "
        "⚪ Abu-abu = data unknown/gagal diambil; data partial hanya dapat diwarnai "
        "untuk jumlah dokter, bukan metrik berbasis jadwal."
    )

    map_df = filtered.dropna(subset=["lat", "lon"]).copy()
    map_df["metric_value"] = map_df[metric_spec.dataframe_column]

    if map_df.empty:
        st.info("Tidak ada RS dengan koordinat yang cocok filter saat ini.")
    else:
        center_lat, center_lon = map_df["lat"].mean(), map_df["lon"].mean()
        fmap = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="cartodbpositron")

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

            popup_html = (
                f"<b>{r['Hospital']}</b><br>"
                f"Group: {r['Group']}<br>"
                f"Metrik aktif: {metric_spec.label} = "
                f"{value if pd.notna(value) else 'unknown'}<br>"
                f"Kategori marker: {category.label}<br>"
                f"Derm: {r['Derm'] if pd.notna(r['Derm']) else 'unknown'}<br>"
                f"Derm hrs/wk: {r['Derm hrs/wk'] if pd.notna(r['Derm hrs/wk']) else 'unknown'}<br>"
                f"Gap jam ramai: {r['Gap jam ramai'] if pd.notna(r['Gap jam ramai']) else 'unknown'}<br>"
                f"Opportunity: {r['Opportunity'] if pd.notna(r['Opportunity']) else 'insufficient_data'}<br>"
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

        st_folium(fmap, use_container_width=True, height=550, returned_objects=[])

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
