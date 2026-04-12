"""Tests for AppContext DI container."""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.context import AppContext, create_app_context

pytestmark = pytest.mark.asyncio


class TestAppContext:
    """Test AppContext creation and field access."""

    def _make_ctx(self) -> AppContext:
        """Create an AppContext with mock dependencies."""
        return AppContext(
            repository=MagicMock(),
            settings=MagicMock(),
            prep_workflow=MagicMock(),
            retry_workflow=MagicMock(),
            job_queue=MagicMock(),
        )

    def test_fields_accessible(self):
        ctx = self._make_ctx()
        assert ctx.repository is not None
        assert ctx.settings is not None
        assert ctx.prep_workflow is not None
        assert ctx.retry_workflow is not None
        assert ctx.job_queue is not None

    def test_optional_fields_default_none(self):
        ctx = AppContext(
            repository=MagicMock(),
            settings=MagicMock(),
            prep_workflow=MagicMock(),
            retry_workflow=MagicMock(),
        )
        assert ctx.job_queue is None
        assert ctx.scheduler is None
        assert ctx.browser is None

    async def test_register_and_get_workflow(self):
        ctx = self._make_ctx()
        await ctx.register_workflow("job-1", "thread-1", "preparation", user_id="u1")

        info = await ctx.get_workflow_thread("job-1")
        assert info is not None
        assert info["thread_id"] == "thread-1"
        assert info["workflow_type"] == "preparation"
        assert info["user_id"] == "u1"
        assert "created_at" in info

    async def test_unregister_workflow(self):
        ctx = self._make_ctx()
        await ctx.register_workflow("job-1", "thread-1", "preparation")
        await ctx.unregister_workflow("job-1")
        assert await ctx.get_workflow_thread("job-1") is None

    async def test_unregister_nonexistent_is_noop(self):
        ctx = self._make_ctx()
        await ctx.unregister_workflow("nonexistent")  # should not raise

    async def test_get_nonexistent_workflow_returns_none(self):
        ctx = self._make_ctx()
        info = await ctx.get_workflow_thread("nonexistent")
        assert info is None

    async def test_get_all_workflow_threads(self):
        ctx = self._make_ctx()
        await ctx.register_workflow("job-1", "t1", "preparation")
        await ctx.register_workflow("job-2", "t2", "retry")

        all_threads = await ctx.get_all_workflow_threads()
        assert len(all_threads) == 2
        assert "job-1" in all_threads
        assert "job-2" in all_threads

    async def test_register_overwrites_existing(self):
        ctx = self._make_ctx()
        await ctx.register_workflow("job-1", "t1", "preparation")
        await ctx.register_workflow("job-1", "t2", "retry")

        info = await ctx.get_workflow_thread("job-1")
        assert info["thread_id"] == "t2"
        assert info["workflow_type"] == "retry"

    async def test_thread_safety(self):
        """Test concurrent register_workflow calls don't lose data."""
        ctx = self._make_ctx()

        async def register(i: int):
            await ctx.register_workflow(f"job-{i}", f"thread-{i}", "preparation")

        await asyncio.gather(*[register(i) for i in range(100)])

        all_threads = await ctx.get_all_workflow_threads()
        assert len(all_threads) == 100


class TestCreateAppContext:
    """Test the create_app_context factory.

    These tests require DYLD_LIBRARY_PATH=/opt/homebrew/lib on macOS
    because create_app_context imports the preparation_workflow module
    which imports WeasyPrint (via PDFGenerator).
    """

    @pytest.fixture(autouse=True)
    def _set_dyld(self, monkeypatch):
        """Set DYLD_LIBRARY_PATH for WeasyPrint if not already set."""
        import os
        if not os.environ.get("DYLD_LIBRARY_PATH"):
            monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

    def test_creates_context_with_defaults(self):
        """Test that create_app_context returns a properly wired context."""
        from src.config.settings import Settings

        test_settings = Settings(_env_file=None)
        ctx = create_app_context(settings=test_settings)

        assert ctx.repository is not None
        assert ctx.settings is test_settings
        assert ctx.prep_workflow is not None
        assert ctx.retry_workflow is not None
        assert ctx.job_queue is not None

    def test_creates_context_uses_provided_settings(self):
        from src.config.settings import Settings

        test_settings = Settings(_env_file=None, repo_type="memory")
        ctx = create_app_context(settings=test_settings)

        assert ctx.settings.repo_type == "memory"
