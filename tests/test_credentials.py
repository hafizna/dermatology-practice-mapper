"""Fase 4.1: dermatologist credential detection tests.

Covers the spec §9 Fase 4.1 valid/false-positive lists verbatim, plus
real-world variants collected from Fase 2/3 scraped data (280+ distinct
doctor name strings across 10 hospital groups) — spacing, dotting, casing,
and trailing-credential variations that the spec's short example list
doesn't cover but that real sources actually produce.
"""

from __future__ import annotations

import pytest

from src.parsing.credentials import is_dermatologist_credential

# --- spec §9 Fase 4.1 valid examples (verbatim) ------------------------

VALID_SPEC_EXAMPLES = [
    "Sp.KK",
    "Sp.DV",
    "Sp.DVE",
    "SpKK",
    "Sp. K.K.",
    "Sp.DVE(K)",
    "Sp.KK(K)",
    "Dermatologi dan Venereologi",
    "Dermatovenereologi",
    "Kulit dan Kelamin",
]


@pytest.mark.parametrize("credential", VALID_SPEC_EXAMPLES)
def test_spec_valid_examples_detected(credential: str):
    assert is_dermatologist_credential(f"dr. Contoh Nama, {credential}") is True


# --- spec §9 Fase 4.1 false-positive examples (verbatim) ---------------

FALSE_POSITIVE_SPEC_EXAMPLES = [
    "Sp.KKLP",
    "Sp.KJ",
    "Sp.KFR",
    "Sp.KL",
    "Sp.KO",
    "Sp.KN",
    "Sp.KG",
    "Sp.KKV",
]


@pytest.mark.parametrize("credential", FALSE_POSITIVE_SPEC_EXAMPLES)
def test_spec_false_positives_rejected(credential: str):
    assert is_dermatologist_credential(f"dr. Contoh Nama, {credential}") is False


# --- real-world variants from Fase 2/3 scraped data ---------------------


def test_no_dot_no_space_spkk():
    assert is_dermatologist_credential("dr. Budi Santoso, SpKK") is True


def test_no_dot_no_space_spdve():
    assert is_dermatologist_credential("dr. Budi Santoso, SpDVE") is True


def test_no_dot_no_space_spdv():
    assert is_dermatologist_credential("dr. Budi Santoso, SpDV") is True


def test_space_separated_sp_kk():
    assert is_dermatologist_credential("dr. Dani Djuanda, Sp KK") is True


def test_every_letter_dotted_dve():
    assert is_dermatologist_credential("dr. Arif Widiatmoko, Sp.D.V.E") is True


def test_trailing_period_after_credential():
    assert is_dermatologist_credential("dr. Adi Gunadi Sp.KK.") is True


def test_konsultan_suffix_parens_with_space():
    assert is_dermatologist_credential("Prof. dr. Siti Aisah, SpKK (K)") is True


def test_konsultan_suffix_parens_no_space():
    assert is_dermatologist_credential("Prof. dr. Siti Aisah, SpKK(K)") is True


def test_konsultan_suffix_dash():
    assert is_dermatologist_credential("Prof. Theresia L. Toruan, SpKK-K") is True


def test_uppercase_spkk():
    assert is_dermatologist_credential("DR ARIF RISDIANTO, SPKK., M.KES") is True


def test_trailing_fellowship_credentials_finsdv():
    assert is_dermatologist_credential("dr. Fahmi Rizal, SpDVE, FINSDV") is True


def test_trailing_fellowship_credentials_faadv():
    assert is_dermatologist_credential("dr. Poppy Syafnita, Sp.DVE, FINSDV, FAADV") is True


def test_trailing_subspecialist_credential():
    assert is_dermatologist_credential("dr. Bagus Haryo K, Sp.DVE., Subsp. O.B.K") is True


def test_leading_double_doctor_title():
    assert is_dermatologist_credential("Dr. dr. Betty Ekawati Suryaningsih, Sp.DV") is True


def test_leading_professor_title():
    assert is_dermatologist_credential("Prof. Dr. dr. Kabulrachman, Sp.DVE") is True


def test_credential_before_dr_title():
    # "Lia Marlia Rudi, dr., SpKK" — credential appears after the "dr."
    # marker rather than immediately after the name; detection must not
    # assume a fixed position.
    assert is_dermatologist_credential("Lia Marlia Rudi, dr., SpKK") is True


def test_dv_not_confused_with_dve_prefix():
    # "Sp.DV" alone (not part of a longer "Sp.DVE") must still match —
    # regression guard for the DVE-vs-DV negative lookahead.
    assert is_dermatologist_credential("dr. Contoh, Sp.DV") is True


def test_dve_matches_fully_not_truncated_as_dv():
    assert is_dermatologist_credential("dr. Contoh, Sp.DVE") is True


def test_non_breaking_space_in_name():
    assert is_dermatologist_credential("dr. Silvia Wilvestra, Sp.D.V.E.") is True


def test_full_word_dermatologi_venereologi():
    assert is_dermatologist_credential("Spesialis Dermatologi dan Venereologi") is True


def test_full_word_dermatovenereologi():
    assert is_dermatologist_credential("Spesialis Dermatovenereologi") is True


def test_full_word_kulit_dan_kelamin():
    assert is_dermatologist_credential("Spesialis Kulit dan Kelamin") is True


def test_full_word_english_dermatology_and_venereology():
    assert is_dermatologist_credential("Dermatology and Venereology Specialist") is True


def test_multiple_credentials_dermatology_not_first():
    # A dual-boarded physician's non-dermatology credential must not mask
    # a real dermatology credential appearing later in the same string.
    assert is_dermatologist_credential("dr. Contoh, Sp.KJ, Sp.KK") is True


def test_empty_string_rejected():
    assert is_dermatologist_credential("") is False


def test_none_like_empty_rejected():
    assert is_dermatologist_credential("   ") is False


def test_unrelated_specialty_only_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.OG") is False


def test_general_practitioner_no_specialty_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama") is False


def test_spkkv_false_positive_with_trailing_text():
    # Sp.KKV embedded with trailing fellowship-style text must still be
    # correctly rejected, not just the bare credential in isolation.
    assert is_dermatologist_credential("dr. Contoh, Sp.KKV, FIHA") is False


def test_spkklp_with_different_spacing():
    assert is_dermatologist_credential("dr. Contoh, Sp KKLP") is False


def test_known_typo_missing_p_is_not_guessed_as_dermatology():
    # "S.DV" (missing the "p") appeared verbatim in real Hermina-sourced
    # data for one doctor across multiple fields — a genuine source-side
    # typo, not a scraping/parsing bug on our end. Per spec §3.1 ("jangan
    # mengarang data"), we do not guess this is Sp.DV; it is correctly
    # rejected and should surface for manual review instead.
    assert is_dermatologist_credential("dr. RR. Kharisma Yuliasis, M.Sc, S.DV") is False


def test_kg_dentistry_not_confused_with_kk():
    assert is_dermatologist_credential("drg. Contoh Nama, Sp.KG") is False


def test_kfr_rehabilitation_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.K.F.R") is False


def test_kl_maritime_medicine_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.KL") is False


def test_ko_sports_medicine_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.KO") is False


def test_kn_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.KN") is False


def test_kj_psychiatry_rejected():
    assert is_dermatologist_credential("dr. Contoh Nama, Sp.KJ") is False
