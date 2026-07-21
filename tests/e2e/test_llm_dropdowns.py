"""E2E tests for the shared LLM provider + model selector on /generate.

The selector (`ModelSelector.svelte`) is catalog-driven: the provider list comes
from `/api/llm/models`, filtered server-side to providers that have an API key
configured. The e2e API server (see `_test_api_server.py`) configures dummy
OpenAI + Anthropic keys and uses the static catalog, so the expected options are
derived from the API response rather than hardcoded here.

Run with: pytest tests/e2e/test_llm_dropdowns.py -v -m e2e
"""

import httpx
import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

PROVIDER_SELECT = "#llm-provider-select"
MODEL_SELECT = "#llm-model-select"


def _options(page: Page, selector: str) -> list[str]:
    """All option values of a <select>."""
    return page.eval_on_selector(selector, "el => Array.from(el.options).map(o => o.value)")


def _selected(page: Page, selector: str) -> str:
    """Currently selected value of a <select>."""
    return page.eval_on_selector(selector, "el => el.value")


def _catalog(api_url: str) -> dict:
    """Fetch the cv_generation catalog the /generate form uses."""
    resp = httpx.get(
        f"{api_url}/api/llm/models",
        params={"operation": "cv_generation"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _goto_form(page: Page, ui_url: str) -> None:
    """Open the CV generator and wait for the provider dropdown to render."""
    page.goto(f"{ui_url}/generate")
    page.wait_for_selector(PROVIDER_SELECT, timeout=15000)
    page.wait_for_load_state("networkidle")


class TestLLMDropdowns:
    """The provider/model selector is decoupled into two catalog-driven dropdowns."""

    def test_providers_match_configured_keys(
        self, ui_dev_server: str, mock_llm_and_api_server: str, page: Page
    ):
        """Provider dropdown shows exactly the providers with a configured key."""
        _goto_form(page, ui_dev_server)

        catalog = _catalog(mock_llm_and_api_server)
        expected = sorted({m["provider"] for m in catalog["models"]})

        assert sorted(_options(page, PROVIDER_SELECT)) == expected
        # The e2e server configures OpenAI + Anthropic keys only.
        assert expected == ["anthropic", "openai"]

    def test_default_selection_matches_api_default(
        self, ui_dev_server: str, mock_llm_and_api_server: str, page: Page
    ):
        """Initial provider/model match the catalog's server-provided default."""
        _goto_form(page, ui_dev_server)

        default = _catalog(mock_llm_and_api_server)["default"]
        assert _selected(page, PROVIDER_SELECT) == default["provider"]
        assert _selected(page, MODEL_SELECT) == default["model"]

    def test_model_options_belong_to_selected_provider(
        self, ui_dev_server: str, mock_llm_and_api_server: str, page: Page
    ):
        """Switching provider updates the model list to that provider's models."""
        _goto_form(page, ui_dev_server)

        catalog = _catalog(mock_llm_and_api_server)
        by_provider: dict[str, list[str]] = {}
        for m in catalog["models"]:
            by_provider.setdefault(m["provider"], []).append(m["model"])

        for provider, models in by_provider.items():
            page.select_option(PROVIDER_SELECT, provider)
            page.wait_for_timeout(300)  # reactive model reset

            options = _options(page, MODEL_SELECT)
            assert set(options) == set(models), (
                f"Provider {provider}: expected {models}, got {options}"
            )
            # The auto-selected model belongs to the chosen provider.
            assert _selected(page, MODEL_SELECT) in models

    def test_model_selection_persists_within_provider(
        self, ui_dev_server: str, mock_llm_and_api_server: str, page: Page
    ):
        """Picking a non-default model of the same provider sticks."""
        _goto_form(page, ui_dev_server)

        catalog = _catalog(mock_llm_and_api_server)
        provider = _selected(page, PROVIDER_SELECT)
        models = [m["model"] for m in catalog["models"] if m["provider"] == provider]
        assert len(models) >= 2, "Need >=2 models for this provider to test persistence"

        target = next(m for m in models if m != _selected(page, MODEL_SELECT))
        page.select_option(MODEL_SELECT, target)
        page.wait_for_timeout(100)

        assert _selected(page, MODEL_SELECT) == target

    def test_dropdowns_enabled_initially(self, ui_dev_server: str, page: Page):
        """Both dropdowns are interactive before submission."""
        _goto_form(page, ui_dev_server)

        assert not page.eval_on_selector(PROVIDER_SELECT, "el => el.disabled")
        assert not page.eval_on_selector(MODEL_SELECT, "el => el.disabled")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
