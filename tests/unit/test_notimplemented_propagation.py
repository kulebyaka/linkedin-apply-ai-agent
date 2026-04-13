"""Tests verifying that NotImplementedError propagates properly in workflow nodes.

Task 4: Remove NotImplementedError swallowing. Stubs should fail loudly,
not fabricate data or silently drop operations.
"""

import sys
from unittest.mock import MagicMock

# Mock weasyprint before importing workflow modules (avoids native lib requirement)
_wp_mock = MagicMock()
sys.modules.setdefault("weasyprint", _wp_mock)
sys.modules.setdefault("weasyprint.text", MagicMock())
sys.modules.setdefault("weasyprint.text.fonts", MagicMock())

from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

from src.agents.preparation_workflow import extract_job_node, save_to_db_node  # noqa: E402
from src.agents.retry_workflow import (  # noqa: E402
    load_from_db_node as retry_load_from_db_node,
)
from src.agents.retry_workflow import (  # noqa: E402
    update_db_node as retry_update_db_node,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# preparation_workflow: extract_job_node
# ---------------------------------------------------------------------------


async def test_extract_job_url_not_implemented_fails():
    """URL extraction NotImplementedError should propagate as a failure, not fabricate stub data."""
    state = {
        "job_id": "test-1",
        "source": "url",
        "mode": "mvp",
        "raw_input": {"url": "https://example.com/job/123"},
        "current_step": "",
        "error_message": None,
    }

    mock_adapter = MagicMock()
    mock_adapter.extract = AsyncMock(
        side_effect=NotImplementedError("URL job extraction not yet implemented.")
    )

    mock_factory = MagicMock()
    mock_factory.get_adapter.return_value = mock_adapter

    with (
        patch("src.agents.preparation_workflow.JobSourceFactory", return_value=mock_factory),
        patch("src.agents.preparation_workflow.create_llm_client"),
    ):
        result = await extract_job_node(state)

    assert result["current_step"] == "failed"
    assert "not yet implemented" in result["error_message"]
    assert "source='manual'" in result["error_message"]
    # Must NOT have fabricated a job_posting
    assert result.get("job_posting") is None


async def test_extract_job_linkedin_not_implemented_fails():
    """LinkedIn extraction NotImplementedError should propagate as a failure."""
    state = {
        "job_id": "test-2",
        "source": "linkedin",
        "mode": "full",
        "raw_input": {"linkedin_job_id": "12345"},
        "current_step": "",
        "error_message": None,
    }

    mock_adapter = MagicMock()
    mock_adapter.extract = AsyncMock(
        side_effect=NotImplementedError("LinkedIn extraction not implemented.")
    )

    mock_factory = MagicMock()
    mock_factory.get_adapter.return_value = mock_adapter

    with (
        patch("src.agents.preparation_workflow.JobSourceFactory", return_value=mock_factory),
        patch("src.agents.preparation_workflow.create_llm_client"),
    ):
        result = await extract_job_node(state)

    assert result["current_step"] == "failed"
    assert "not yet implemented" in result["error_message"]


# ---------------------------------------------------------------------------
# preparation_workflow: save_to_db_node
# ---------------------------------------------------------------------------


async def test_save_to_db_repo_not_implemented_fails():
    """If repo.create() raises NotImplementedError, save_to_db_node should fail, not silently skip."""
    state = {
        "job_id": "test-3",
        "source": "manual",
        "mode": "mvp",
        "raw_input": {},
        "job_posting": {"title": "Engineer", "company": "Acme"},
        "tailored_cv_json": {"contact": {"full_name": "Test"}},
        "tailored_cv_pdf_path": "/tmp/test.pdf",
        "current_step": "",
        "error_message": None,
        "user_feedback": None,
        "retry_count": 0,
    }

    mock_repo = AsyncMock()
    mock_repo.create = AsyncMock(
        side_effect=NotImplementedError("create() not implemented")
    )
    config = {"configurable": {"repository": mock_repo}}

    result = await save_to_db_node(state, config=config)

    assert result["current_step"] == "failed"
    assert result["error_message"] is not None
    assert "not implemented" in result["error_message"].lower()


# ---------------------------------------------------------------------------
# retry_workflow: load_from_db_node
# ---------------------------------------------------------------------------


async def test_retry_load_from_db_not_implemented_fails():
    """If repo.get() raises NotImplementedError, retry load should fail, not use stub data."""
    state = {
        "job_id": "test-4",
        "user_feedback": "Make it shorter",
        "current_step": "",
        "error_message": None,
        "job_posting": None,
        "master_cv": None,
        "retry_count": 0,
    }

    mock_repo = AsyncMock()
    mock_repo.get = AsyncMock(
        side_effect=NotImplementedError("get() not implemented")
    )
    config = {"configurable": {"repository": mock_repo}}

    result = await retry_load_from_db_node(state, config=config)

    assert result["current_step"] == "failed"
    assert result["error_message"] is not None


# ---------------------------------------------------------------------------
# retry_workflow: update_db_node
# ---------------------------------------------------------------------------


async def test_retry_update_db_not_implemented_fails():
    """If repo.update() raises NotImplementedError, retry update should fail, not silently skip."""
    state = {
        "job_id": "test-5",
        "user_feedback": "More detail",
        "tailored_cv_json": {"contact": {"full_name": "Test"}},
        "tailored_cv_pdf_path": "/tmp/test.pdf",
        "current_step": "",
        "error_message": None,
        "retry_count": 1,
    }

    mock_repo = AsyncMock()
    mock_repo.update = AsyncMock(
        side_effect=NotImplementedError("update() not implemented")
    )
    config = {"configurable": {"repository": mock_repo}}

    result = await retry_update_db_node(state, config=config)

    assert result["current_step"] == "failed"
    assert result["error_message"] is not None


