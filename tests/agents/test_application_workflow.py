"""Unit tests for the deterministic Easy Apply application workflow.

A scripted ``StubBridge`` stands in for ``ApplyBridge`` so we exercise the full
LangGraph (open → fill loop → submit → finalize) and the abort/recovery paths
without any WebSocket / browser. Terminal state is asserted against a real
``InMemoryJobRepository`` (so ``ALLOWED_TRANSITIONS`` is enforced for real).
"""

from __future__ import annotations

import pytest

from pathlib import Path

from src.agents.application_workflow import (
    create_application_workflow,
    get_apply_bridge_from_config,
)
from src.config.settings import Settings
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.services.db.in_memory_repository import InMemoryJobRepository
from src.services.linkedin.apply_bridge import (
    AdvanceResult,
    ExtensionUnavailable,
    FormState,
    SubmitResult,
)
from src.services.linkedin.field_classifier import FieldFill, Skip, Unknown

pytestmark = pytest.mark.asyncio

JOB_ID = "job-apply-1"
USER_ID = "user-1"
JOB_URL = "https://www.linkedin.com/jobs/view/123/"


def _settings(**overrides) -> Settings:
    base = {"jwt_secret": "x" * 40, "apply_per_app_timeout_seconds": 180}
    base.update(overrides)
    return Settings(**base)


class StubBridge:
    """Scripted ApplyBridge. ``form_states`` / ``advance_results`` are FIFO queues."""

    def __init__(
        self,
        *,
        opened: bool = True,
        form_states: list[FormState] | None = None,
        advance_results: list[AdvanceResult] | None = None,
        submit_result: SubmitResult | None = None,
        disconnect_on: set[str] | None = None,
        error_on: set[str] | None = None,
    ) -> None:
        self._opened = opened
        self._forms = list(form_states or [])
        self._advances = list(advance_results or [])
        self._submit = submit_result or SubmitResult(confirmed=True)
        self._disconnect_on = disconnect_on or set()
        self._error_on = error_on or set()
        self.calls: list[str] = []
        self.discard_reasons: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.filled: list[tuple[str, str]] = []

    def _maybe_disconnect(self, method: str) -> None:
        if method in self._disconnect_on:
            raise ExtensionUnavailable(f"no session ({method})")
        if method in self._error_on:
            raise RuntimeError(f"boom ({method})")

    async def open_easy_apply(self, user_id, job_url=None):
        self.calls.append("open_easy_apply")
        self._maybe_disconnect("open_easy_apply")
        return self._opened

    async def read_form_state(self, user_id, apply_profile=None, contact_info=None):
        self.calls.append("read_form_state")
        self._maybe_disconnect("read_form_state")
        return self._forms.pop(0) if self._forms else FormState()

    async def fill_field(self, user_id, selector, value):
        self.calls.append("fill_field")
        self._maybe_disconnect("fill_field")
        self.filled.append((selector, value))
        return {}

    async def upload_file(self, user_id, selector, pdf_path):
        self.calls.append("upload_file")
        self.uploaded.append((selector, pdf_path))
        return {}

    async def advance_step(self, user_id):
        self.calls.append("advance_step")
        self._maybe_disconnect("advance_step")
        return self._advances.pop(0) if self._advances else AdvanceResult(advanced=False)

    async def submit_form(self, user_id):
        self.calls.append("submit_form")
        self._maybe_disconnect("submit_form")
        return self._submit

    async def discard(self, user_id, reason=""):
        self.calls.append("discard")
        self.discard_reasons.append(reason)
        return {}


async def _run(bridge: StubBridge, *, settings: Settings | None = None, status=BusinessState.APPLYING):
    repo = InMemoryJobRepository()
    await repo.create(
        JobRecord(job_id=JOB_ID, user_id=USER_ID, source="linkedin", mode="full", status=status)
    )
    workflow = create_application_workflow()
    config = {
        "configurable": {
            "thread_id": f"thread-{JOB_ID}",
            "repository": repo,
            "apply_bridge": bridge,
            "settings": settings or _settings(),
        }
    }
    final = await workflow.ainvoke(
        {
            "job_id": JOB_ID,
            "user_id": USER_ID,
            "job_url": JOB_URL,
            "pdf_path": "",
        },
        config,
    )
    record = await repo.get(JOB_ID)
    return final, record


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestHappyPath:
    async def test_three_step_form_reaches_applied(self):
        # Two steps that advance, then a final step with no Next/Review → submit.
        forms = [
            FormState(step=1, total=3, fill_plan=[FieldFill(selector="#a", value="x", kind="email")]),
            FormState(step=2, total=3, fill_plan=[FieldFill(selector="#b", value="5", kind="years_experience")]),
            FormState(step=3, total=3),
        ]
        advances = [
            AdvanceResult(advanced=True),
            AdvanceResult(advanced=True),
            AdvanceResult(advanced=False),  # no nav button → submit step
        ]
        bridge = StubBridge(
            form_states=forms,
            advance_results=advances,
            submit_result=SubmitResult(confirmed=True, confirmation_text="Application sent"),
        )

        final, record = await _run(bridge)

        assert record.status == BusinessState.APPLIED
        assert record.application_url == JOB_URL
        assert "submit_form" in bridge.calls
        assert "discard" not in bridge.calls
        assert final["current_step"] == str(BusinessState.APPLIED)

    async def test_uploads_pdf_when_file_field_present(self):
        forms = [FormState(step=1, total=1, skipped=[Skip(selector="#cv", reason="file upload handled by upload_file")])]
        advances = [AdvanceResult(advanced=False)]
        bridge = StubBridge(form_states=forms, advance_results=advances)
        repo = InMemoryJobRepository()
        await repo.create(
            JobRecord(job_id=JOB_ID, user_id=USER_ID, source="linkedin", mode="full", status=BusinessState.APPLYING)
        )
        workflow = create_application_workflow()
        config = {
            "configurable": {
                "thread_id": "t",
                "repository": repo,
                "apply_bridge": bridge,
                "settings": _settings(),
            }
        }
        await workflow.ainvoke(
            {"job_id": JOB_ID, "user_id": USER_ID, "job_url": JOB_URL, "pdf_path": "/tmp/cv.pdf"}, config
        )
        assert bridge.uploaded == [("#cv", "/tmp/cv.pdf")]


# ---------------------------------------------------------------------------
# Abort: unknown field → manual_required
# ---------------------------------------------------------------------------
class TestUnknownField:
    async def test_unknown_field_aborts_to_manual_required(self):
        forms = [
            FormState(
                step=1,
                total=2,
                unknown_fields=[Unknown(selector="#q", label="Why us?", reason="unrecognized field")],
            )
        ]
        bridge = StubBridge(form_states=forms)

        final, record = await _run(bridge)

        assert record.status == BusinessState.MANUAL_REQUIRED
        assert "discard" in bridge.calls
        assert "submit_form" not in bridge.calls
        assert "Why us?" in (record.error_message or "")


# ---------------------------------------------------------------------------
# Daily limit → stop without submit
# ---------------------------------------------------------------------------
class TestDailyLimit:
    async def test_daily_limit_stops_without_submit(self):
        forms = [FormState(step=1, total=1, daily_limit_reached=True)]
        bridge = StubBridge(form_states=forms)

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "daily" in (record.error_message or "").lower()
        assert "submit_form" not in bridge.calls
        assert "discard" in bridge.calls


# ---------------------------------------------------------------------------
# Disconnect → needs_extension
# ---------------------------------------------------------------------------
class TestDisconnect:
    async def test_disconnect_mid_apply_maps_to_needs_extension(self):
        forms = [FormState(step=1, total=2)]
        bridge = StubBridge(form_states=forms, disconnect_on={"read_form_state"})

        final, record = await _run(bridge)

        assert record.status == BusinessState.NEEDS_EXTENSION
        assert "submit_form" not in bridge.calls

    async def test_disconnect_on_open_maps_to_needs_extension(self):
        bridge = StubBridge(disconnect_on={"open_easy_apply"})
        # From APPROVED, NEEDS_EXTENSION is a legal transition.
        final, record = await _run(bridge, status=BusinessState.APPROVED)
        assert record.status == BusinessState.NEEDS_EXTENSION


# ---------------------------------------------------------------------------
# Per-app timeout → failed
# ---------------------------------------------------------------------------
class TestTimeout:
    async def test_per_app_timeout_fails_and_discards(self):
        forms = [FormState(step=1, total=3)]
        bridge = StubBridge(form_states=forms, advance_results=[AdvanceResult(advanced=True)])

        final, record = await _run(bridge, settings=_settings(apply_per_app_timeout_seconds=-1))

        assert record.status == BusinessState.FAILED
        assert "discard" in bridge.calls
        assert "submit_form" not in bridge.calls


# ---------------------------------------------------------------------------
# Submit not confirmed → failed
# ---------------------------------------------------------------------------
class TestSubmitUnconfirmed:
    async def test_unconfirmed_submit_is_failed(self):
        forms = [FormState(step=1, total=1)]
        advances = [AdvanceResult(advanced=False)]
        bridge = StubBridge(
            form_states=forms,
            advance_results=advances,
            submit_result=SubmitResult(confirmed=False),
        )

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "submit_form" in bridge.calls


# ---------------------------------------------------------------------------
# Open / generic-error / validation-error / screenshot branches
# ---------------------------------------------------------------------------
class TestOpenFailures:
    async def test_modal_not_opening_is_failed(self):
        bridge = StubBridge(opened=False)

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "did not open" in (record.error_message or "")
        assert "read_form_state" not in bridge.calls

    async def test_generic_open_error_is_failed(self):
        bridge = StubBridge(error_on={"open_easy_apply"})

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "open_easy_apply failed" in (record.error_message or "")


class TestValidationErrors:
    async def test_validation_errors_abort_to_manual_required(self):
        forms = [FormState(step=1, total=2)]
        advances = [AdvanceResult(advanced=False, errors=["Phone is required", "Bad ZIP"])]
        bridge = StubBridge(form_states=forms, advance_results=advances)

        final, record = await _run(bridge)

        assert record.status == BusinessState.MANUAL_REQUIRED
        assert "Phone is required" in (record.error_message or "")
        assert "discard" in bridge.calls
        assert "submit_form" not in bridge.calls


class TestGenericErrors:
    async def test_generic_read_form_error_is_failed(self):
        bridge = StubBridge(error_on={"read_form_state"})

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "read_form_state failed" in (record.error_message or "")

    async def test_generic_submit_error_is_failed(self):
        forms = [FormState(step=1, total=1)]
        advances = [AdvanceResult(advanced=False)]
        bridge = StubBridge(
            form_states=forms, advance_results=advances, error_on={"submit_form"}
        )

        final, record = await _run(bridge)

        assert record.status == BusinessState.FAILED
        assert "submit_form failed" in (record.error_message or "")


class TestConfirmationScreenshot:
    async def test_screenshot_persisted_on_applied(self, tmp_path):
        # A 1x1 transparent PNG, base64-encoded (with a data-URL prefix to exercise
        # the prefix-stripping branch).
        png_b64 = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        forms = [FormState(step=1, total=1)]
        advances = [AdvanceResult(advanced=False)]
        bridge = StubBridge(
            form_states=forms,
            advance_results=advances,
            submit_result=SubmitResult(
                confirmed=True, confirmation_text="Application sent", screenshot_b64=png_b64
            ),
        )

        final, record = await _run(
            bridge, settings=_settings(generated_cvs_dir=str(tmp_path))
        )

        assert record.status == BusinessState.APPLIED
        shot = final["confirmation_screenshot_path"]
        assert shot is not None
        assert Path(shot).exists()
        assert Path(shot).read_bytes()  # non-empty


class TestConfigHelpers:
    def test_missing_apply_bridge_raises(self):
        with pytest.raises(RuntimeError, match="ApplyBridge not found"):
            get_apply_bridge_from_config({"configurable": {}})
