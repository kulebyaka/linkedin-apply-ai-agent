"""CSS selectors and text patterns for the LinkedIn Easy Apply flow.

Ported from the proven AutoApplyMax extension
(``experiments/browser-extensions/AutoApplyMax/content-simple.js``) — logic and
selectors only, not files. The content script (``extension/content_script.js``)
holds the DOM *mechanics*; this module is the server-side source of truth for the
selectors/patterns the bridge tools (``src/services/linkedin/apply_bridge.py``)
and the deterministic apply workflow reason about.

As with ``selectors.py`` two LinkedIn layouts coexist (authenticated SPA + the
occasional legacy/guest markup); selectors below cover both where they differ.
"""

from __future__ import annotations

# Core Easy Apply DOM anchors (AutoApplyMax :320, :551, :647, :1131, :1363, :803).
EASY_APPLY_SELECTORS: dict[str, str] = {
    # The modal that hosts the multi-step Easy Apply form.
    "modal": ".jobs-easy-apply-modal, [role='dialog'].artdeco-modal",
    # Job result cards in the search/collections list.
    "job_card": (
        "li[data-occludable-job-id], "
        ".jobs-search-results__list-item, .scaffold-layout__list-item"
    ),
    # The "Easy Apply" launch button (must contain "Easy" to avoid external Apply).
    "easy_apply_button": (
        "button.jobs-apply-button[aria-label*='Easy'], " "button[aria-label*='Easy Apply']"
    ),
    # Step navigation controls inside the modal (matched by text in code).
    "footer_buttons": ".jobs-easy-apply-modal button, [role='dialog'] button",
    # Radio question fieldsets (LinkedIn form-builder component).
    "radio_fieldset": "fieldset[data-test-form-builder-radio-button-form-component], fieldset",
    # The "follow company" checkbox — un-followed before Submit, never auto-checked.
    "follow_company_checkbox": (
        "input#follow-company-checkbox, input[id*='follow-company'][type='checkbox']"
    ),
    # Inline validation / error surfaces scanned after advancing a step.
    "error": "[role='alert'], .artdeco-inline-feedback--error, .fb-form-element-label__error",
    # Loading spinner / progress indicator (slow-screen detection).
    "spinner": ".artdeco-loader, [role='progressbar'], .loading-spinner, .spinner",
    # Toast / inline-feedback containers used for daily-limit detection.
    "feedback_container": (
        ".artdeco-inline-feedback, .artdeco-toast-item, .artdeco-modal__content"
    ),
}


# Messages LinkedIn shows when the daily Easy Apply quota is hit
# (AutoApplyMax :63-75). Matched case-insensitively against page/feedback text.
DAILY_LIMIT_PATTERNS: tuple[str, ...] = (
    "You've reached today's Easy Apply limit",
    "You've reached today's easy apply limit",
    "reached today's Easy Apply limit",
    "Great effort applying today",
    "we limit daily submissions",
    "continue applying tomorrow",
    "Save this job and continue applying tomorrow",
    "exceeded the daily application limit",
    "reached today's easy apply limit",
    "daily Easy Apply limit",
    "limit daily submissions",
)


# Text labels for the final confirmation / Done button (AutoApplyMax :129).
DONE_TEXTS: tuple[str, ...] = (
    "Done",
    "Terminé",
    "Submit application",
    "Soumettre la candidature",
    "Dismiss",
    "Close",
    "Fermer",
)

# LinkedIn ``data-control-name`` values for the Done/Submit control
# (AutoApplyMax :197).
DONE_CONTROL_NAMES: tuple[str, ...] = (
    "done",
    "submit",
    "continue_application",
)


# Button text used to advance a step (Next / Review / Submit), matched
# case-insensitively (AutoApplyMax :1363-1367). The Submit variants double as
# the "form complete" signal.
NEXT_BUTTON_TEXTS: tuple[str, ...] = (
    "next",
    "suivant",
    "review",
    "revoir",
)

SUBMIT_BUTTON_TEXTS: tuple[str, ...] = (
    "submit",
    "soumettre",
)

# Safety-reminder modal copy ("Continue applying") LinkedIn occasionally injects
# between the Easy Apply click and the form (AutoApplyMax :668-687).
SAFETY_REMINDER_PATTERNS: tuple[str, ...] = (
    "safety reminder",
    "rappel de sécurité",
    "continue applying",
    "continuer à postuler",
)
