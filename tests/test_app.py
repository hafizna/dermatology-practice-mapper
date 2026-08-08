"""Fase 8: Streamlit dashboard smoke tests via streamlit.testing.v1.AppTest.

These run the REAL src/app.py script against the REAL configured database
(data/processed/derm_mapper.sqlite via src.db.get_engine()) — not an
in-memory fixture, since the app itself has no dependency-injection point
for the engine (spec doesn't require one, and it isn't worth adding
complexity for testability alone here). This means these tests are
skipped if that database doesn't exist/is empty, rather than failing —
they verify the dashboard SCRIPT is exception-free given whatever data is
currently loaded, not any particular data content.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.config import DATA_DIR, REPO_ROOT
from src.db import get_engine
from src.map_categories import MAP_METRICS
from src.models import Hospital
from sqlalchemy.orm import Session

_DB_PATH = DATA_DIR / "processed" / "derm_mapper.sqlite"
_APP_PATH = REPO_ROOT / "src" / "app.py"


def _db_has_hospitals() -> bool:
    if not _DB_PATH.exists():
        return False
    try:
        with Session(get_engine()) as session:
            return session.query(Hospital).count() > 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_has_hospitals(),
    reason="data/processed/derm_mapper.sqlite kosong/tidak ada — jalankan fetch-registry dahulu.",
)


def test_app_renders_default_tab_without_exception():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception


def test_app_all_hospitals_universe_renders_without_exception():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    universe_selectbox = next(sb for sb in at.selectbox if sb.label == "Universe")
    universe_selectbox.set_value("All Hospitals")
    at.run()
    assert not at.exception


def test_app_all_private_universe_renders_without_exception():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    universe_selectbox = next(sb for sb in at.selectbox if sb.label == "Universe")
    universe_selectbox.set_value("All Private")
    at.run()
    assert not at.exception


def test_app_map_metric_switch_renders_without_exception():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    map_selectbox = next(sb for sb in at.selectbox if sb.label == "Metrik warna marker")
    for metric_key in MAP_METRICS:
        map_selectbox.set_value(metric_key)
        at.run()
        assert not at.exception, f"map metric {metric_key!r} raised an exception"
        map_selectbox = next(sb for sb in at.selectbox if sb.label == "Metrik warna marker")


def test_app_map_legends_match_each_metric_direction():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    map_selectbox = next(sb for sb in at.selectbox if sb.label == "Metrik warna marker")

    expected_directions = {
        "Opportunity": "nilai lebih tinggi",
        "Derm": "nilai lebih rendah",
        "Derm hrs/wk": "nilai lebih rendah",
        "prime_gap_ratio_display": "nilai lebih tinggi",
    }
    for metric_key, direction in expected_directions.items():
        map_selectbox.set_value(metric_key)
        at.run()
        # "Derm" uses a fixed scale ("Skala tetap") rather than
        # tercile ("Tercile peta aktif") — see MapMetricSpec.fixed_boundaries
        # docstring in src/map_categories.py.
        legends = [
            str(md.value)
            for md in at.markdown
            if "Tercile peta aktif" in str(md.value) or "Skala tetap" in str(md.value)
        ]
        assert len(legends) == 1
        assert MAP_METRICS[metric_key].label in legends[0]
        assert direction in legends[0]
        map_selectbox = next(sb for sb in at.selectbox if sb.label == "Metrik warna marker")


def test_app_heatmap_hospital_selection_renders_without_exception():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    hospital_selectbox = next(sb for sb in at.selectbox if sb.label == "Pilih RS")
    if not hospital_selectbox.options:
        pytest.skip("Tidak ada RS yang cocok filter default.")
    # Try a handful of hospitals (not all — keep the test fast), covering
    # both the "has real schedule data" and "no schedule data at all"
    # branches if present.
    for option in hospital_selectbox.options[:5]:
        hospital_selectbox.set_value(option)
        at.run()
        assert not at.exception, f"hospital {option!r} raised an exception in heatmap tab"


def test_app_ranking_table_has_expected_columns():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    ranking_df = at.dataframe[0].value
    expected_cols = {
        "Hospital",
        "Group",
        "Derm",
        "Sessions/wk",
        "Derm hrs/wk",
        "Gap jam ramai",
        "Sat/weekend gap",
        "Opportunity",
        "Data quality",
    }
    assert expected_cols.issubset(set(ranking_df.columns))


def test_app_data_quality_tab_metrics_present():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    # Data Quality tab renders 3 (top) + 3 (tier) + 2 (data_status) + 4
    # (dermatologist_count_status) + 3 (parse confidence) = 15 st.metric
    # calls, plus 1 in the heatmap tab (schedule_completeness) = 16.
    assert len(at.metric) == 16


def test_app_no_matching_filter_shows_empty_state_not_crash():
    at = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    at.run()
    min_derm_input = at.number_input[0]
    min_derm_input.set_value(9999)  # no hospital has this many doctors
    at.run()
    assert not at.exception
