"""Tests for the CV PDF extraction registry and worker."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.llm.provider import BaseLLMClient, InstructorClient, LLMProvider, provider_supports_pdf
from src.services.cv.pdf_extraction import (
    CVExtractionRegistry,
    CVExtractionTask,
    run_extraction,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Provider capability flags
# ---------------------------------------------------------------------------


class TestProviderCapabilities:
    def test_openai_and_anthropic_support_pdf(self):
        assert provider_supports_pdf(LLMProvider.OPENAI) is True
        assert provider_supports_pdf(LLMProvider.ANTHROPIC) is True

    def test_deepseek_and_grok_do_not_support_pdf(self):
        assert provider_supports_pdf(LLMProvider.DEEPSEEK) is False
        assert provider_supports_pdf(LLMProvider.GROK) is False

    def test_instructor_client_supports_pdf(self):
        assert InstructorClient.SUPPORTS_PDF_INPUT is True

    def test_base_default_raises_not_implemented(self):
        class Dummy(BaseLLMClient):
            def generate(self, spec, temperature=0.7, **kwargs):  # pragma: no cover
                return ""

            def generate_json(  # pragma: no cover
                self,
                spec,
                response_model=None,
                schema=None,
                temperature=0.4,
                max_retries=3,
                **kwargs,
            ):
                return {}

        d = Dummy(api_key="x", model="dummy")
        with pytest.raises(NotImplementedError):
            d.generate_json_from_pdf(b"%PDF-1.4", "extract")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestCVExtractionRegistry:
    async def test_create_returns_task_with_user_and_pending_status(self):
        reg = CVExtractionRegistry()
        task = await reg.create("user-1")
        assert task.user_id == "user-1"
        assert task.status == "pending"
        assert task.id

    async def test_get_returns_created_task(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        assert await reg.get(task.id) is task

    async def test_get_unknown_returns_none(self):
        reg = CVExtractionRegistry()
        assert await reg.get("nope") is None

    async def test_update_mutates_task(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        await reg.update(task.id, status="running")
        assert (await reg.get(task.id)).status == "running"

    async def test_update_unknown_raises(self):
        reg = CVExtractionRegistry()
        with pytest.raises(KeyError):
            await reg.update("nope", status="failed")

    async def test_new_upload_evicts_previous_task_for_user(self):
        reg = CVExtractionRegistry()
        first = await reg.create("u")
        second = await reg.create("u")
        latest = await reg.get_latest_for_user("u")
        assert latest is second
        # Previous task is evicted to keep memory bounded.
        assert await reg.get(first.id) is None

    async def test_create_if_not_in_flight_returns_none_when_running(self):
        reg = CVExtractionRegistry()
        first = await reg.create("u")
        await reg.update(first.id, status="running")
        result = await reg.create_if_not_in_flight("u")
        assert result is None

    async def test_create_if_not_in_flight_creates_when_terminal(self):
        reg = CVExtractionRegistry()
        first = await reg.create("u")
        await reg.update(first.id, status="completed")
        result = await reg.create_if_not_in_flight("u")
        assert result is not None
        assert result.id != first.id


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


VALID_CV_JSON = {
    "contact": {
        "full_name": "Test Person",
        "email": "test@example.com",
    },
    "summary": "Experienced engineer.",
    "experiences": [
        {
            "company": "Acme",
            "position": "Engineer",
            "start_date": "2020-01-01",
            "end_date": "2022-01-01",
            "is_current": False,
            "description": "Built things.",
            "achievements": [],
            "technologies": [],
            "projects": [],
        }
    ],
    "education": [],
    "skills": [],
    "projects": [],
    "certifications": [],
    "languages": [],
}


def _make_llm_returning(value):
    """Build a mock LLM client whose generate_json_from_pdf returns value."""
    client = MagicMock(spec=BaseLLMClient)
    client.model = "mock-model"
    client.generate_json_from_pdf = MagicMock(return_value=value)
    return client


class TestRunExtraction:
    async def test_happy_path_records_valid_result(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        llm = _make_llm_returning(VALID_CV_JSON)

        await run_extraction(task, b"%PDF-1.4 fake", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "completed"
        assert updated.result_json == VALID_CV_JSON
        assert updated.validation_errors == []
        assert updated.error_message is None

    async def test_invalid_json_still_completed_with_validation_errors(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        # Email is missing — Pydantic should flag it.
        broken = {**VALID_CV_JSON, "contact": {"full_name": "X"}}
        llm = _make_llm_returning(broken)

        await run_extraction(task, b"%PDF", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "completed"
        assert updated.result_json == broken
        assert updated.validation_errors  # non-empty
        assert any("email" in err for err in updated.validation_errors)

    async def test_llm_exception_marks_task_failed(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        llm = MagicMock(spec=BaseLLMClient)
        llm.model = "mock-model"
        llm.generate_json_from_pdf = MagicMock(side_effect=RuntimeError("boom"))

        await run_extraction(task, b"%PDF", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "failed"
        assert updated.result_json is None
        assert "boom" in (updated.error_message or "")

    async def test_not_implemented_marks_task_failed(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        llm = MagicMock(spec=BaseLLMClient)
        llm.model = "mock-model"
        llm.generate_json_from_pdf = MagicMock(side_effect=NotImplementedError())

        await run_extraction(task, b"%PDF", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "failed"
        assert "does not support" in (updated.error_message or "").lower()

    async def test_non_dict_response_marks_failed(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")
        llm = _make_llm_returning(["not", "a", "dict"])

        await run_extraction(task, b"%PDF", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "failed"
        assert "non-object" in (updated.error_message or "")

    async def test_json_decode_error_triggers_one_retry(self):
        reg = CVExtractionRegistry()
        task = await reg.create("u")

        llm = MagicMock(spec=BaseLLMClient)
        llm.model = "mock-model"
        # First call: raise JSONDecodeError; second call: success.
        llm.generate_json_from_pdf = MagicMock(
            side_effect=[
                json.JSONDecodeError("bad", "doc", 0),
                VALID_CV_JSON,
            ]
        )

        await run_extraction(task, b"%PDF", llm, reg)

        updated = await reg.get(task.id)
        assert updated.status == "completed"
        assert llm.generate_json_from_pdf.call_count == 2


class TestCVExtractionTaskDefaults:
    def test_defaults(self):
        task = CVExtractionTask(id="abc", user_id="u")
        assert task.status == "pending"
        assert task.result_json is None
        assert task.validation_errors == []
        assert task.error_message is None
