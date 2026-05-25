"""CSS selectors for LinkedIn job search results and detail pages.

Two layouts coexist on LinkedIn /jobs/view/ and /jobs/search:

- the authenticated SPA (``job-card-container`` / SDUI ``data-sdui-component``)
- the public guest JSERP (``job-search-card`` / ``base-search-card`` family)

Direct deep-links to ``/jobs/search/?...`` often render the guest layout even
with a valid session cookie, so each selector covers both.
"""

from __future__ import annotations

SELECTORS: dict[str, str] = {
    "job_card": "div.job-card-container, div.job-search-card",
    "job_card_title": (
        "a.job-card-container__link, a.job-card-list__title--link, "
        "a.base-card__full-link, h3.base-search-card__title"
    ),
    "job_card_company": (
        "div.artdeco-entity-lockup__subtitle span, "
        "span.job-card-container__primary-description, "
        "h4.base-search-card__subtitle a, h4.base-search-card__subtitle"
    ),
    "job_card_location": (
        "div.artdeco-entity-lockup__caption li span, "
        "li.job-card-container__metadata-item, "
        "span.job-search-card__location"
    ),
    "job_card_easy_apply": (
        "li-icon[type='linkedin-bug'], span.job-card-container__apply-method"
    ),
    "job_card_posted": (
        "time, span.job-card-container__listed-time, time.job-search-card__listed-time"
    ),
    # Authenticated SDUI layout (current as of 2026-05): hashed CSS classes are
    # rotated, so we anchor on the stable `data-sdui-component` attribute.
    # Note: we target the section container, not the inner `[data-testid=
    # "expandable-text-box"]` — that span ends up empty in the parsed DOM
    # because LinkedIn's markup nests `<p>` inside `<span>` inside `<p>`, which
    # browsers auto-correct by hoisting the inner content out. The container's
    # innerText includes the "About the job" heading; strip it in code.
    "detail_description": (
        "[data-sdui-component$='aboutTheJob'], "
        "div.jobs-description__content, div#job-details, "
        "div.show-more-less-html__markup, div.description__text"
    ),
    "detail_criteria": (
        "li.jobs-unified-top-card__job-insight, ul.job-criteria__list li, "
        "ul.description__job-criteria-list li"
    ),
    "detail_salary": "div.salary-main-rail__data-body, span.jobs-unified-top-card__salary",
    "detail_show_more": (
        "[data-sdui-component$='aboutTheJob'] [data-testid='expandable-text-button'], "
        "button.jobs-description__footer-button, button[aria-label='Show more'], "
        "button.show-more-less-html__button--more, "
        "button:has-text('Show more')"
    ),
    "no_results": "div.jobs-search-no-results-banner",
}


# Authenticated (SDUI / legacy SPA) description container selectors,
# tried in priority order.
AUTHENTICATED_DESCRIPTION_SELECTORS: tuple[str, ...] = (
    "[data-sdui-component$='aboutTheJob']",
    "div.jobs-description__content",
    "div#job-details",
)

# Guest (JSERP) description selector. Only the inner markup div — never
# the outer `div.description__text` wrapper, which contains the
# "Show more"/"Show less" toggle buttons as siblings of the markup.
GUEST_DESCRIPTION_SELECTOR: str = "div.show-more-less-html__markup"

# Markers used to detect which layout LinkedIn served. Authenticated
# markers are checked first; absence implies guest. (We don't trust
# `li_at` cookie presence alone — sessions can be silently downgraded.)
AUTHENTICATED_LAYOUT_MARKERS: tuple[str, ...] = (
    "[data-sdui-component$='aboutTheJob']",
    "div.jobs-description__content",
    "div#job-details",
    "h2:has-text('About the job')",
)
