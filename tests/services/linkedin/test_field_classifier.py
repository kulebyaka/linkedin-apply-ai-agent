"""TDD tests for the deterministic Easy Apply field classifier.

Written before the implementation (per the plan's TDD directive for this module —
its correctness is the core risk). Each test feeds a *serialized field* (the shape
produced by ``extension/content_script.js`` ``serialize_form()``) plus an
``ApplyProfile`` / ``ContactInfo`` and asserts the classifier resolves the right
value — or returns ``Unknown`` for anything unrecognized or missing a value.

The cardinal rule under test: the classifier NEVER guesses. An unmatched field,
or a matched field whose backing profile value is absent, must yield ``Unknown``
so the apply workflow aborts to ``manual_required``.
"""

from __future__ import annotations

import pytest

from src.models.cv import ContactInfo
from src.models.user import ApplyProfile
from src.services.linkedin.field_classifier import (
    FieldFill,
    Skip,
    Unknown,
    classify_field,
)


@pytest.fixture
def profile() -> ApplyProfile:
    return ApplyProfile(
        phone_country_code="+1",
        years_experience=7,
        expected_salary="120000",
        needs_visa_sponsorship=False,
        legally_authorized=True,
        willing_to_relocate=True,
        drivers_license=False,
    )


@pytest.fixture
def contact() -> ContactInfo:
    return ContactInfo(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="555-0100",
        location="London, UK",
    )


def _field(label: str, type_: str = "text", **kw) -> dict:
    base = {
        "selector": kw.pop("selector", "#f"),
        "label": label,
        "type": type_,
        "value": kw.pop("value", ""),
        "options": kw.pop("options", []),
        "required": kw.pop("required", True),
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Text fields: names / email / phone / location / years / salary
# ---------------------------------------------------------------------------


def test_first_name(profile, contact):
    r = classify_field(_field("First name"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "first_name"
    assert r.value == "Ada"


def test_last_name(profile, contact):
    r = classify_field(_field("Last name"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "last_name"
    assert r.value == "Lovelace"


def test_email(profile, contact):
    r = classify_field(_field("Email address", type_="email"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "email"
    assert r.value == "ada@example.com"


def test_phone(profile, contact):
    r = classify_field(_field("Mobile phone number", type_="tel"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "phone"
    assert r.value == "555-0100"


def test_phone_country_code_before_phone(profile, contact):
    # A "Phone country code" field contains "phone" but must resolve to the
    # country code, not the bare phone number.
    r = classify_field(
        _field(
            "Phone country code", type_="select", options=["United States (+1)", "France (+33)"]
        ),
        profile,
        contact,
    )
    assert isinstance(r, FieldFill)
    assert r.kind == "phone_country_code"
    assert "+1" in r.value


def test_city_location(profile, contact):
    r = classify_field(_field("City"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "city"
    assert r.value == "London, UK"


def test_years_of_experience(profile, contact):
    r = classify_field(_field("How many years of experience do you have?"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "years_experience"
    assert r.value == "7"


def test_salary(profile, contact):
    r = classify_field(_field("Expected salary"), profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "expected_salary"
    assert r.value == "120000"


def test_multilingual_labels(profile, contact):
    # French / Spanish / German / Italian variants resolve to the same kinds.
    assert classify_field(_field("Prénom"), profile, contact).kind == "first_name"
    assert classify_field(_field("Apellido"), profile, contact).kind == "last_name"
    assert classify_field(_field("Téléphone"), profile, contact).kind == "phone"
    assert (
        classify_field(_field("Années d'expérience"), profile, contact).kind == "years_experience"
    )
    assert classify_field(_field("Ciudad"), profile, contact).kind == "city"


# ---------------------------------------------------------------------------
# Radio Yes/No questions resolved from ApplyProfile booleans
# ---------------------------------------------------------------------------


def test_visa_sponsorship_no(profile, contact):
    r = classify_field(
        _field("Do you require visa sponsorship?", type_="radio", options=["Yes", "No"]),
        profile,
        contact,
    )
    assert isinstance(r, FieldFill)
    assert r.kind == "needs_visa_sponsorship"
    assert r.value == "No"  # profile.needs_visa_sponsorship is False


def test_work_authorization_yes(profile, contact):
    r = classify_field(
        _field(
            "Are you legally authorized to work in this country?",
            type_="radio",
            options=["Yes", "No"],
        ),
        profile,
        contact,
    )
    assert isinstance(r, FieldFill)
    assert r.kind == "legally_authorized"
    assert r.value == "Yes"


def test_relocation_yes(profile, contact):
    r = classify_field(
        _field("Are you willing to relocate?", type_="radio", options=["Yes", "No"]),
        profile,
        contact,
    )
    assert r.kind == "willing_to_relocate"
    assert r.value == "Yes"


def test_drivers_license_no(profile, contact):
    r = classify_field(
        _field("Do you hold a valid driver's license?", type_="radio", options=["Yes", "No"]),
        profile,
        contact,
    )
    assert r.kind == "drivers_license"
    assert r.value == "No"


def test_radio_matches_localized_yes_no_option(profile, contact):
    # Option labels can be localized; resolve True -> the "oui"-like option.
    r = classify_field(
        _field("Êtes-vous autorisé à travailler ?", type_="radio", options=["Oui", "Non"]),
        profile,
        contact,
    )
    assert r.kind == "legally_authorized"
    assert r.value == "Oui"


# ---------------------------------------------------------------------------
# Dropdowns: language proficiency + consent checkbox
# ---------------------------------------------------------------------------


def test_language_proficiency_prefers_native(profile, contact):
    r = classify_field(
        _field(
            "What is your level of proficiency in English?",
            type_="select",
            options=["Select an option", "None", "Professional", "Fluent", "Native or bilingual"],
        ),
        profile,
        contact,
    )
    assert isinstance(r, FieldFill)
    assert r.kind == "language_proficiency"
    assert r.value == "Native or bilingual"


def test_language_proficiency_falls_back_to_fluent(profile, contact):
    r = classify_field(
        _field(
            "Niveau de maîtrise du français",
            type_="select",
            options=["Sélectionner", "Débutant", "Courant", "Professionnel"],
        ),
        profile,
        contact,
    )
    assert r.kind == "language_proficiency"
    assert r.value == "Courant"  # "fluent" tier


def test_consent_checkbox_checked(profile, contact):
    r = classify_field(
        _field("I agree to the terms and conditions", type_="checkbox"),
        profile,
        contact,
    )
    assert isinstance(r, FieldFill)
    assert r.kind == "consent"
    assert r.value == "true"


def test_follow_company_checkbox_skipped(profile, contact):
    # Un-followed at submit time; the classifier must not auto-check it,
    # and it must NOT count as an unknown (that would abort the apply).
    r = classify_field(
        _field("Follow Acme Corp", type_="checkbox", selector="#follow-company-checkbox"),
        profile,
        contact,
    )
    assert isinstance(r, Skip)


def test_file_input_skipped(profile, contact):
    # Resume upload is handled by the dedicated upload_file tool, not classify.
    r = classify_field(_field("Upload resume", type_="file"), profile, contact)
    assert isinstance(r, Skip)


# ---------------------------------------------------------------------------
# The cardinal rule: unknown / missing-value -> Unknown (never a guess)
# ---------------------------------------------------------------------------


def test_unrecognized_screening_question_is_unknown(profile, contact):
    r = classify_field(
        _field("How would you rate your knowledge of Kubernetes from 1-10?"),
        profile,
        contact,
    )
    assert isinstance(r, Unknown)
    assert r.label


def test_unrecognized_radio_is_unknown(profile, contact):
    r = classify_field(
        _field("Have you read our company blog?", type_="radio", options=["Yes", "No"]),
        profile,
        contact,
    )
    assert isinstance(r, Unknown)


def test_unrecognized_select_is_unknown(profile, contact):
    r = classify_field(
        _field("Preferred start date", type_="select", options=["Immediately", "1 month"]),
        profile,
        contact,
    )
    assert isinstance(r, Unknown)


def test_unrecognized_checkbox_is_unknown(profile, contact):
    r = classify_field(
        _field("Subscribe to our newsletter", type_="checkbox"),
        profile,
        contact,
    )
    assert isinstance(r, Unknown)


def test_matched_but_missing_value_is_unknown(contact):
    # Years-of-experience field, but the profile has no value -> never guess.
    empty = ApplyProfile()
    r = classify_field(_field("Years of experience"), empty, contact)
    assert isinstance(r, Unknown)
    assert "years_experience" in r.reason


def test_missing_radio_bool_is_unknown(contact):
    empty = ApplyProfile()
    r = classify_field(
        _field("Do you require visa sponsorship?", type_="radio", options=["Yes", "No"]),
        empty,
        contact,
    )
    assert isinstance(r, Unknown)
    assert "needs_visa_sponsorship" in r.reason


def test_missing_contact_value_is_unknown(profile):
    # Phone field but no phone on the contact card -> Unknown.
    no_phone = ContactInfo(full_name="Ada Lovelace", email="ada@example.com")
    r = classify_field(_field("Phone number", type_="tel"), profile, no_phone)
    assert isinstance(r, Unknown)


def test_none_contact_info_text_field_is_unknown(profile):
    r = classify_field(_field("First name"), profile, None)
    assert isinstance(r, Unknown)


def test_accepts_pydantic_serialized_field(profile, contact):
    # classify_field should accept either a dict or a SerializedField model.
    from src.services.linkedin.field_classifier import SerializedField

    sf = SerializedField(selector="#x", label="Email", type="email")
    r = classify_field(sf, profile, contact)
    assert isinstance(r, FieldFill)
    assert r.kind == "email"
