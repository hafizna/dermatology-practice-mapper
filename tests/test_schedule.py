"""Fase 4.2: schedule parser tests.

Covers day-of-week normalization (including the user-verified Siloam
numeric mapping and the deliberately-lower-confidence Brawijaya one),
free-text time-range parsing (spec §9 Fase 4.2 examples plus real-world
edge cases from Fase 2/3 scraped data: dotted time separators, multi-
session days, ambiguous concatenation, "selesai", "Dengan Perjanjian"),
and the per-source structured-entry parsers.
"""

from __future__ import annotations

from src.parsing.schedule import (
    ParsedScheduleSlot,
    normalize_day_of_week,
    parse_schedule_entries,
    parse_schedule_entries_by_hospital,
    parse_time_range_text,
)

# --- normalize_day_of_week ----------------------------------------------


def test_indonesian_day_names():
    assert normalize_day_of_week("Senin") == 0
    assert normalize_day_of_week("Selasa") == 1
    assert normalize_day_of_week("Rabu") == 2
    assert normalize_day_of_week("Kamis") == 3
    assert normalize_day_of_week("Jumat") == 4
    assert normalize_day_of_week("Sabtu") == 5
    assert normalize_day_of_week("Minggu") == 6


def test_indonesian_day_names_case_insensitive():
    assert normalize_day_of_week("SENIN") == 0
    assert normalize_day_of_week("senin") == 0


def test_english_day_names():
    assert normalize_day_of_week("Monday") == 0
    assert normalize_day_of_week("Tuesday") == 1
    assert normalize_day_of_week("Sunday") == 6


def test_english_day_names_case_insensitive():
    assert normalize_day_of_week("MONDAY") == 0


def test_indonesian_abbreviations():
    assert normalize_day_of_week("Sen") == 0
    assert normalize_day_of_week("Sab") == 5
    assert normalize_day_of_week("Min") == 6


def test_none_input_returns_none():
    assert normalize_day_of_week(None) is None


def test_empty_string_returns_none():
    assert normalize_day_of_week("") is None
    assert normalize_day_of_week("   ") is None


def test_unrecognized_day_text_returns_none():
    assert normalize_day_of_week("Someday") is None


def test_numeric_day_without_source_returns_none():
    # No source given -> no convention to trust -> None, never guessed.
    assert normalize_day_of_week(1) is None


def test_numeric_day_with_unmapped_source_returns_none():
    assert normalize_day_of_week(1, source="mitra_keluarga") is None


def test_siloam_numeric_day_1_is_senin():
    # User-verified 2026-08-08 against the live siloamhospitals.com
    # schedule widget: day=1 renders as "Senin".
    assert normalize_day_of_week(1, source="siloam") == 0


def test_siloam_numeric_day_6_is_sabtu():
    assert normalize_day_of_week(6, source="siloam") == 5


def test_siloam_numeric_day_7_is_minggu():
    assert normalize_day_of_week(7, source="siloam") == 6


def test_siloam_numeric_day_as_string_digit():
    # Some payloads may carry the day as a numeric string; must resolve
    # the same as the int form for a mapped source.
    assert normalize_day_of_week("1", source="siloam") == 0


def test_brawijaya_numeric_weekday_1_is_monday():
    assert normalize_day_of_week(1, source="brawijaya") == 0


def test_brawijaya_weekday_0_is_unresolved():
    # Deliberately absent from the mapping — could be Sunday in a 0- or
    # 7-indexed scheme, not resolved, must not guess.
    assert normalize_day_of_week(0, source="brawijaya") is None


# --- parse_time_range_text -----------------------------------------------


def test_colon_separated_range():
    result = parse_time_range_text("08:00 - 12:00")
    assert len(result) == 1
    assert result[0].start_time == "08:00"
    assert result[0].end_time == "12:00"
    assert result[0].parse_confidence == "high"


def test_dot_separated_range_primaya_style():
    # Primaya uses dots, not colons: "14.00 - 18.00"
    result = parse_time_range_text("14.00 - 18.00")
    assert len(result) == 1
    assert result[0].start_time == "14:00"
    assert result[0].end_time == "18:00"
    assert result[0].parse_confidence == "high"


def test_selesai_open_ended_end_time_is_none():
    # spec §9 Fase 4.2: "Sabtu 09.00 - selesai" -> end_time=None, never guessed.
    result = parse_time_range_text("09.00 - selesai")
    assert len(result) == 1
    assert result[0].start_time == "09:00"
    assert result[0].end_time is None
    assert result[0].parse_confidence == "high"


def test_dengan_perjanjian_no_fixed_hours():
    # spec §9 Fase 4.2 example — by-appointment-only, not an error.
    result = parse_time_range_text("Dengan Perjanjian")
    assert len(result) == 1
    assert result[0].start_time is None
    assert result[0].end_time is None
    assert result[0].parse_confidence == "medium"


def test_multi_session_with_dan_splits_into_two_high_confidence_entries():
    # Real Primaya data: "08.30 - 10.30 dan 13.30 - 14.30"
    result = parse_time_range_text("08.30 - 10.30 dan 13.30 - 14.30")
    assert len(result) == 2
    assert result[0].start_time == "08:30"
    assert result[0].end_time == "10:30"
    assert result[0].parse_confidence == "high"
    assert result[1].start_time == "13:30"
    assert result[1].end_time == "14:30"
    assert result[1].parse_confidence == "high"


def test_multi_session_with_english_and():
    result = parse_time_range_text("09:00 - 11:00 and 14:00 - 16:00")
    assert len(result) == 2
    assert result[0].end_time == "11:00"
    assert result[1].start_time == "14:00"


def test_ambiguous_concatenated_ranges_no_separator_is_low_confidence():
    # Real Bethsaida data: two ranges glued together with no separator.
    # Must NOT be guessed apart — spec §3.1.
    result = parse_time_range_text("14:00-16:0014:00-17:00")
    assert len(result) == 1
    assert result[0].start_time is None
    assert result[0].end_time is None
    assert result[0].parse_confidence == "low"
    # Raw text preserved untouched for audit.
    assert result[0].raw_text == "14:00-16:0014:00-17:00"


def test_messy_whitespace_from_html_extraction_is_cleaned():
    # Real Bethsaida data has tabs/newlines from raw HTML text extraction.
    result = parse_time_range_text("14:00\t\t\t\t\t\t\t\t-\n\n\t\t\t\t\t\t\t\t17:00")
    assert result[0].start_time == "14:00"
    assert result[0].end_time == "17:00"
    assert result[0].parse_confidence == "high"


def test_trailing_wib_suffix_does_not_break_parsing():
    # Real Mayapada data: "13:00 -\n...19:00 WIB"
    result = parse_time_range_text("13:00 -\n                    19:00 WIB")
    assert result[0].start_time == "13:00"
    assert result[0].end_time == "19:00"


def test_empty_text_is_low_confidence():
    result = parse_time_range_text("")
    assert result[0].parse_confidence == "low"
    assert result[0].start_time is None


def test_completely_unparseable_text_is_low_confidence():
    result = parse_time_range_text("Hubungi kami untuk info lebih lanjut")
    assert result[0].parse_confidence == "low"
    assert result[0].start_time is None


# --- parse_schedule_entries: per-source structured parsing --------------


def test_siloam_structured_entries():
    entries = [
        {"day": 1, "from_time": "14:00:00", "to_time": "17:30:00"},
        {"day": 6, "from_time": "15:00:00", "to_time": "19:00:00"},
    ]
    slots = parse_schedule_entries(entries, source="siloam")
    assert slots[0] == ParsedScheduleSlot(0, "14:00", "17:30", "1 14:00:00-17:30:00", "high")
    assert slots[1].day_of_week == 5


def test_hermina_nested_structured_entries():
    # Hermina's raw_schedule_entries shape: [{"<hospital>": {"<clinic>": [...]}}]
    entries = [
        {
            "Hermina Depok": {
                "Klinik Kulit dan Kelamin": [
                    {"day": "monday", "from_time": "10:00", "to_time": "12:00"},
                ]
            }
        }
    ]
    slots = parse_schedule_entries(entries, source="hermina")
    assert len(slots) == 1
    assert slots[0].day_of_week == 0
    assert slots[0].start_time == "10:00"


def test_rs_pondok_indah_nested_structured_entries():
    entries = [
        {
            "clinics": [
                {
                    "clinic_name": "Dermatology",
                    "schedules": [{"day": "Monday", "time_from": "14:00:00", "time_to": "20:00:00"}],
                }
            ]
        }
    ]
    slots = parse_schedule_entries(entries, source="rs_pondok_indah")
    assert len(slots) == 1
    assert slots[0].day_of_week == 0
    assert slots[0].start_time == "14:00"
    assert slots[0].end_time == "20:00"


def test_mitra_keluarga_dated_entries_are_medium_confidence():
    # Concrete-date-derived recurring pattern -> medium, not high (spec:
    # inference, not a source-declared recurring rule).
    entries = [{"date": "2026-08-09", "day": "Minggu", "start_time": "10:00", "end_time": "12:00"}]
    slots = parse_schedule_entries(entries, source="mitra_keluarga")
    assert slots[0].day_of_week == 6
    assert slots[0].parse_confidence == "medium"


def test_mitra_keluarga_repeated_weekly_dates_deduplicate_to_one_slot():
    # Regression guard for a real Fase 6 bug: the source returns several
    # weeks' worth of concrete future bookings for the SAME recurring
    # weekly slot (e.g. every Tuesday 11:00-13:00, appearing once per
    # week for 4+ weeks) — without deduplication, doctor_hours_week was
    # inflated 4x (one real hospital showed 358 hours/week for 6 doctors
    # before this fix; should be a much smaller, plausible number).
    entries = [
        {"date": "2026-08-11", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"},
        {"date": "2026-08-18", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"},
        {"date": "2026-08-25", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"},
        {"date": "2026-09-01", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"},
    ]
    slots = parse_schedule_entries(entries, source="mitra_keluarga")
    assert len(slots) == 1
    assert slots[0].day_of_week == 1  # Selasa
    assert slots[0].start_time == "11:00"
    assert slots[0].end_time == "13:00"


def test_mitra_keluarga_different_slots_on_same_day_not_collapsed():
    # A doctor with TWO distinct sessions on the same weekday (different
    # times) must keep both — dedup is keyed on (day, start, end), not
    # day alone.
    entries = [
        {"date": "2026-08-10", "day": "Senin", "start_time": "10:00:00", "end_time": "13:00:00"},
        {"date": "2026-08-10", "day": "Senin", "start_time": "15:00:00", "end_time": "18:00:00"},
        {"date": "2026-08-17", "day": "Senin", "start_time": "10:00:00", "end_time": "13:00:00"},
        {"date": "2026-08-17", "day": "Senin", "start_time": "15:00:00", "end_time": "18:00:00"},
    ]
    slots = parse_schedule_entries(entries, source="mitra_keluarga")
    assert len(slots) == 2
    times = {(s.start_time, s.end_time) for s in slots}
    assert times == {("10:00", "13:00"), ("15:00", "18:00")}


def test_mitra_keluarga_different_doctors_at_same_hospital_not_cross_deduplicated():
    # Dedup happens per parse_schedule_entries() CALL, which is always
    # scoped to one doctor's own entries (per persist_doctor_record) — a
    # sanity check that two calls for different doctors don't share
    # dedup state across calls.
    entries_doctor_a = [{"date": "2026-08-11", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"}]
    entries_doctor_b = [{"date": "2026-08-11", "day": "Selasa", "start_time": "11:00:00", "end_time": "13:00:00"}]
    slots_a = parse_schedule_entries(entries_doctor_a, source="mitra_keluarga")
    slots_b = parse_schedule_entries(entries_doctor_b, source="mitra_keluarga")
    assert len(slots_a) == 1
    assert len(slots_b) == 1


def test_emc_numeric_day_2_is_selasa():
    # Self-verified 2026-08-08 by cross-referencing every doctor card's
    # day=N appointment link against the Indonesian day-name table header
    # it appears under, corroborated across many cards with no
    # contradictions — see _SOURCE_NUMERIC_DAY_OFFSETS note.
    entries = [{"day": "2", "start_time": "17:30:00", "end_time": "20:00:00"}]
    slots = parse_schedule_entries(entries, source="emc")
    assert slots[0].day_of_week == 1  # Selasa
    assert slots[0].parse_confidence == "high"


def test_emc_numeric_day_7_unconfirmed_is_low_confidence():
    # day=7 (Minggu) was never observed in the verified snapshot — must
    # not extend the confirmed Mon-Sat pattern to it without evidence.
    entries = [{"day": "7", "start_time": "10:00:00", "end_time": "12:00:00"}]
    slots = parse_schedule_entries(entries, source="emc")
    assert slots[0].day_of_week is None
    assert slots[0].parse_confidence == "low"


def test_brawijaya_structured_entries_are_medium_confidence():
    entries = [{"weekday": 1, "start_hour": "9", "start_minute": 0, "end_hour": "12", "end_minute": 0}]
    slots = parse_schedule_entries(entries, source="brawijaya")
    assert slots[0].day_of_week == 0
    assert slots[0].start_time == "09:00"
    assert slots[0].end_time == "12:00"
    assert slots[0].parse_confidence == "medium"


def test_bethsaida_freetext_entries():
    entries = [{"day_text": "Senin", "time_text": "14:00 - 17:00"}]
    slots = parse_schedule_entries(entries, source="bethsaida")
    assert slots[0].day_of_week == 0
    assert slots[0].start_time == "14:00"
    assert slots[0].parse_confidence == "high"


def test_mayapada_freetext_entries():
    entries = [{"day_text": "Monday", "time_text": "13:00 - 19:00 WIB"}]
    slots = parse_schedule_entries(entries, source="mayapada")
    assert slots[0].day_of_week == 0
    assert slots[0].end_time == "19:00"


def test_primaya_html_fragment_entries():
    html = (
        "<div class='schedule-day-row'>"
        "<span class='schedule-day-label'>Senin</span>"
        "<span class='schedule-day-time'>14.00 - 18.00<br/>(Tatap Muka)</span>"
        "</div>"
    )
    entries = [{"raw_html": html}]
    slots = parse_schedule_entries(entries, source="primaya")
    assert len(slots) == 1
    assert slots[0].day_of_week == 0
    assert slots[0].start_time == "14:00"
    assert slots[0].end_time == "18:00"


def test_primaya_html_fragment_multi_session():
    html = (
        "<div class='schedule-day-row'>"
        "<span class='schedule-day-label'>Selasa</span>"
        "<span class='schedule-day-time'>08.30 - 10.30 dan 13.30 - 14.30<br/>(Tatap Muka)</span>"
        "</div>"
    )
    entries = [{"raw_html": html}]
    slots = parse_schedule_entries(entries, source="primaya")
    assert len(slots) == 2
    assert slots[0].start_time == "08:30"
    assert slots[1].start_time == "13:30"


def test_unrecognized_source_returns_low_confidence_without_guessing():
    slots = parse_schedule_entries([{"whatever": "shape"}], source="some_new_unhandled_source")
    assert len(slots) == 1
    assert slots[0].day_of_week is None


# --- parse_schedule_entries_by_hospital: multi-branch doctors ------------
#
# Regression coverage for a real Fase 4.5 pipeline bug: Hermina, RSPI and
# Primaya can report ONE doctor at SEVERAL branches within a single raw
# record. The flat parse_schedule_entries() pools every branch's slots
# together, which would wrongly attach branch A's schedule to branch B's
# Doctor row too. parse_schedule_entries_by_hospital() must keep each
# branch's slots separate.


def test_returns_none_for_single_branch_sources():
    # Sources that report exactly one hospital per record don't need
    # branch-scoping — None signals "use the flat parser instead", not
    # "this doctor has no schedule".
    assert parse_schedule_entries_by_hospital([], source="siloam") is None
    assert parse_schedule_entries_by_hospital([], source="emc") is None


def test_hermina_by_hospital_keeps_branches_separate():
    entries = [
        {
            "Hermina Depok": {
                "Klinik Kulit": [
                    {"day": "monday", "from_time": "10:00", "to_time": "12:00"},
                ]
            },
            "Hermina Jatinegara": {
                "Klinik Kulit": [
                    {"day": "tuesday", "from_time": "14:00", "to_time": "16:00"},
                ]
            },
        }
    ]
    by_hospital = parse_schedule_entries_by_hospital(entries, source="hermina")
    assert set(by_hospital.keys()) == {"Hermina Depok", "Hermina Jatinegara"}
    assert len(by_hospital["Hermina Depok"]) == 1
    assert by_hospital["Hermina Depok"][0].day_of_week == 0
    assert len(by_hospital["Hermina Jatinegara"]) == 1
    assert by_hospital["Hermina Jatinegara"][0].day_of_week == 1


def test_rspi_by_hospital_keeps_branches_separate():
    entries = [
        {
            "hospital": "RS Pondok Indah - Puri Indah",
            "clinics": [{"schedules": [{"day": "Monday", "time_from": "09:00:00", "time_to": "15:00:00"}]}],
        },
        {
            "hospital": "RS Pondok Indah - Bintaro Jaya",
            "clinics": [{"schedules": [{"day": "Tuesday", "time_from": "10:00:00", "time_to": "12:00:00"}]}],
        },
    ]
    by_hospital = parse_schedule_entries_by_hospital(entries, source="rs_pondok_indah")
    assert set(by_hospital.keys()) == {"RS Pondok Indah - Puri Indah", "RS Pondok Indah - Bintaro Jaya"}
    assert by_hospital["RS Pondok Indah - Puri Indah"][0].day_of_week == 0
    assert by_hospital["RS Pondok Indah - Bintaro Jaya"][0].day_of_week == 1


def test_primaya_by_hospital_keeps_branches_separate():
    # Real shape (confirmed Fase 4.5 pipeline testing): one doctor's
    # schedule_html can contain multiple ".schedule-item" blocks, each
    # tagged with its own ".schedule-hospital" branch name.
    html = (
        "<div class='schedule-item'>"
        "<div class='schedule-hospital'>Primaya Hospital Bekasi Timur</div>"
        "<div class='schedule-day-row'>"
        "<span class='schedule-day-label'>Senin</span>"
        "<span class='schedule-day-time'>14.00 - 18.00<br/>(Tatap Muka)</span>"
        "</div>"
        "</div>"
        "<div class='schedule-item'>"
        "<div class='schedule-hospital'>Primaya Hospital Tangerang</div>"
        "<div class='schedule-day-row'>"
        "<span class='schedule-day-label'>Rabu</span>"
        "<span class='schedule-day-time'>09.00 - 11.00<br/>(Tatap Muka)</span>"
        "</div>"
        "</div>"
    )
    entries = [{"raw_html": html}]
    by_hospital = parse_schedule_entries_by_hospital(entries, source="primaya")
    assert set(by_hospital.keys()) == {"Primaya Hospital Bekasi Timur", "Primaya Hospital Tangerang"}
    assert by_hospital["Primaya Hospital Bekasi Timur"][0].day_of_week == 0
    assert by_hospital["Primaya Hospital Bekasi Timur"][0].start_time == "14:00"
    assert by_hospital["Primaya Hospital Tangerang"][0].day_of_week == 2
    assert by_hospital["Primaya Hospital Tangerang"][0].start_time == "09:00"


def test_primaya_by_hospital_empty_html_returns_empty_dict():
    assert parse_schedule_entries_by_hospital([{"raw_html": ""}], source="primaya") == {}


def test_eka_visible_schedule_entry_is_parsed():
    entries = [
        {
            "hospital": "EKA Hospital BSD",
            "day_text": "Selasa",
            "time_text": "16:30 - 20:00",
        }
    ]
    slots = parse_schedule_entries(entries, source="eka")
    assert len(slots) == 1
    assert (slots[0].day_of_week, slots[0].start_time, slots[0].end_time) == (
        1,
        "16:30",
        "20:00",
    )


def test_eka_by_hospital_does_not_copy_selected_schedule_to_other_branch():
    entries = [
        {
            "hospital": "RSIA Eka Hospital PIK",
            "day_text": "Sabtu",
            "time_text": "08:00 - 10:00",
        }
    ]
    by_hospital = parse_schedule_entries_by_hospital(entries, source="eka")
    assert set(by_hospital) == {"RSIA Eka Hospital PIK"}
    assert by_hospital["RSIA Eka Hospital PIK"][0].day_of_week == 5


def test_missing_day_field_is_low_confidence_not_dropped():
    entries = [{"day": None, "from_time": "10:00:00", "to_time": "12:00:00"}]
    slots = parse_schedule_entries(entries, source="siloam")
    assert len(slots) == 1
    assert slots[0].day_of_week is None
    assert slots[0].parse_confidence == "low"
