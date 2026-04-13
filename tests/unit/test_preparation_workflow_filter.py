"""Tests for filter_job_node, save_filtered_out_node, and filter-related routing
in the preparation workflow."""

import sys
from unittest.mock import MagicMock

# WeasyPrint loads native system libraries at import time (Pango/GLib).
# These are not available in the CI / unit-test environment, so we stub
# out the entire weasyprint package before any import that chains into it.
_wp_mock = MagicMock()
for _mod in [
    "weasyprint",
    "weasyprint.css",
    "weasyprint.html",
    "weasyprint.text",
    "weasyprint.text.fonts",
    "weasyprint.text.ffi",
    "weasyprint.text.constants",
    "weasyprint.formatting_structure",
    "weasyprint.layout",
    "weasyprint.stacking",
    "weasyprint.document",
    "weasyprint.draw",
    "weasyprint.images",
]:
    sys.modules.setdefault(_mod, _wp_mock)

from datetime import datetime, timezone  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

from src.agents.preparation_workflow import (  # noqa: E402
    filter_job_node,
    route_after_filter,
    save_filtered_out_node,
)
from src.models.job_filter import FilterResult, UserFilterPreferences  # noqa: E402
from src.models.state_machine import BusinessState, WorkflowStep  # noqa: E402
from src.models.unified import JobRecord  # noqa: E402

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JOB_POSTING = {
    "id": "job-1",
    "title": "Senior Python Engineer",
    "company": "Acme",
    "location": "Remote",
    "description": "Build great things with Python",
}

GOOD_FILTER_RESULT = FilterResult(
    score=85,
    red_flags=[],
    disqualified=False,
    disqualifier_reason=None,
    reasoning="Great match.",
)

WARN_FILTER_RESULT = FilterResult(
    score=55,
    red_flags=["Requires 10+ years experience"],
    disqualified=False,
    disqualifier_reason=None,
    reasoning="Some concerns.",
)

REJECT_FILTER_RESULT = FilterResult(
    score=20,
    red_flags=["TS/SCI clearance required"],
    disqualified=False,
    disqualifier_reason=None,
    reasoning="Low score.",
)

DISQUALIFIED_FILTER_RESULT = FilterResult(
    score=10,
    red_flags=["Requires US citizenship"],
    disqualified=True,
    disqualifier_reason="Requires US citizenship for clearance.",
    reasoning="Hard disqualifier.",
)


def _make_state(**overrides) -> dict:
    state = {
        "job_id": "job-1",
        "user_id": "user-1",
        "source": "linkedin",
        "mode": "full",
        "raw_input": {},
        "job_posting": JOB_POSTING,
        "master_cv": {"contact": {"full_name": "Test User"}},
        "tailored_cv_json": None,
        "tailored_cv_pdf_path": None,
        "user_feedback": None,
        "retry_count": 0,
        "filter_result": None,
        "current_step": WorkflowStep.FILTERING,
        "error_message": None,
    }
    state.update(overrides)
    return state


def _make_config(repo=None, user_repo=None) -> dict:
    return {
        "configurable": {
            "thread_id": "thread-1",
            "repository": repo or AsyncMock(),
            "user_repository": user_repo,
        }
    }


# ---------------------------------------------------------------------------
# route_after_filter
# ---------------------------------------------------------------------------


class TestRouteAfterFilter:
    def test_routes_to_filtered_out_when_step_is_filtered_out(self):
        state = _make_state(current_step=BusinessState.FILTERED_OUT)
        assert route_after_filter(state) == "filtered_out"

    def test_routes_to_compose_when_step_is_job_filtered(self):
        state = _make_state(current_step=WorkflowStep.JOB_FILTERED)
        assert route_after_filter(state) == "compose"

    def test_routes_to_compose_when_step_is_filtering(self):
        state = _make_state(current_step=WorkflowStep.FILTERING)
        assert route_after_filter(state) == "compose"

    def test_routes_to_compose_when_no_current_step(self):
        state = _make_state()
        state.pop("current_step", None)
        assert route_after_filter(state) == "compose"


# ---------------------------------------------------------------------------
# filter_job_node — pass-through cases
# ---------------------------------------------------------------------------


class TestFilterJobNodePassThrough:
    async def test_passes_through_when_globally_disabled(self):
        state = _make_state()

        with patch("src.agents.preparation_workflow.settings") as mock_settings:
            mock_settings.job_filter_enabled = False
            result = await filter_job_node(state, _make_config())

        assert result["current_step"] == WorkflowStep.JOB_FILTERED
        assert result["filter_result"] is None

    async def test_passes_through_when_user_prefs_disabled(self):
        state = _make_state()
        prefs = UserFilterPreferences(enabled=False)

        user = MagicMock()
        user.filter_preferences = prefs

        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=user)

        with patch("src.agents.preparation_workflow.settings") as mock_settings:
            mock_settings.job_filter_enabled = True
            mock_settings.job_filter_reject_threshold = 30
            mock_settings.job_filter_warning_threshold = 70
            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        assert result["current_step"] == WorkflowStep.JOB_FILTERED
        assert result["filter_result"] is None

    async def test_passes_through_when_user_repo_unavailable(self):
        """When user_repository is not in config, filtering still runs with defaults."""
        state = _make_state()
        config = {"configurable": {"thread_id": "t1", "repository": AsyncMock()}}

        with patch("src.agents.preparation_workflow.settings") as mock_settings, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client") as mock_llm:

            mock_settings.job_filter_enabled = True
            mock_settings.job_filter_reject_threshold = 30
            mock_settings.job_filter_warning_threshold = 70

            mock_filter_instance = MagicMock()
            mock_filter_instance.evaluate_job = MagicMock(return_value=GOOD_FILTER_RESULT)
            mock_filter_instance.should_reject = MagicMock(return_value=False)
            mock_filter_instance.should_warn = MagicMock(return_value=False)
            mock_job_filter_cls.return_value = mock_filter_instance
            mock_llm.return_value = MagicMock()

            result = await filter_job_node(state, config)

        assert result["current_step"] == WorkflowStep.JOB_FILTERED

    async def test_passes_through_on_llm_error(self):
        """Filter errors must not block the pipeline."""
        state = _make_state()

        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.agents.preparation_workflow.settings") as mock_settings, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            mock_settings.job_filter_enabled = True
            mock_settings.job_filter_reject_threshold = 30
            mock_settings.job_filter_warning_threshold = 70

            mock_filter_instance = MagicMock()
            mock_filter_instance.evaluate_job = MagicMock(
                side_effect=RuntimeError("LLM API timeout")
            )
            mock_job_filter_cls.return_value = mock_filter_instance

            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        assert result["current_step"] == WorkflowStep.JOB_FILTERED
        assert result["filter_result"] is None


# ---------------------------------------------------------------------------
# filter_job_node — filtering outcomes
# ---------------------------------------------------------------------------


class TestFilterJobNodeOutcomes:
    def _make_filter_node_patches(self, filter_result: FilterResult, mock_settings):
        """Return a JobFilter mock wired to the given result."""
        mock_settings.job_filter_enabled = True
        mock_settings.job_filter_reject_threshold = 30
        mock_settings.job_filter_warning_threshold = 70

        from src.services.job_filter import JobFilter

        mock_filter_instance = MagicMock(spec=JobFilter)
        mock_filter_instance.evaluate_job = MagicMock(return_value=filter_result)
        mock_filter_instance.should_reject = JobFilter.should_reject.__get__(
            mock_filter_instance, JobFilter
        )
        mock_filter_instance.should_warn = JobFilter.should_warn.__get__(
            mock_filter_instance, JobFilter
        )
        return mock_filter_instance

    async def test_clean_pass(self):
        state = _make_state()
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.agents.preparation_workflow.settings") as ms, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            ms.job_filter_enabled = True
            ms.job_filter_reject_threshold = 30
            ms.job_filter_warning_threshold = 70

            mock_inst = MagicMock()
            mock_inst.evaluate_job = MagicMock(return_value=GOOD_FILTER_RESULT)
            mock_inst.should_reject = MagicMock(return_value=False)
            mock_inst.should_warn = MagicMock(return_value=False)
            mock_job_filter_cls.return_value = mock_inst

            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        assert result["current_step"] == WorkflowStep.JOB_FILTERED
        assert result["filter_result"] is not None
        assert result["filter_result"]["score"] == 85
        assert result["filter_result"]["disqualified"] is False

    async def test_warning_pass(self):
        state = _make_state()
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.agents.preparation_workflow.settings") as ms, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            ms.job_filter_enabled = True
            ms.job_filter_reject_threshold = 30
            ms.job_filter_warning_threshold = 70

            mock_inst = MagicMock()
            mock_inst.evaluate_job = MagicMock(return_value=WARN_FILTER_RESULT)
            mock_inst.should_reject = MagicMock(return_value=False)
            mock_inst.should_warn = MagicMock(return_value=True)
            mock_job_filter_cls.return_value = mock_inst

            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        # Warning still passes through the pipeline
        assert result["current_step"] == WorkflowStep.JOB_FILTERED
        assert result["filter_result"]["score"] == 55
        assert len(result["filter_result"]["red_flags"]) == 1

    async def test_reject_by_low_score(self):
        state = _make_state()
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.agents.preparation_workflow.settings") as ms, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            ms.job_filter_enabled = True
            ms.job_filter_reject_threshold = 30
            ms.job_filter_warning_threshold = 70

            mock_inst = MagicMock()
            mock_inst.evaluate_job = MagicMock(return_value=REJECT_FILTER_RESULT)
            mock_inst.should_reject = MagicMock(return_value=True)
            mock_job_filter_cls.return_value = mock_inst

            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        assert result["current_step"] == BusinessState.FILTERED_OUT
        assert result["filter_result"]["score"] == 20

    async def test_reject_by_disqualifier(self):
        state = _make_state()
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.agents.preparation_workflow.settings") as ms, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            ms.job_filter_enabled = True
            ms.job_filter_reject_threshold = 30
            ms.job_filter_warning_threshold = 70

            mock_inst = MagicMock()
            mock_inst.evaluate_job = MagicMock(return_value=DISQUALIFIED_FILTER_RESULT)
            mock_inst.should_reject = MagicMock(return_value=True)
            mock_job_filter_cls.return_value = mock_inst

            result = await filter_job_node(state, _make_config(user_repo=user_repo))

        assert result["current_step"] == BusinessState.FILTERED_OUT
        assert result["filter_result"]["disqualified"] is True

    async def test_uses_user_thresholds_from_prefs(self):
        """User-configured thresholds override global settings."""
        state = _make_state()
        prefs = UserFilterPreferences(
            reject_threshold=50,
            warning_threshold=80,
            enabled=True,
        )
        user = MagicMock()
        user.filter_preferences = prefs

        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=user)

        captured_reject_threshold = {}

        def fake_should_reject(result, reject_threshold=30):
            captured_reject_threshold["value"] = reject_threshold
            return False

        with patch("src.agents.preparation_workflow.settings") as ms, \
             patch("src.agents.preparation_workflow.JobFilter") as mock_job_filter_cls, \
             patch("src.agents.preparation_workflow.create_llm_client"):

            ms.job_filter_enabled = True
            ms.job_filter_reject_threshold = 30
            ms.job_filter_warning_threshold = 70

            mock_inst = MagicMock()
            mock_inst.evaluate_job = MagicMock(return_value=GOOD_FILTER_RESULT)
            mock_inst.should_reject = MagicMock(side_effect=fake_should_reject)
            mock_inst.should_warn = MagicMock(return_value=False)
            mock_job_filter_cls.return_value = mock_inst

            await filter_job_node(state, _make_config(user_repo=user_repo))

        # User threshold (50) should be used instead of global (30)
        assert captured_reject_threshold["value"] == 50


# ---------------------------------------------------------------------------
# save_filtered_out_node
# ---------------------------------------------------------------------------


class TestSaveFilteredOutNode:
    async def test_saves_minimal_job_record(self):
        repo = AsyncMock()
        repo.create = AsyncMock()

        state = _make_state(
            current_step=BusinessState.FILTERED_OUT,
            filter_result=REJECT_FILTER_RESULT.model_dump(),
        )
        config = _make_config(repo=repo)

        await save_filtered_out_node(state, config)

        repo.create.assert_called_once()
        saved_record: JobRecord = repo.create.call_args[0][0]
        assert saved_record.status == BusinessState.FILTERED_OUT
        assert saved_record.job_id == "job-1"
        assert saved_record.filter_result is not None
        assert saved_record.filter_result["score"] == 20
        # No CV data
        assert saved_record.current_cv_json is None
        assert saved_record.current_pdf_path is None

    async def test_saves_with_correct_user_id(self):
        repo = AsyncMock()
        state = _make_state(user_id="user-xyz", current_step=BusinessState.FILTERED_OUT)
        await save_filtered_out_node(state, _make_config(repo=repo))
        saved_record = repo.create.call_args[0][0]
        assert saved_record.user_id == "user-xyz"

    async def test_handles_save_error_gracefully(self):
        repo = AsyncMock()
        repo.create = AsyncMock(side_effect=RuntimeError("DB write failed"))

        state = _make_state(current_step=BusinessState.FILTERED_OUT)
        result = await save_filtered_out_node(state, _make_config(repo=repo))

        # Should not raise; error captured in state
        assert result["error_message"] is not None
        assert "DB write failed" in result["error_message"]

    async def test_saves_source_and_mode(self):
        repo = AsyncMock()
        state = _make_state(source="linkedin", mode="full")
        await save_filtered_out_node(state, _make_config(repo=repo))

        saved_record = repo.create.call_args[0][0]
        assert saved_record.source == "linkedin"
        assert saved_record.mode == "full"


# ---------------------------------------------------------------------------
# HITLProcessor.get_pending includes filter_result
# ---------------------------------------------------------------------------


class TestHITLProcessorFilterResult:
    async def test_get_pending_includes_filter_result(self):
        from src.context import AppContext
        from src.services.hitl_processor import HITLProcessor

        filter_data = {
            "score": 55,
            "red_flags": ["Requires 10+ years"],
            "disqualified": False,
            "disqualifier_reason": None,
            "reasoning": "Warning.",
        }

        job = JobRecord(
            job_id="job-1",
            user_id="user-1",
            source="linkedin",
            mode="full",
            status=BusinessState.PENDING_REVIEW,
            job_posting={"title": "Engineer", "company": "Acme"},
            current_cv_json={"name": "Test"},
            filter_result=filter_data,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=[job])
        repo.get_cv_attempts = AsyncMock(return_value=[])

        ctx = AppContext(
            repository=repo,
            settings=MagicMock(),
            prep_workflow=MagicMock(),
            retry_workflow=MagicMock(),
        )
        processor = HITLProcessor(ctx)
        pending = await processor.get_pending("user-1")

        assert len(pending) == 1
        assert pending[0].filter_result == filter_data
        assert pending[0].filter_result["score"] == 55

    async def test_get_pending_filter_result_none_for_non_linkedin(self):
        from src.context import AppContext
        from src.services.hitl_processor import HITLProcessor

        job = JobRecord(
            job_id="job-2",
            user_id="user-1",
            source="manual",
            mode="full",
            status=BusinessState.PENDING_REVIEW,
            job_posting={"title": "Engineer", "company": "Acme"},
            current_cv_json={"name": "Test"},
            filter_result=None,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=[job])
        repo.get_cv_attempts = AsyncMock(return_value=[])

        ctx = AppContext(
            repository=repo,
            settings=MagicMock(),
            prep_workflow=MagicMock(),
            retry_workflow=MagicMock(),
        )
        processor = HITLProcessor(ctx)
        pending = await processor.get_pending("user-1")

        assert len(pending) == 1
        assert pending[0].filter_result is None
