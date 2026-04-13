"""Tests for JobFilter service."""

import json
from pathlib import Path

import pytest

from src.llm.provider import BaseLLMClient
from src.models.job_filter import FilterResult, UserFilterPreferences
from src.services.jobs.job_filter import JobFilter, JobFilterError

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockLLMClient(BaseLLMClient):
    """Mock LLM client that returns pre-configured responses."""

    def __init__(self):
        super().__init__(api_key="test-key", model="test-model")
        self._generate_json_response: dict | None = None
        self._generate_response: str | None = None
        self.generate_json_calls: list[dict] = []
        self.generate_calls: list[dict] = []

    def set_generate_json_response(self, response: dict):
        self._generate_json_response = response

    def set_generate_response(self, response: str):
        self._generate_response = response

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        self.generate_calls.append({"prompt": prompt, "temperature": temperature})
        if self._generate_response is not None:
            return self._generate_response
        return "Mock response"

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        self.generate_json_calls.append(
            {"prompt": prompt, "schema": schema, "temperature": temperature}
        )
        if self._generate_json_response is not None:
            return self._generate_json_response
        return {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm():
    return MockLLMClient()


@pytest.fixture
def job_posting():
    with open(FIXTURES_DIR / "job_posting.json") as f:
        return json.load(f)


@pytest.fixture
def job_filter(mock_llm):
    return JobFilter(llm_client=mock_llm, prompts_dir="prompts/job_filter")


@pytest.fixture
def good_filter_result_dict():
    """A clean-pass result with high score."""
    return {
        "score": 85,
        "red_flags": [],
        "disqualified": False,
        "disqualifier_reason": None,
        "reasoning": "Good match for a senior Python engineer.",
    }


@pytest.fixture
def warning_filter_result_dict():
    """A warning-level result."""
    return {
        "score": 55,
        "red_flags": ["Requires 10+ years experience", "Vague role description"],
        "disqualified": False,
        "disqualifier_reason": None,
        "reasoning": "Some concerns about experience requirements.",
    }


@pytest.fixture
def reject_filter_result_dict():
    """A result that should be rejected (low score)."""
    return {
        "score": 20,
        "red_flags": ["Requires TS/SCI clearance", "Not actually remote"],
        "disqualified": False,
        "disqualifier_reason": None,
        "reasoning": "Multiple hard issues found.",
    }


@pytest.fixture
def disqualified_filter_result_dict():
    """A result with a hard disqualifier."""
    return {
        "score": 10,
        "red_flags": ["Requires US citizenship"],
        "disqualified": True,
        "disqualifier_reason": "Job requires US citizenship for security clearance.",
        "reasoning": "Hard disqualifier: requires US citizenship.",
    }


# ---------------------------------------------------------------------------
# evaluate_job tests
# ---------------------------------------------------------------------------


class TestEvaluateJob:
    def test_evaluate_job_returns_filter_result(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        result = job_filter.evaluate_job(job_posting)

        assert isinstance(result, FilterResult)
        assert result.score == 85
        assert result.red_flags == []
        assert result.disqualified is False
        assert result.disqualifier_reason is None

    def test_evaluate_job_with_red_flags(
        self, job_filter, mock_llm, job_posting, warning_filter_result_dict
    ):
        mock_llm.set_generate_json_response(warning_filter_result_dict)
        result = job_filter.evaluate_job(job_posting)

        assert result.score == 55
        assert len(result.red_flags) == 2
        assert "Requires 10+ years experience" in result.red_flags

    def test_evaluate_job_disqualified(
        self, job_filter, mock_llm, job_posting, disqualified_filter_result_dict
    ):
        mock_llm.set_generate_json_response(disqualified_filter_result_dict)
        result = job_filter.evaluate_job(job_posting)

        assert result.disqualified is True
        assert result.disqualifier_reason is not None
        assert "citizenship" in result.disqualifier_reason.lower()

    def test_evaluate_job_uses_default_prompt_without_prefs(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        job_filter.evaluate_job(job_posting)

        assert len(mock_llm.generate_json_calls) == 1
        prompt = mock_llm.generate_json_calls[0]["prompt"]
        # Default prompt should contain the job title
        assert "Senior Software Engineer" in prompt
        assert "Acme Corp" in prompt

    def test_evaluate_job_uses_default_prompt_with_natural_language_prefs(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        prefs = UserFilterPreferences(
            natural_language_prefs="I want remote Python jobs only",
        )
        job_filter.evaluate_job(job_posting, user_filter_prefs=prefs)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert "I want remote Python jobs only" in prompt

    def test_evaluate_job_uses_custom_prompt(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        prefs = UserFilterPreferences(
            custom_prompt="Check if this job is a good fit for a Python dev.",
        )
        job_filter.evaluate_job(job_posting, user_filter_prefs=prefs)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert "Check if this job is a good fit for a Python dev." in prompt
        # Custom prompt should still include job data
        assert "Senior Software Engineer" in prompt

    def test_evaluate_job_passes_schema(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        job_filter.evaluate_job(job_posting)

        schema = mock_llm.generate_json_calls[0]["schema"]
        assert schema is not None
        assert "properties" in schema
        assert "score" in schema["properties"]

    def test_evaluate_job_uses_low_temperature(
        self, job_filter, mock_llm, job_posting, good_filter_result_dict
    ):
        mock_llm.set_generate_json_response(good_filter_result_dict)
        job_filter.evaluate_job(job_posting)

        temp = mock_llm.generate_json_calls[0]["temperature"]
        assert temp == 0.2

    def test_evaluate_job_raises_on_llm_failure(self, job_filter, mock_llm, job_posting):
        mock_llm.set_generate_json_response(None)

        def raise_error(*args, **kwargs):
            raise RuntimeError("API timeout")

        mock_llm.generate_json = raise_error

        with pytest.raises(JobFilterError, match="Job evaluation failed"):
            job_filter.evaluate_job(job_posting)

    def test_evaluate_job_raises_on_invalid_result(
        self, job_filter, mock_llm, job_posting
    ):
        # Missing required fields
        mock_llm.set_generate_json_response({"score": 50})

        with pytest.raises(JobFilterError, match="Invalid filter result"):
            job_filter.evaluate_job(job_posting)


# ---------------------------------------------------------------------------
# should_reject / should_warn tests
# ---------------------------------------------------------------------------


class TestThresholdDecisions:
    def test_should_reject_below_threshold(self, job_filter):
        result = FilterResult(
            score=20, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="Low score."
        )
        assert job_filter.should_reject(result, reject_threshold=30) is True

    def test_should_reject_at_threshold(self, job_filter):
        result = FilterResult(
            score=30, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="At threshold."
        )
        assert job_filter.should_reject(result, reject_threshold=30) is False

    def test_should_reject_above_threshold(self, job_filter):
        result = FilterResult(
            score=85, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="Good match."
        )
        assert job_filter.should_reject(result, reject_threshold=30) is False

    def test_should_reject_when_disqualified_regardless_of_score(self, job_filter):
        result = FilterResult(
            score=95, red_flags=["Requires clearance"],
            disqualified=True,
            disqualifier_reason="Needs TS/SCI clearance.",
            reasoning="High score but disqualified."
        )
        assert job_filter.should_reject(result, reject_threshold=30) is True

    def test_should_reject_custom_threshold(self, job_filter):
        result = FilterResult(
            score=45, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="Mid score."
        )
        assert job_filter.should_reject(result, reject_threshold=50) is True
        assert job_filter.should_reject(result, reject_threshold=40) is False

    def test_should_warn_below_warning_threshold(self, job_filter):
        result = FilterResult(
            score=55, red_flags=["Concern"], disqualified=False,
            disqualifier_reason=None, reasoning="Some concerns."
        )
        assert job_filter.should_warn(result, warning_threshold=70) is True

    def test_should_warn_at_warning_threshold(self, job_filter):
        result = FilterResult(
            score=70, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="At threshold."
        )
        assert job_filter.should_warn(result, warning_threshold=70) is False

    def test_should_warn_above_warning_threshold(self, job_filter):
        result = FilterResult(
            score=85, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="Good match."
        )
        assert job_filter.should_warn(result, warning_threshold=70) is False

    def test_should_warn_custom_threshold(self, job_filter):
        result = FilterResult(
            score=55, red_flags=[], disqualified=False,
            disqualifier_reason=None, reasoning="Mid score."
        )
        assert job_filter.should_warn(result, warning_threshold=50) is False
        assert job_filter.should_warn(result, warning_threshold=60) is True


# ---------------------------------------------------------------------------
# generate_prompt_from_preferences tests
# ---------------------------------------------------------------------------


class TestGeneratePromptFromPreferences:
    def test_generate_prompt_returns_string(self, job_filter, mock_llm):
        mock_llm.set_generate_response(
            "Evaluate this job posting for the following criteria:\n"
            "1. Must be remote\n2. Must use Python"
        )
        result = job_filter.generate_prompt_from_preferences(
            "I want remote Python jobs"
        )

        assert isinstance(result, str)
        assert "Evaluate this job posting" in result

    def test_generate_prompt_passes_prefs_to_llm(self, job_filter, mock_llm):
        mock_llm.set_generate_response("Generated prompt")
        job_filter.generate_prompt_from_preferences(
            "No security clearance, remote only"
        )

        assert len(mock_llm.generate_calls) == 1
        prompt = mock_llm.generate_calls[0]["prompt"]
        assert "No security clearance, remote only" in prompt

    def test_generate_prompt_strips_whitespace(self, job_filter, mock_llm):
        mock_llm.set_generate_response("  Generated prompt with whitespace  \n\n")
        result = job_filter.generate_prompt_from_preferences("test")

        assert result == "Generated prompt with whitespace"

    def test_generate_prompt_raises_on_llm_failure(self, job_filter, mock_llm):
        def raise_error(*args, **kwargs):
            raise RuntimeError("API error")

        mock_llm.generate = raise_error

        with pytest.raises(JobFilterError, match="Prompt generation failed"):
            job_filter.generate_prompt_from_preferences("test")


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    def test_default_prompt_includes_job_fields(self, job_filter, mock_llm, job_posting):
        mock_llm.set_generate_json_response({
            "score": 80, "red_flags": [], "disqualified": False,
            "disqualifier_reason": None, "reasoning": "Good."
        })
        job_filter.evaluate_job(job_posting)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert job_posting["title"] in prompt
        assert job_posting["company"] in prompt
        assert job_posting["location"] in prompt
        assert job_posting["description"] in prompt

    def test_custom_prompt_includes_job_data_and_custom_text(
        self, job_filter, mock_llm, job_posting
    ):
        mock_llm.set_generate_json_response({
            "score": 80, "red_flags": [], "disqualified": False,
            "disqualifier_reason": None, "reasoning": "Good."
        })
        prefs = UserFilterPreferences(
            custom_prompt="MY CUSTOM EVALUATION INSTRUCTIONS HERE",
        )
        job_filter.evaluate_job(job_posting, user_filter_prefs=prefs)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert "MY CUSTOM EVALUATION INSTRUCTIONS HERE" in prompt
        assert job_posting["title"] in prompt

    def test_default_prompt_without_prefs_has_no_user_section(
        self, job_filter, mock_llm, job_posting
    ):
        mock_llm.set_generate_json_response({
            "score": 80, "red_flags": [], "disqualified": False,
            "disqualifier_reason": None, "reasoning": "Good."
        })
        job_filter.evaluate_job(job_posting, user_filter_prefs=None)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert "User Preferences" not in prompt

    def test_default_prompt_with_empty_prefs_has_no_user_section(
        self, job_filter, mock_llm, job_posting
    ):
        mock_llm.set_generate_json_response({
            "score": 80, "red_flags": [], "disqualified": False,
            "disqualifier_reason": None, "reasoning": "Good."
        })
        prefs = UserFilterPreferences(natural_language_prefs="")
        job_filter.evaluate_job(job_posting, user_filter_prefs=prefs)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        assert "User Preferences" not in prompt

    def test_custom_prompt_overrides_default_template(
        self, job_filter, mock_llm, job_posting
    ):
        """When custom_prompt is set, the default template text should NOT appear."""
        mock_llm.set_generate_json_response({
            "score": 80, "red_flags": [], "disqualified": False,
            "disqualifier_reason": None, "reasoning": "Good."
        })
        prefs = UserFilterPreferences(
            custom_prompt="Only check if it's a Python role.",
            natural_language_prefs="I also have prefs here",
        )
        job_filter.evaluate_job(job_posting, user_filter_prefs=prefs)

        prompt = mock_llm.generate_json_calls[0]["prompt"]
        # Custom prompt should be used
        assert "Only check if it's a Python role." in prompt
        # Default template markers should NOT be present
        assert "FAKE REMOTE" not in prompt
