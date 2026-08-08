"""Shared map-marker category rules for the dashboard.

Marker colours always communicate practice opportunity, not the raw
numeric direction of a metric. Keeping these rules outside ``src.app``
makes direction and boundary logic testable without executing Streamlit.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class MapMetricSpec:
    key: str
    label: str
    dataframe_column: str
    higher_is_more_opportunity: bool
    allow_partial: bool
    decimals: int


@dataclass(frozen=True)
class MarkerCategory:
    color: str
    label: str


@dataclass(frozen=True)
class CategoryBoundaries:
    minimum: float
    lower: float
    upper: float
    maximum: float
    has_values: bool = True


MAP_METRICS: dict[str, MapMetricSpec] = {
    "Opportunity": MapMetricSpec(
        "Opportunity", "Skor opportunity", "Opportunity", True, False, 2
    ),
    "Derm": MapMetricSpec(
        "Derm", "Jumlah dokter", "Derm", False, True, 0
    ),
    "Derm hrs/wk": MapMetricSpec(
        "Derm hrs/wk", "Jam dokter/minggu", "Derm hrs/wk", False, False, 1
    ),
    "prime_gap_ratio_display": MapMetricSpec(
        "prime_gap_ratio_display", "Gap jam ramai", "Gap jam ramai", True, False, 2
    ),
}


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def metric_value_participates_in_scale(
    value: object,
    *,
    data_quality: str,
    spec: MapMetricSpec,
) -> bool:
    """Whether a row should influence red/orange/green boundaries."""
    if _is_missing(value):
        return False
    if data_quality in {"unknown", "confirmed_zero"}:
        return False
    if data_quality == "partial" and not spec.allow_partial:
        return False
    return True


def calculate_category_boundaries(values: list[float]) -> CategoryBoundaries:
    """Return inclusive tercile boundaries for the active, colourable rows."""
    if not values:
        return CategoryBoundaries(0.0, 0.0, 0.0, 0.0, has_values=False)
    numeric = sorted(float(value) for value in values)
    if len(numeric) == 1:
        return CategoryBoundaries(numeric[0], numeric[0], numeric[0], numeric[0])
    lower, upper = statistics.quantiles(numeric, n=3, method="inclusive")
    return CategoryBoundaries(numeric[0], lower, upper, numeric[-1])


def classify_marker(
    value: object,
    *,
    data_quality: str,
    spec: MapMetricSpec,
    boundaries: CategoryBoundaries,
) -> MarkerCategory:
    """Classify a value into relative thirds of the active map.

    Raw-value order is reversed for doctor count and doctor-hours because
    lower internal supply means larger practice opportunity.
    """
    if data_quality == "confirmed_zero":
        # Complete official group coverage establishes that this branch
        # really has no listed dermatologist. That is maximum internal
        # scarcity/opportunity, even where a schedule-derived metric is
        # naturally absent because no doctor exists to have a schedule.
        return MarkerCategory("green", "peluang besar (confirmed zero dokter)")
    if _is_missing(value):
        return MarkerCategory("gray", "data tidak tersedia")
    if data_quality == "unknown":
        return MarkerCategory("gray", "data tidak tersedia")
    if data_quality == "partial" and not spec.allow_partial:
        return MarkerCategory("gray", "data jadwal belum cukup")
    if boundaries.maximum <= boundaries.minimum:
        return MarkerCategory("orange", "peluang sedang (rentang tunggal)")

    numeric_value = float(value)
    if numeric_value <= boundaries.lower:
        raw_bucket = "low"
    elif numeric_value <= boundaries.upper:
        raw_bucket = "middle"
    else:
        raw_bucket = "high"

    if raw_bucket == "middle":
        return MarkerCategory("orange", "peluang sedang")
    more_opportunity = (
        raw_bucket == "high" if spec.higher_is_more_opportunity else raw_bucket == "low"
    )
    return (
        MarkerCategory("green", "peluang besar")
        if more_opportunity
        else MarkerCategory("red", "peluang kecil")
    )


def format_metric_legend(spec: MapMetricSpec, boundaries: CategoryBoundaries) -> str:
    """Legend whose displayed thresholds exactly match ``classify_marker``."""
    direction = "nilai lebih tinggi" if spec.higher_is_more_opportunity else "nilai lebih rendah"
    if not boundaries.has_values:
        return (
            f"**{spec.label}:** tidak ada nilai yang dapat membentuk tercile; "
            "unknown tetap abu-abu dan confirmed zero tetap hijau."
        )
    if boundaries.maximum <= boundaries.minimum:
        value_text = _format_value(boundaries.minimum, spec.decimals)
        return (
            f"**{spec.label}:** {direction} = peluang lebih besar. Semua nilai berwarna "
            f"sama ({value_text}), sehingga kategorinya oranye."
        )

    low = f"≤ {_format_value(boundaries.lower, spec.decimals)}"
    middle = (
        f"> {_format_value(boundaries.lower, spec.decimals)} s.d. "
        f"{_format_value(boundaries.upper, spec.decimals)}"
    )
    high = f"> {_format_value(boundaries.upper, spec.decimals)}"
    if spec.higher_is_more_opportunity:
        buckets = f"🔴 {low} · 🟠 {middle} · 🟢 {high}"
    else:
        buckets = f"🟢 {low} · 🟠 {middle} · 🔴 {high}"
    return f"**{spec.label}:** {direction} = peluang lebih besar. Tercile peta aktif: {buckets}."


def _format_value(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"
