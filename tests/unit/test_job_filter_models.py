"""Tests for job filter Pydantic models and FILTERED_OUT state transitions."""

import pytest

from src.models.job_filter import FilterResult, UserFilterPreferences
from src.models.state_machine import (
    ALLOWED_TRANSITIONS,
    BusinessState,
    InvalidStateTransitionError,
    validate_transition,
)


class TestFilterResult:
    """Test FilterResult model validation."""

    def test_valid_result(self):
        result = FilterResult(
            score=75,
            red_flags=["Requires on-site 3 days/week"],
            disqualified=False,
            disqualifier_reason=None,
            reasoning="Job is mostly suitable but has hybrid requirement.",
        )
        assert result.score == 75
        assert len(result.red_flags) == 1
        assert result.disqualified is False
        assert result.disqualifier_reason is None

    def test_disqualified_result(self):
        result = FilterResult(
            score=10,
            red_flags=["Requires TS/SCI clearance"],
            disqualified=True,
            disqualifier_reason="Security clearance required",
            reasoning="Hard disqualifier: requires active TS/SCI clearance.",
        )
        assert result.disqualified is True
        assert result.disqualifier_reason == "Security clearance required"
        assert result.score == 10

    def test_clean_pass(self):
        result = FilterResult(
            score=90,
            red_flags=[],
            disqualified=False,
            reasoning="Excellent match for the candidate's profile.",
        )
        assert result.score == 90
        assert result.red_flags == []

    def test_score_min_boundary(self):
        result = FilterResult(score=0, reasoning="Completely unsuitable.")
        assert result.score == 0

    def test_score_max_boundary(self):
        result = FilterResult(score=100, reasoning="Perfect match.")
        assert result.score == 100

    def test_score_below_min_rejected(self):
        with pytest.raises(ValueError):
            FilterResult(score=-1, reasoning="Invalid.")

    def test_score_above_max_rejected(self):
        with pytest.raises(ValueError):
            FilterResult(score=101, reasoning="Invalid.")

    def test_defaults(self):
        result = FilterResult(score=50, reasoning="Middling.")
        assert result.red_flags == []
        assert result.disqualified is False
        assert result.disqualifier_reason is None

    def test_serialization_roundtrip(self):
        result = FilterResult(
            score=65,
            red_flags=["Flag 1", "Flag 2"],
            disqualified=False,
            reasoning="Some reasoning.",
        )
        data = result.model_dump()
        restored = FilterResult(**data)
        assert restored == result

    def test_json_roundtrip(self):
        result = FilterResult(
            score=20,
            red_flags=["Clearance required"],
            disqualified=True,
            disqualifier_reason="Needs clearance",
            reasoning="Hard no.",
        )
        json_str = result.model_dump_json()
        restored = FilterResult.model_validate_json(json_str)
        assert restored == result


class TestUserFilterPreferences:
    """Test UserFilterPreferences model validation."""

    def test_defaults(self):
        prefs = UserFilterPreferences()
        assert prefs.natural_language_prefs == ""
        assert prefs.custom_prompt is None
        assert prefs.reject_threshold == 30
        assert prefs.warning_threshold == 70
        assert prefs.enabled is True

    def test_full_preferences(self):
        prefs = UserFilterPreferences(
            natural_language_prefs="No clearance jobs, remote only",
            custom_prompt="Check for hidden requirements...",
            reject_threshold=25,
            warning_threshold=60,
            enabled=True,
        )
        assert prefs.natural_language_prefs == "No clearance jobs, remote only"
        assert prefs.custom_prompt == "Check for hidden requirements..."
        assert prefs.reject_threshold == 25
        assert prefs.warning_threshold == 60

    def test_disabled_filter(self):
        prefs = UserFilterPreferences(enabled=False)
        assert prefs.enabled is False

    def test_warning_must_be_gte_reject(self):
        with pytest.raises(ValueError, match="warning_threshold.*reject_threshold"):
            UserFilterPreferences(reject_threshold=50, warning_threshold=30)

    def test_warning_equal_to_reject_is_valid(self):
        prefs = UserFilterPreferences(reject_threshold=50, warning_threshold=50)
        assert prefs.reject_threshold == 50
        assert prefs.warning_threshold == 50

    def test_threshold_boundaries(self):
        prefs = UserFilterPreferences(reject_threshold=0, warning_threshold=0)
        assert prefs.reject_threshold == 0
        assert prefs.warning_threshold == 0

        prefs = UserFilterPreferences(reject_threshold=100, warning_threshold=100)
        assert prefs.reject_threshold == 100
        assert prefs.warning_threshold == 100

    def test_threshold_below_zero_rejected(self):
        with pytest.raises(ValueError):
            UserFilterPreferences(reject_threshold=-1)

    def test_threshold_above_100_rejected(self):
        with pytest.raises(ValueError):
            UserFilterPreferences(warning_threshold=101)

    def test_serialization_roundtrip(self):
        prefs = UserFilterPreferences(
            natural_language_prefs="Remote only",
            custom_prompt="My prompt",
            reject_threshold=20,
            warning_threshold=80,
            enabled=True,
        )
        data = prefs.model_dump()
        restored = UserFilterPreferences(**data)
        assert restored == prefs

    def test_json_roundtrip(self):
        prefs = UserFilterPreferences(
            natural_language_prefs="No junior roles",
            reject_threshold=40,
            warning_threshold=75,
        )
        json_str = prefs.model_dump_json()
        restored = UserFilterPreferences.model_validate_json(json_str)
        assert restored == prefs


class TestFilteredOutState:
    """Test FILTERED_OUT state in BusinessState enum and transitions."""

    def test_enum_value(self):
        assert BusinessState.FILTERED_OUT == "filtered_out"

    def test_enum_from_string(self):
        assert BusinessState("filtered_out") == BusinessState.FILTERED_OUT

    def test_filtered_out_is_terminal(self):
        assert ALLOWED_TRANSITIONS[BusinessState.FILTERED_OUT] == set()

    def test_queued_to_filtered_out(self):
        assert validate_transition(BusinessState.QUEUED, BusinessState.FILTERED_OUT) is True

    def test_processing_to_filtered_out(self):
        assert validate_transition(BusinessState.PROCESSING, BusinessState.FILTERED_OUT) is True

    def test_filtered_out_self_transition(self):
        assert validate_transition(BusinessState.FILTERED_OUT, BusinessState.FILTERED_OUT) is True

    def test_filtered_out_to_other_states_invalid(self):
        for target in BusinessState:
            if target == BusinessState.FILTERED_OUT:
                continue
            with pytest.raises(InvalidStateTransitionError):
                validate_transition(BusinessState.FILTERED_OUT, target)

    def test_filtered_out_not_reachable_from_terminal_states(self):
        terminal_states = [
            BusinessState.CV_READY,
            BusinessState.DECLINED,
            BusinessState.APPLIED,
        ]
        for state in terminal_states:
            with pytest.raises(InvalidStateTransitionError):
                validate_transition(state, BusinessState.FILTERED_OUT)

    def test_filtered_out_in_allowed_transitions(self):
        assert BusinessState.FILTERED_OUT in ALLOWED_TRANSITIONS
