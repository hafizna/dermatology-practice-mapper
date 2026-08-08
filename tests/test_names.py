"""Fase 4.3: doctor name normalization / cross-hospital identity
resolution tests.

Calibrated against a full-scale audit of 333 real doctor names scraped
across 3 hospital groups in Fase 2/3 (55,278 cross-source pairs checked)
— every case referenced here is a real name pair found in that audit, not
a synthetic example, unless stated otherwise.
"""

from __future__ import annotations

from src.parsing.names import match_doctor_identity, normalize_person_key

# --- normalize_person_key -------------------------------------------------


def test_strips_leading_dr_title():
    assert normalize_person_key("dr. Budi Santoso, Sp.KK") == "budi santoso"


def test_strips_double_doctor_title():
    assert normalize_person_key("Dr. dr. Betty Ekawati Suryaningsih, Sp.DV") == "betty ekawati suryaningsih"


def test_strips_professor_and_double_doctor_title():
    assert (
        normalize_person_key("Prof. Dr. dr. Kabulrachman, Sp.DVE, Subsp.DAI, FINSDV, FAADV")
        == "kabulrachman"
    )


def test_strips_credentials_after_comma():
    assert normalize_person_key("dr. Danny Gunawan, SpDVE, FINSDV") == "danny gunawan"


def test_trailing_title_marker_between_commas():
    # Real scraped data: "Lia Marlia Rudi, dr., SpKK" — title sandwiched
    # between commas rather than leading. The name segment (before the
    # FIRST comma) is correctly isolated regardless of where "dr."
    # appears relative to it.
    assert normalize_person_key("Lia Marlia Rudi, dr., SpKK") == "lia marlia rudi"


def test_lowercases():
    assert normalize_person_key("DR ARIF RISDIANTO") == "arif risdianto"


def test_collapses_whitespace():
    assert normalize_person_key("dr.   Budi   Santoso") == "budi santoso"


def test_strips_punctuation():
    assert normalize_person_key("dr. A. Rendy Laksditalia Nugroho") == "a rendy laksditalia nugroho"


def test_non_breaking_space_normalized():
    assert normalize_person_key("dr. Silvia Wilvestra") == "silvia wilvestra"


def test_empty_string_returns_empty():
    assert normalize_person_key("") == ""


def test_no_comma_no_title_returns_lowercased_name():
    assert normalize_person_key("Budi Santoso") == "budi santoso"


def test_name_with_no_comma_before_credential_is_a_known_limitation():
    # Real scraped data: "dr. Gayanti Germania Sp.KK." has NO comma
    # before the credential (unlike the more common "Name, Sp.KK"
    # shape) — the credential-splitting logic (which splits on the
    # first comma) cannot detect this and the credential text leaks
    # into the key. This is intentionally documented as a known
    # limitation rather than silently "fixed" with a guess at where
    # the name ends and the credential begins — spec §3.1. Downstream
    # callers should expect this normalization gap for the "no comma"
    # shape and rely on match_doctor_identity's token-overlap fallback
    # (still catches this case at "medium" confidence — see
    # test_medium_confidence_when_key_leaks_credential_due_to_missing_comma).
    key = normalize_person_key("dr. Gayanti Germania Sp.KK.")
    assert "spkk" in key or "sp kk" in key  # credential leaked into the key, as documented


# --- match_doctor_identity: real cross-hospital HIGH-confidence cases ----


def test_identical_full_name_different_credential_dotting_is_high():
    result = match_doctor_identity("dr. Danny Gunawan, SpDVE, FINSDV", "dr. Danny Gunawan, Sp.DVE, FINSDV")
    assert result.is_match is True
    assert result.confidence == "high"


def test_identical_name_short_initial_token_is_high():
    # "Armita Asri A" — the trailing single-letter initial "A" is part
    # of an otherwise-identical full name, so the whole-key equality
    # path (not the token-overlap path) correctly handles it.
    result = match_doctor_identity("dr. Armita Asri A, SpDV", "dr. Armita Asri A, Sp.DV")
    assert result.is_match is True
    assert result.confidence == "high"


def test_identical_name_across_three_hospitals_pairwise():
    result = match_doctor_identity("dr. Maria Leleury, SpDV", "dr. Maria Leleury, Sp.DV")
    assert result.is_match is True
    assert result.confidence == "high"


def test_identical_name_with_stray_surrounding_whitespace():
    # Real Hermina data had a name with leading/trailing spaces:
    # " dr. Stephanie Nathania, Sp.DVE " vs Siloam's clean version.
    result = match_doctor_identity("dr. Stephanie Nathania, SpDVE", " dr. Stephanie Nathania, Sp.DVE ")
    assert result.is_match is True
    assert result.confidence == "high"


# --- match_doctor_identity: real cross-hospital MEDIUM-confidence cases --


def test_shared_two_tokens_but_extra_middle_name_is_medium_not_high():
    # "Nila Puspasari Kunta Adjie" vs "Hendrik Kunta Adjie" — shares
    # "kunta"+"adjie" but has a DIFFERENT given name (Nila vs Hendrik).
    # This is very plausibly two different people who share a two-word
    # family name; must not be auto-merged as "high".
    result = match_doctor_identity("dr. Hendrik Kunta Adjie, SpDVE", "dr. Nila Puspasari Kunta Adjie, Sp.KK")
    assert result.confidence == "medium"
    assert result.is_match is True  # flagged for review, not silently discarded


def test_medium_confidence_when_key_leaks_credential_due_to_missing_comma():
    # Real data: "dr. Gayanti Germania Sp.KK." (no comma) vs
    # "dr. Gayanti Germania, Sp.KK" (has comma). The no-comma name's key
    # leaks "spkk" as a trailing token, so whole-key equality fails, but
    # the two substantial shared tokens ("gayanti", "germania") still
    # correctly surface this as a medium-confidence match rather than
    # missing it entirely.
    result = match_doctor_identity("dr. Gayanti Germania Sp.KK.", "dr. Gayanti Germania, Sp.KK")
    assert result.is_match is True
    assert result.confidence == "medium"


def test_short_name_vs_full_name_with_extra_surname_is_medium():
    # "Vonny Indriati" (short) vs "Vonny Indriati Widjojo" (has an extra
    # surname) — plausible same person using an abbreviated name at one
    # hospital, but not certain.
    result = match_doctor_identity("dr. Vonny Indriati, Sp. KK", "dr. Vonny Indriati Widjojo, SpKK")
    assert result.confidence == "medium"


# --- match_doctor_identity: real cross-hospital NON-matches (must reject) -


def test_shared_surname_only_is_not_a_match():
    # THE motivating false-positive case from the spec's own warning
    # (§9 Fase 4.3): two clearly different physicians sharing only the
    # common Indonesian surname "Aryani".
    result = match_doctor_identity("dr. Inda Astri Aryani, SpKK (K)", "dr. Christilla Citra Aryani, Sp.KK")
    assert result.is_match is False
    assert result.confidence == "low"


def test_shared_surname_only_second_real_case():
    result = match_doctor_identity(
        "dr. A. Rendy Laksditalia Nugroho, SpDVE", "dr. Wisnu Triadi Nugroho, M.Ked.Klin., Sp.DV"
    )
    assert result.is_match is False
    assert result.confidence == "low"


def test_completely_different_names_no_match():
    result = match_doctor_identity("dr. Budi Santoso, Sp.KK", "dr. Siti Aisyah, Sp.DVE")
    assert result.is_match is False
    assert result.confidence == "none"


def test_single_token_name_never_matches_even_if_substring():
    # spec explicitly forbids merging on "token pendek" — a single-word
    # name (however it arose) must not participate in token-overlap
    # matching at all, only whole-key equality.
    result = match_doctor_identity("Aryani", "dr. Christilla Citra Aryani, Sp.KK")
    assert result.is_match is False
    assert result.confidence == "none"


def test_empty_names_do_not_match():
    result = match_doctor_identity("", "dr. Budi Santoso, Sp.KK")
    assert result.is_match is False
    assert result.confidence == "none"


def test_shared_single_letter_initial_is_not_counted_as_substantial():
    # Two different doctors who happen to share a single-letter initial
    # token must not use that initial as corroborating evidence.
    result = match_doctor_identity("dr. Budi A, Sp.KK", "dr. Siti A, Sp.DVE")
    assert result.is_match is False


def test_full_scale_audit_produces_no_high_confidence_false_positive():
    # Regression guard mirroring the Fase 4.3 authoring audit: run every
    # documented false-positive-prone pair and assert none reaches
    # "high" — "high" is reserved for whole-name equality only.
    false_positive_pairs = [
        ("dr. Inda Astri Aryani, SpKK (K)", "dr. Christilla Citra Aryani, Sp.KK"),
        ("dr. A. Rendy Laksditalia Nugroho, SpDVE", "dr. Wisnu Triadi Nugroho, M.Ked.Klin., Sp.DV"),
        ("dr. Hendrik Kunta Adjie, SpDVE", "dr. Nila Puspasari Kunta Adjie, Sp.KK"),
    ]
    for a, b in false_positive_pairs:
        result = match_doctor_identity(a, b)
        assert result.confidence != "high", f"{a!r} vs {b!r} wrongly reached high confidence"
