"""Map marker direction, gray-state, and legend regression tests."""

import math

import pytest

from src.map_categories import (
    MAP_METRICS,
    CategoryBoundaries,
    calculate_category_boundaries,
    classify_marker,
    format_metric_legend,
    metric_value_participates_in_scale,
)


@pytest.mark.parametrize("metric_key", ["Opportunity", "prime_gap_ratio_display"])
def test_higher_value_is_greener_for_opportunity_and_gap(metric_key):
    spec = MAP_METRICS[metric_key]
    bounds = CategoryBoundaries(0, 3, 6, 9)
    assert classify_marker(0.0, data_quality="complete", spec=spec, boundaries=bounds).color == "red"
    assert classify_marker(4.5, data_quality="complete", spec=spec, boundaries=bounds).color == "orange"
    assert classify_marker(9.0, data_quality="complete", spec=spec, boundaries=bounds).color == "green"


@pytest.mark.parametrize("metric_key", ["Derm", "Derm hrs/wk"])
def test_lower_value_is_greener_for_supply_metrics(metric_key):
    spec = MAP_METRICS[metric_key]
    bounds = CategoryBoundaries(0, 3, 6, 9)
    assert classify_marker(0.0, data_quality="complete", spec=spec, boundaries=bounds).color == "green"
    assert classify_marker(4.5, data_quality="complete", spec=spec, boundaries=bounds).color == "orange"
    assert classify_marker(9.0, data_quality="complete", spec=spec, boundaries=bounds).color == "red"


def test_confirmed_zero_is_green_and_excluded_from_tercile_scale_for_every_metric():
    for metric_key in MAP_METRICS:
        spec = MAP_METRICS[metric_key]
        bounds = CategoryBoundaries(0, 3, 6, 9)
        category = classify_marker(
            0.0, data_quality="confirmed_zero", spec=spec, boundaries=bounds
        )
        assert category.color == "green"
        assert not metric_value_participates_in_scale(
            0.0, data_quality="confirmed_zero", spec=spec
        )


def test_confirmed_zero_is_green_even_when_schedule_metric_is_naturally_missing():
    bounds = CategoryBoundaries(0, 3, 6, 9)
    for metric_key in ("Derm hrs/wk", "prime_gap_ratio_display"):
        spec = MAP_METRICS[metric_key]
        assert classify_marker(
            None, data_quality="confirmed_zero", spec=spec, boundaries=bounds
        ).color == "green"


@pytest.mark.parametrize("missing", [None, math.nan])
def test_missing_value_is_always_gray_and_outside_scale(missing):
    bounds = CategoryBoundaries(0, 1 / 3, 2 / 3, 1)
    for spec in MAP_METRICS.values():
        assert classify_marker(
            missing, data_quality="unknown", spec=spec, boundaries=bounds
        ).color == "gray"
        assert not metric_value_participates_in_scale(
            missing, data_quality="unknown", spec=spec
        )


def test_unknown_is_gray_and_excluded_even_if_a_stale_numeric_value_exists():
    bounds = CategoryBoundaries(0, 1 / 3, 2 / 3, 1)
    for spec in MAP_METRICS.values():
        assert classify_marker(
            0.5, data_quality="unknown", spec=spec, boundaries=bounds
        ).color == "gray"
        assert not metric_value_participates_in_scale(
            0.5, data_quality="unknown", spec=spec
        )


def test_partial_is_colored_only_for_known_doctor_count():
    bounds = CategoryBoundaries(0, 3, 6, 9)
    count_spec = MAP_METRICS["Derm"]
    assert classify_marker(
        2, data_quality="partial", spec=count_spec, boundaries=bounds
    ).color == "green"
    assert metric_value_participates_in_scale(
        2, data_quality="partial", spec=count_spec
    )

    for metric_key in ("Opportunity", "Derm hrs/wk", "prime_gap_ratio_display"):
        spec = MAP_METRICS[metric_key]
        assert classify_marker(
            0, data_quality="partial", spec=spec, boundaries=bounds
        ).color == "gray"
        assert not metric_value_participates_in_scale(
            0, data_quality="partial", spec=spec
        )


def test_legend_matches_metric_direction_and_exact_thirds():
    # "Derm hrs/wk" (not "Derm") used here since "Derm" now has a fixed
    # scale (see test_derm_uses_fixed_1_3_4_5_6plus_scale_not_tercile) —
    # this test is specifically about tercile-based legend formatting.
    bounds = CategoryBoundaries(0, 3, 6, 9)
    hours_legend = format_metric_legend(MAP_METRICS["Derm hrs/wk"], bounds)
    assert "nilai lebih rendah" in hours_legend
    assert "🟢 ≤ 3" in hours_legend
    assert "🔴 > 6" in hours_legend

    opportunity_bounds = CategoryBoundaries(0, 0.3, 0.6, 0.9)
    opportunity_legend = format_metric_legend(MAP_METRICS["Opportunity"], opportunity_bounds)
    assert "nilai lebih tinggi" in opportunity_legend
    assert "🔴 ≤ 0.30" in opportunity_legend
    assert "🟢 > 0.60" in opportunity_legend


def test_derm_uses_fixed_1_3_4_5_6plus_scale_not_tercile():
    # User's own clinical judgment (2026-08-09): tercile made the middle
    # "oranye" bucket nearly useless for doctor counts (with ~91
    # hospitals mostly having 1-4 dermatologists, tercile boundaries
    # landed on "> 3 s.d. 4", matching only an exact count of 4). Fixed
    # scale: 1-3=hijau, 4-5=oranye, 6+=merah, regardless of what
    # tercile boundaries would have computed from the live data.
    spec = MAP_METRICS["Derm"]
    tercile_bounds_that_should_be_ignored = CategoryBoundaries(0, 100, 200, 300)
    for count, expected_color in [(1, "green"), (3, "green"), (4, "orange"), (5, "orange"), (6, "red"), (10, "red")]:
        category = classify_marker(
            count, data_quality="complete", spec=spec, boundaries=tercile_bounds_that_should_be_ignored
        )
        assert category.color == expected_color, f"{count} dokter -> expected {expected_color}, got {category.color}"

    legend = format_metric_legend(spec, tercile_bounds_that_should_be_ignored)
    assert "Skala tetap" in legend
    assert "🟢 ≤ 3" in legend
    assert "🟠 > 3 s.d. 5" in legend
    assert "🔴 > 5" in legend


def test_boundaries_use_distribution_terciles_not_min_max_range():
    # 205 is an outlier. Equal-width min/max buckets would make almost all
    # values green; distribution terciles keep the categories informative.
    values = [0, 7, 8, 10, 12, 15, 20, 30, 40, 50, 205]
    bounds = calculate_category_boundaries(values)
    assert bounds.minimum == 0
    assert bounds.maximum == 205
    assert bounds.lower < 20
    assert bounds.upper < 50


def test_empty_and_uniform_scales_have_explicit_legends():
    # "Derm hrs/wk" used here (not "Derm", which has a fixed scale and so
    # never falls into "no values"/"uniform" tercile edge cases).
    spec = MAP_METRICS["Derm hrs/wk"]
    empty = calculate_category_boundaries([])
    assert not empty.has_values
    assert "tidak ada nilai" in format_metric_legend(spec, empty)
    assert "confirmed zero tetap hijau" in format_metric_legend(spec, empty)

    uniform = calculate_category_boundaries([2, 2, 2])
    assert uniform.has_values
    assert "semua nilai" in format_metric_legend(spec, uniform).lower()
    assert classify_marker(
        2, data_quality="complete", spec=spec, boundaries=uniform
    ).color == "orange"
