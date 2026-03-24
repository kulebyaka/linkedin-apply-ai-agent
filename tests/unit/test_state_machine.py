"""Tests for job lifecycle state machine.

Tests all valid transitions succeed, invalid transitions raise InvalidStateTransition,
and terminal states have no successors (except failed -> retrying).
"""

import pytest

from src.models.state_machine import (
    ALLOWED_TRANSITIONS,
    BusinessState,
    InvalidStateTransition,
    WorkflowStep,
    validate_transition,
)


class TestBusinessStateEnum:
    """Test BusinessState enum values and string compatibility."""

    def test_enum_values_are_strings(self):
        assert BusinessState.QUEUED == "queued"
        assert BusinessState.PROCESSING == "processing"
        assert BusinessState.CV_READY == "completed"
        assert BusinessState.PENDING_REVIEW == "pending"
        assert BusinessState.APPROVED == "approved"
        assert BusinessState.DECLINED == "declined"
        assert BusinessState.RETRYING == "retrying"
        assert BusinessState.APPLYING == "applying"
        assert BusinessState.APPLIED == "applied"
        assert BusinessState.FAILED == "failed"

    def test_enum_from_string(self):
        assert BusinessState("queued") == BusinessState.QUEUED
        assert BusinessState("pending") == BusinessState.PENDING_REVIEW
        assert BusinessState("completed") == BusinessState.CV_READY

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            BusinessState("nonexistent")

    def test_str_comparison(self):
        """BusinessState values should compare equal to plain strings."""
        assert BusinessState.QUEUED == "queued"
        assert "queued" == BusinessState.QUEUED
        assert BusinessState.PENDING_REVIEW == "pending"


class TestWorkflowStepEnum:
    """Test WorkflowStep enum values."""

    def test_enum_values(self):
        assert WorkflowStep.EXTRACTING == "extracting"
        assert WorkflowStep.COMPOSING_CV == "composing_cv"
        assert WorkflowStep.GENERATING_PDF == "generating_pdf"
        assert WorkflowStep.LOADING == "loading"

    def test_enum_from_string(self):
        assert WorkflowStep("extracting") == WorkflowStep.EXTRACTING


class TestValidTransitions:
    """Test that all valid transitions succeed."""

    @pytest.mark.parametrize(
        "current,target",
        [
            (BusinessState.QUEUED, BusinessState.PROCESSING),
            (BusinessState.QUEUED, BusinessState.CV_READY),
            (BusinessState.QUEUED, BusinessState.PENDING_REVIEW),
            (BusinessState.QUEUED, BusinessState.FAILED),
            (BusinessState.PROCESSING, BusinessState.CV_READY),
            (BusinessState.PROCESSING, BusinessState.PENDING_REVIEW),
            (BusinessState.PROCESSING, BusinessState.FAILED),
            (BusinessState.PENDING_REVIEW, BusinessState.APPROVED),
            (BusinessState.PENDING_REVIEW, BusinessState.DECLINED),
            (BusinessState.PENDING_REVIEW, BusinessState.RETRYING),
            (BusinessState.APPROVED, BusinessState.APPLYING),
            (BusinessState.APPROVED, BusinessState.APPLIED),
            (BusinessState.APPROVED, BusinessState.FAILED),
            (BusinessState.RETRYING, BusinessState.PENDING_REVIEW),
            (BusinessState.RETRYING, BusinessState.FAILED),
            (BusinessState.APPLYING, BusinessState.APPLIED),
            (BusinessState.APPLYING, BusinessState.FAILED),
            (BusinessState.FAILED, BusinessState.RETRYING),
        ],
    )
    def test_valid_transition(self, current, target):
        assert validate_transition(current, target) is True

    def test_self_transition_always_allowed(self):
        """Self-transitions are idempotent and always valid."""
        for state in BusinessState:
            assert validate_transition(state, state) is True


class TestInvalidTransitions:
    """Test that invalid transitions raise InvalidStateTransition."""

    @pytest.mark.parametrize(
        "current,target",
        [
            # Terminal states have no successors
            (BusinessState.CV_READY, BusinessState.PENDING_REVIEW),
            (BusinessState.CV_READY, BusinessState.FAILED),
            (BusinessState.DECLINED, BusinessState.APPROVED),
            (BusinessState.DECLINED, BusinessState.RETRYING),
            (BusinessState.APPLIED, BusinessState.FAILED),
            (BusinessState.APPLIED, BusinessState.QUEUED),
            # Backward transitions
            (BusinessState.APPROVED, BusinessState.PENDING_REVIEW),
            (BusinessState.APPROVED, BusinessState.QUEUED),
            (BusinessState.PROCESSING, BusinessState.QUEUED),
            # Skip-ahead transitions
            (BusinessState.QUEUED, BusinessState.APPLIED),
            (BusinessState.QUEUED, BusinessState.APPROVED),
            (BusinessState.PENDING_REVIEW, BusinessState.APPLIED),
            # Failed can only go to retrying
            (BusinessState.FAILED, BusinessState.APPROVED),
            (BusinessState.FAILED, BusinessState.PENDING_REVIEW),
            (BusinessState.FAILED, BusinessState.QUEUED),
        ],
    )
    def test_invalid_transition_raises(self, current, target):
        with pytest.raises(InvalidStateTransition) as exc_info:
            validate_transition(current, target)
        assert exc_info.value.current == current
        assert exc_info.value.target == target

    def test_error_includes_job_id(self):
        with pytest.raises(InvalidStateTransition) as exc_info:
            validate_transition(
                BusinessState.DECLINED,
                BusinessState.APPROVED,
                job_id="test-123",
            )
        assert "test-123" in str(exc_info.value)
        assert exc_info.value.job_id == "test-123"

    def test_error_includes_state_names(self):
        with pytest.raises(InvalidStateTransition) as exc_info:
            validate_transition(
                BusinessState.DECLINED,
                BusinessState.APPROVED,
            )
        assert "declined" in str(exc_info.value)
        assert "approved" in str(exc_info.value)


class TestTerminalStates:
    """Test that terminal states have no successors (except failed -> retrying)."""

    def test_cv_ready_is_terminal(self):
        assert ALLOWED_TRANSITIONS[BusinessState.CV_READY] == set()

    def test_declined_is_terminal(self):
        assert ALLOWED_TRANSITIONS[BusinessState.DECLINED] == set()

    def test_applied_is_terminal(self):
        assert ALLOWED_TRANSITIONS[BusinessState.APPLIED] == set()

    def test_failed_only_allows_retrying(self):
        assert ALLOWED_TRANSITIONS[BusinessState.FAILED] == {BusinessState.RETRYING}

    def test_all_states_have_transition_entry(self):
        """Every BusinessState must have an entry in ALLOWED_TRANSITIONS."""
        for state in BusinessState:
            assert state in ALLOWED_TRANSITIONS, f"Missing transition entry for {state}"


class TestTransitionMapCompleteness:
    """Test the transition map covers all expected workflows."""

    def test_preparation_workflow_mvp_path(self):
        """queued -> cv_ready (MVP mode)."""
        validate_transition(BusinessState.QUEUED, BusinessState.CV_READY)

    def test_preparation_workflow_full_path(self):
        """queued -> pending_review (full mode)."""
        validate_transition(BusinessState.QUEUED, BusinessState.PENDING_REVIEW)

    def test_hitl_approve_path(self):
        """pending_review -> approved."""
        validate_transition(BusinessState.PENDING_REVIEW, BusinessState.APPROVED)

    def test_hitl_decline_path(self):
        """pending_review -> declined."""
        validate_transition(BusinessState.PENDING_REVIEW, BusinessState.DECLINED)

    def test_hitl_retry_path(self):
        """pending_review -> retrying -> pending_review."""
        validate_transition(BusinessState.PENDING_REVIEW, BusinessState.RETRYING)
        validate_transition(BusinessState.RETRYING, BusinessState.PENDING_REVIEW)

    def test_application_path(self):
        """approved -> applying -> applied."""
        validate_transition(BusinessState.APPROVED, BusinessState.APPLYING)
        validate_transition(BusinessState.APPLYING, BusinessState.APPLIED)

    def test_failed_retry_path(self):
        """failed -> retrying -> pending_review."""
        validate_transition(BusinessState.FAILED, BusinessState.RETRYING)
        validate_transition(BusinessState.RETRYING, BusinessState.PENDING_REVIEW)
