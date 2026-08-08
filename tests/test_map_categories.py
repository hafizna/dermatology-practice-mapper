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


def test_confirmed_zero_is_gray_and_excluded_for_every_metric():
    for metric_key in MAP_METRICS:
        spec = MAP_METRICS[metric_key]
        bounds = CategoryBoundaries(0, 3, 6, 9)
        category = classify_marker(
            0.0, data_quality="confirmed_zero", spec=spec, boundaries=bounds
        )
        assert category.color == "gray"
        assert not metric_value_participates_in_scale(
            0.0, data_quality="confirmed_zero", spec=spec
        )


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
    bounds = CategoryBoundaries(0, 3, 6, 9)
    count_legend = format_metric_legend(MAP_METRICS["Derm"], bounds)
    assert "nilai lebih rendah" in count_legend
    assert "🟢 ≤ 3" in count_legend
    assert "🔴 > 6" in count_legend

    opportunity_bounds = CategoryBoundaries(0, 0.3, 0.6, 0.9)
    opportunity_legend = format_metric_legend(MAP_METRICS["Opportunity"], opportunity_bounds)
    assert "nilai lebih tinggi" in opportunity_legend
    assert "🔴 ≤ 0.30" in opportunity_legend
    assert "🟢 > 0.60" in opportunity_legend


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
    spec = MAP_METRICS["Derm"]
    empty = calculate_category_boundaries([])
    assert not empty.has_values
    assert "tidak ada nilai" in format_metric_legend(spec, empty)

    uniform = calculate_category_boundaries([2, 2, 2])
    assert uniform.has_values
    assert "semua nilai" in format_metric_legend(spec, uniform).lower()
    assert classify_marker(
        2, data_quality="complete", spec=spec, boundaries=uniform
    ).color == "orange"
