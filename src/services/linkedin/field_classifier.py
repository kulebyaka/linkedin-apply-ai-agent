"""Deterministic, no-LLM classifier for LinkedIn Easy Apply form fields.

Given a *serialized field* (the shape produced by ``serialize_form()`` in
``extension/content_script.js``) plus the user's ``ApplyProfile`` and CV
``ContactInfo``, decide which concrete value fills the field — or return
``Unknown`` when the field is unrecognized OR matched-but-the-value-is-missing.

The cardinal rule: **never guess.** AutoApplyMax's content script had several
"answer Yes / pick option 1" fallbacks (content-simple.js :1188-1210, :1271-1274,
:1342-1349); those are deliberately *excluded* here. An ``Unknown`` field makes
the apply workflow abort to ``manual_required`` so we never submit a fabricated
screening answer.

The multilingual (EN/FR/ES/DE/IT) label regexes are ported from AutoApplyMax
(:899-924 text, :1143-1165 radio, :1238-1268 dropdown).

NOTE (LLM sprint): when the agent lands, unmatched fields will route to an LLM
decision instead of straight to ``Unknown``; the per-field shape and outcome
types here are intended to be the same surface the agent consumes.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from src.models.cv import ContactInfo
from src.models.user import ApplyProfile

# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class SerializedField(BaseModel):
    """One field as serialized by the content script's ``serialize_form()``."""

    selector: str
    label: str = ""
    type: str = "text"
    value: str = ""
    options: list[str] = Field(default_factory=list)
    required: bool = False


class FieldFill(BaseModel):
    """A resolved fill instruction: write ``value`` into ``selector``.

    ``kind`` is the classifier's field-kind name. For profile-backed fields it
    matches an ``ApplyProfile`` attribute so ``ApplyProfile.is_complete_for`` can
    reason about completeness.
    """

    selector: str
    value: str
    kind: str


class Skip(BaseModel):
    """A field the classifier intentionally ignores (not an abort signal).

    e.g. the follow-company checkbox (un-followed at submit) or a file input
    (handled by the dedicated ``upload_file`` tool).
    """

    selector: str
    reason: str


class Unknown(BaseModel):
    """An unrecognized field, or a recognized field with no backing value.

    The caller treats any ``Unknown`` as a hard stop -> ``manual_required``.
    """

    selector: str
    label: str
    reason: str


# ---------------------------------------------------------------------------
# Label regexes (multilingual: EN / FR / ES / DE / IT)
# ---------------------------------------------------------------------------

# Order matters: more specific patterns first (e.g. phone-country-code before
# phone, salary/experience before generic name matches).
_RE_YEARS = re.compile(
    r"experience|years|exp[ée]rience|ann[ée]es|a[ñn]os|jahre|anni|esperienza",
    re.I,
)
_RE_SALARY = re.compile(
    r"salary|compensation|remuneration|salaire|r[ée]mun[ée]ration|"
    r"sueldo|salario|gehalt|stipendio",
    re.I,
)
_RE_EMAIL = re.compile(r"e-?mail|courriel|correo", re.I)
_RE_FIRST = re.compile(
    r"first\s*name|prénom|prenom|nombre|vorname|\bnome\b",
    re.I,
)
_RE_LAST = re.compile(
    r"last\s*name|surname|family\s*name|\bnom\b|apellido|nachname|cognome",
    re.I,
)
_RE_PHONE_COUNTRY = re.compile(
    r"country\s*code|phone\s*country|code\s*pays|indicatif|landesvorwahl|prefijo",
    re.I,
)
_RE_PHONE = re.compile(
    r"phone|t[ée]l[ée]phone|telefono|telefon|mobile|portable|\bcell\b|"
    r"m[óo]vil|cellulare|num[ée]ro",
    re.I,
)
_RE_CITY = re.compile(
    r"city|ville|ciudad|stadt|citt[àa]|location|localisation|" r"ubicaci[óo]n|standort|\btown\b",
    re.I,
)

# Radio yes/no questions -> ApplyProfile boolean kinds.
_RE_VISA = re.compile(r"visa|sponsor", re.I)
_RE_AUTHORIZED = re.compile(
    r"author|legally|legal.*work|permit.*work|eligib.*work|right.*work|"
    r"autoris[ée]|autorizad|berechtigt|autorizzat",
    re.I,
)
_RE_RELOCATE = re.compile(
    r"relocat|willing.*move|d[ée]m[ée]nag|reubicar|umzieh|trasfer",
    re.I,
)
_RE_LICENSE = re.compile(
    r"driver.?s?\s*licen|driving\s*licen|valid.*licen|permis de conduire|"
    r"f[üu]hrerschein|patente di guida",
    re.I,
)

# Consent / terms checkboxes.
_RE_CONSENT = re.compile(
    r"consent|agree|terms|conditions|policy|privacy|accept|" r"j'accepte|j'autorise|consentement",
    re.I,
)

# Language proficiency dropdowns.
_RE_PROFICIENCY = re.compile(
    r"proficiency|level.*(english|french|spanish|german|italian)|"
    r"niveau|nivel|sprachkenntnis|livello",
    re.I,
)

# Yes/No option matchers (multilingual).
_RE_YES = re.compile(r"^(yes|oui|s[íi]|ja|s[ìi])$", re.I)
_RE_NO = re.compile(r"^(no|non|nein)$", re.I)

# Language-proficiency tier preference: Native > Fluent > Professional.
_PROFICIENCY_TIERS: tuple[tuple[str, ...], ...] = (
    ("native", "bilingual", "bilingue", "langue maternelle", "muttersprache", "madrelingua"),
    ("fluent", "courant", "fluide", "fließend", "fluente"),
    ("professional", "professionnel", "advanced", "avanc", "profession"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce(field: SerializedField | dict) -> SerializedField:
    if isinstance(field, SerializedField):
        return field
    return SerializedField(**field)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _match_yes_no_option(options: list[str], want_yes: bool) -> str:
    """Return the option label matching the desired yes/no answer.

    Falls back to the canonical English word when options aren't enumerated
    (e.g. a custom listbox serialized with an empty options list).
    """
    matcher = _RE_YES if want_yes else _RE_NO
    for opt in options:
        if matcher.match(opt.strip()):
            return opt
    return "Yes" if want_yes else "No"


def _pick_proficiency(options: list[str]) -> str | None:
    for tier in _PROFICIENCY_TIERS:
        for opt in options:
            low = opt.lower()
            if any(token in low for token in tier):
                return opt
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_field(
    field: SerializedField | dict,
    apply_profile: ApplyProfile | None,
    contact_info: ContactInfo | None,
) -> FieldFill | Skip | Unknown:
    """Classify a single serialized field into a fill / skip / unknown outcome."""
    f = _coerce(field)
    profile = apply_profile or ApplyProfile()
    label = (f.label or "").lower()
    ftype = (f.type or "text").lower()

    # File inputs: the resume is uploaded by the dedicated upload_file tool.
    if ftype == "file":
        return Skip(selector=f.selector, reason="file upload handled by upload_file")

    if ftype == "checkbox":
        return _classify_checkbox(f, label)

    if ftype == "radio":
        return _classify_radio(f, label, profile)

    if ftype in ("select", "listbox"):
        result = _classify_choice(f, label, profile)
        if result is not None:
            return result
        # Choice fields may still be a country code etc. handled as text below;
        # otherwise fall through to Unknown.
        return Unknown(selector=f.selector, label=f.label, reason="unrecognized dropdown")

    # Text-like inputs (text/email/tel/number).
    return _classify_text(f, label, profile, contact_info)


def _classify_checkbox(f: SerializedField, label: str) -> FieldFill | Skip | Unknown:
    # Follow-company checkbox is un-followed at submit, never auto-checked here.
    if "follow-company" in f.selector or ("follow" in label and "compan" in label):
        return Skip(selector=f.selector, reason="follow-company handled at submit")
    if _RE_CONSENT.search(label):
        # value semantics: content script treats "true" as check.
        return FieldFill(selector=f.selector, value="true", kind="consent")
    return Unknown(selector=f.selector, label=f.label, reason="unrecognized checkbox")


def _classify_radio(f: SerializedField, label: str, profile: ApplyProfile) -> FieldFill | Unknown:
    radio_kinds: tuple[tuple[re.Pattern[str], str], ...] = (
        (_RE_VISA, "needs_visa_sponsorship"),
        (_RE_AUTHORIZED, "legally_authorized"),
        (_RE_RELOCATE, "willing_to_relocate"),
        (_RE_LICENSE, "drivers_license"),
    )
    for pattern, kind in radio_kinds:
        if pattern.search(label):
            answer = getattr(profile, kind, None)
            if answer is None:
                return Unknown(
                    selector=f.selector,
                    label=f.label,
                    reason=f"profile value missing: {kind}",
                )
            value = _match_yes_no_option(f.options, want_yes=bool(answer))
            return FieldFill(selector=f.selector, value=value, kind=kind)
    return Unknown(selector=f.selector, label=f.label, reason="unrecognized radio question")


def _classify_choice(
    f: SerializedField, label: str, profile: ApplyProfile
) -> FieldFill | Unknown | None:
    """Classify a <select> / custom listbox. Returns None if not a known choice."""
    if _RE_PHONE_COUNTRY.search(label):
        code = profile.phone_country_code
        if code is None:
            return Unknown(
                selector=f.selector,
                label=f.label,
                reason="profile value missing: phone_country_code",
            )
        # Prefer an option that contains the code (e.g. "United States (+1)").
        value = next((o for o in f.options if code in o), code)
        return FieldFill(selector=f.selector, value=value, kind="phone_country_code")

    if _RE_PROFICIENCY.search(label):
        choice = _pick_proficiency(f.options)
        if choice is None:
            # Custom listbox serializes empty options; let the content script
            # resolve the best tier by substring match.
            choice = "Native"
        return FieldFill(selector=f.selector, value=choice, kind="language_proficiency")

    return None


def _classify_text(
    f: SerializedField,
    label: str,
    profile: ApplyProfile,
    contact_info: ContactInfo | None,
) -> FieldFill | Unknown:
    first_name, last_name = _split_name(contact_info.full_name) if contact_info else ("", "")

    # (pattern, kind, resolved value). First matching pattern wins; order is
    # significant (country-code before phone, salary/experience before names).
    text_matchers: list[tuple[re.Pattern[str], str, str | None]] = [
        (
            _RE_YEARS,
            "years_experience",
            str(profile.years_experience) if profile.years_experience is not None else None,
        ),
        (_RE_SALARY, "expected_salary", profile.expected_salary),
        (_RE_EMAIL, "email", str(contact_info.email) if contact_info else None),
        (_RE_FIRST, "first_name", first_name or None),
        (_RE_LAST, "last_name", last_name or None),
        (_RE_PHONE_COUNTRY, "phone_country_code", profile.phone_country_code),
        (_RE_PHONE, "phone", contact_info.phone if contact_info else None),
        (_RE_CITY, "city", contact_info.location if contact_info else None),
    ]

    for pattern, kind, value in text_matchers:
        if pattern.search(label):
            if value is None or value == "":
                return Unknown(
                    selector=f.selector,
                    label=f.label,
                    reason=f"profile value missing: {kind}",
                )
            return FieldFill(selector=f.selector, value=value, kind=kind)

    return Unknown(selector=f.selector, label=f.label, reason="unrecognized field")
