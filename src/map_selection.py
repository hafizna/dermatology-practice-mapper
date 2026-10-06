"""Resolve a map click against the visible hospitals without guessing."""
from __future__ import annotations

import pandas as pd


def selected_hospital_from_map(event: dict | None, hospitals: pd.DataFrame) -> int | None:
    if not event or hospitals.empty:
        return None
    location = event.get("last_object_clicked")
    name = event.get("last_object_clicked_tooltip")
    if not isinstance(location, dict) or not isinstance(name, str):
        return None
    try:
        lat, lon = float(location["lat"]), float(location["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    matches = hospitals[
        (hospitals["Hospital"] == name.strip())
        & ((hospitals["lat"] - lat).abs() < 0.000001)
        & ((hospitals["lon"] - lon).abs() < 0.000001)
    ]
    return int(matches.iloc[0]["hospital_id"]) if len(matches) == 1 else None
