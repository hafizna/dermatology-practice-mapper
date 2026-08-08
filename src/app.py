"""Streamlit dashboard — placeholder for Fase 8.

Full dashboard (universe filter, ranking table, schedule heatmap, Folium
map, data-quality tab) is built in Fase 8 per PROJECT_SPEC.md §9. Until
core opportunity scores exist (Fase 7), this page only confirms the app
boots and shows what is/isn't available yet.
"""

from __future__ import annotations

import streamlit as st

from src.db import get_engine
from src.models import Hospital
from sqlalchemy.orm import Session

st.set_page_config(page_title="Derm Practice Opportunity Mapper", layout="wide")

st.title("Dermatology Practice Opportunity Mapper — Jabodetabek")
st.caption("V1 (Practice Vacancy Mapper) sedang dibangun fase-demi-fase. Lihat PROJECT_SPEC.md.")

try:
    engine = get_engine()
    with Session(engine) as session:
        hospital_count = session.query(Hospital).count()
    st.metric("Hospitals in registry", hospital_count)
    if hospital_count == 0:
        st.info(
            "Registry masih kosong. Jalankan `python -m src.cli fetch-registry` "
            "(Fase 1) untuk mengisi master registry."
        )
except Exception as exc:  # pragma: no cover - dev convenience only
    st.warning(
        "Database belum diinisialisasi. Jalankan `python -m src.cli init-db` terlebih dahulu.\n\n"
        f"Detail: {exc}"
    )

st.divider()
st.subheader("Roadmap fase (lihat PROJECT_SPEC.md §9)")
st.markdown(
    """
    - [ ] Fase 1 — Hospital Master Registry
    - [ ] Fase 2 — Scraper Framework + Eka Hospital pilot
    - [ ] Fase 3 — Scale adapters ke target groups
    - [ ] Fase 4 — Parsing & identity resolution
    - [ ] Fase 5 — Geocoding
    - [ ] Fase 6 — Coverage matrix & supply metrics
    - [ ] Fase 7 — Core Opportunity Score
    - [ ] Fase 8 — Dashboard MVP (halaman ini akan digantikan versi penuh)
    """
)
