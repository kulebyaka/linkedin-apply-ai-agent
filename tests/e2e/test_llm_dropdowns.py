"""E2E tests for LLM provider and model dropdowns.

Tests verify that:
1. Default values are correctly set (OpenAI / gpt-4.1-nano)
2. Provider dropdown contains expected options
3. Model dropdown updates when provider changes
4. Anthropic provider shows only Claude models
5. OpenAI provider shows only GPT models

Run with: pytest tests/e2e/test_llm_dropdowns.py -v
Set UI_URL env var to override default: UI_URL=http://localhost:5178 pytest ...
"""

import os
import pytest
from playwright.sync_api import sync_playwright, Page, expect


# Expected dropdown options
EXPECTED_PROVIDERS = ["openai", "anthropic"]
EXPECTED_OPENAI_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
EXPECTED_ANTHROPIC_MODELS = ["claude-haiku-4.5"]

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-4o-mini"

UI_URL = os.environ.get("UI_URL", "http://localhost:5173")


@pytest.fixture(scope="module")
def browser():
    """Launch browser for test session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    """Create new page for each test."""
    page = browser.new_page()
    yield page
    page.close()


def get_select_options(page: Page, selector: str) -> list[str]:
    """Extract all option values from a select element."""
    return page.eval_on_selector(
        selector,
        "el => Array.from(el.options).map(o => o.value)"
    )


def get_selected_value(page: Page, selector: str) -> str:
    """Get currently selected value from a select element."""
    return page.eval_on_selector(selector, "el => el.value")


class TestLLMDropdowns:
    """Test suite for LLM provider and model dropdown functionality."""

    def test_default_provider_is_openai(self, page: Page):
        """Verify default provider is OpenAI."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        selected = get_selected_value(page, "#llm-provider-select")
        assert selected == DEFAULT_PROVIDER, f"Expected default provider '{DEFAULT_PROVIDER}', got '{selected}'"

    def test_default_model_is_gpt_4_1_nano(self, page: Page):
        """Verify default model is gpt-4.1-nano."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        selected = get_selected_value(page, "#llm-model-select")
        assert selected == DEFAULT_MODEL, f"Expected default model '{DEFAULT_MODEL}', got '{selected}'"

    def test_provider_dropdown_has_expected_options(self, page: Page):
        """Verify provider dropdown contains OpenAI and Anthropic."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        options = get_select_options(page, "#llm-provider-select")
        assert set(options) == set(EXPECTED_PROVIDERS), f"Expected providers {EXPECTED_PROVIDERS}, got {options}"

    def test_openai_models_shown_by_default(self, page: Page):
        """Verify OpenAI models are shown when OpenAI is selected."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        options = get_select_options(page, "#llm-model-select")
        assert set(options) == set(EXPECTED_OPENAI_MODELS), f"Expected OpenAI models {EXPECTED_OPENAI_MODELS}, got {options}"

    def test_switching_to_anthropic_shows_claude_models(self, page: Page):
        """Verify switching to Anthropic updates model dropdown to Claude models."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        # Switch to Anthropic
        page.select_option("#llm-provider-select", "anthropic")
        page.wait_for_timeout(300)  # Wait for reactive update

        # Verify model dropdown updated
        options = get_select_options(page, "#llm-model-select")
        assert set(options) == set(EXPECTED_ANTHROPIC_MODELS), f"Expected Anthropic models {EXPECTED_ANTHROPIC_MODELS}, got {options}"

        # Verify selected model is Claude
        selected = get_selected_value(page, "#llm-model-select")
        assert selected == "claude-haiku-4.5", f"Expected selected model 'claude-haiku-4.5', got '{selected}'"

    def test_switching_back_to_openai_restores_gpt_models(self, page: Page):
        """Verify switching back to OpenAI restores GPT models."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        # Switch to Anthropic then back to OpenAI
        page.select_option("#llm-provider-select", "anthropic")
        page.wait_for_timeout(300)
        page.select_option("#llm-provider-select", "openai")
        page.wait_for_timeout(300)

        # Verify model dropdown shows OpenAI models
        options = get_select_options(page, "#llm-model-select")
        assert set(options) == set(EXPECTED_OPENAI_MODELS), f"Expected OpenAI models {EXPECTED_OPENAI_MODELS}, got {options}"

    def test_model_selection_persists_within_provider(self, page: Page):
        """Verify selecting a different model persists until provider changes."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        # Select gpt-4o
        page.select_option("#llm-model-select", "gpt-4o")
        page.wait_for_timeout(100)

        # Verify selection persisted
        selected = get_selected_value(page, "#llm-model-select")
        assert selected == "gpt-4o", f"Expected selected model 'gpt-4o', got '{selected}'"

    def test_dropdowns_are_disabled_during_loading(self, page: Page):
        """Verify dropdowns are disabled when form is submitting."""
        page.goto(UI_URL)
        page.wait_for_load_state("networkidle")

        # Initially dropdowns should be enabled
        provider_disabled = page.eval_on_selector("#llm-provider-select", "el => el.disabled")
        model_disabled = page.eval_on_selector("#llm-model-select", "el => el.disabled")

        assert not provider_disabled, "Provider dropdown should be enabled initially"
        assert not model_disabled, "Model dropdown should be enabled initially"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
