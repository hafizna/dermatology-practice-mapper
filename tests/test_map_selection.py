import pandas as pd
import pytest

from src.map_selection import selected_hospital_from_map


@pytest.fixture
def hospitals():
    return pd.DataFrame([
        {"hospital_id": 10, "Hospital": "RS A", "lat": -6.2, "lon": 106.8},
        {"hospital_id": 11, "Hospital": "RS B", "lat": -6.2, "lon": 106.8},
        {"hospital_id": 12, "Hospital": "RS A", "lat": -6.3, "lon": 106.9},
    ])


def test_click_matches_name_and_coordinates_even_when_names_or_locations_repeat(hospitals):
    event = {"last_object_clicked": {"lat": -6.2, "lng": 106.8}, "last_object_clicked_tooltip": "RS B"}
    assert selected_hospital_from_map(event, hospitals) == 11
    event["last_object_clicked_tooltip"] = "RS A"
    assert selected_hospital_from_map(event, hospitals) == 10
    event["last_object_clicked"] = {"lat": -6.3, "lng": 106.9}
    assert selected_hospital_from_map(event, hospitals) == 12


@pytest.mark.parametrize("event", [None, {}, {"last_object_clicked": {"lat": -6.2, "lng": 106.8}},
    {"last_object_clicked": {"lat": "invalid", "lng": 106.8}, "last_object_clicked_tooltip": "RS A"},
    {"last_object_clicked": {"lat": -6.2, "lng": 106.8}, "last_object_clicked_tooltip": "Filtered out RS"}])
def test_missing_invalid_or_filtered_click_does_not_select_another_hospital(hospitals, event):
    assert selected_hospital_from_map(event, hospitals) is None


def test_ambiguous_click_does_not_guess(hospitals):
    hospitals = pd.concat([hospitals, hospitals.iloc[:1].assign(hospital_id=99)])
    event = {"last_object_clicked": {"lat": -6.2, "lng": 106.8}, "last_object_clicked_tooltip": "RS A"}
    assert selected_hospital_from_map(event, hospitals) is None
