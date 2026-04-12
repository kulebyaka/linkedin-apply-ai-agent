"""Application context - Dependency Injection container.

Replaces module-level globals with a single frozen dataclass that holds
all shared dependencies. Created once at startup and stored in app.state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from src.services.auth import AuthService
    from src.services.browser_automation import LinkedInAutomation
    from src.services.hitl_processor import HITLProcessor
    from src.services.job_orchestrator import JobOrchestrator
    from src.services.job_queue import JobQueue
    from src.services.job_repository import JobRepository
    from src.services.scheduler import LinkedInSearchScheduler
    from src.services.user_repository import UserRepository

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holds all shared application dependencies.

    Created once at startup via create_app_context() and stored in
    app.state.ctx. Endpoints retrieve it via request.app.state.ctx.
    """

    repository: JobRepository
    settings: Settings
    prep_workflow: CompiledStateGraph
    retry_workflow: CompiledStateGraph
    user_repository: UserRepository | None = None
    auth_service: AuthService | None = None
    job_queue: JobQueue | None = None
    scheduler: LinkedInSearchScheduler | None = None
    browser: LinkedInAutomation | None = None
    orchestrator: JobOrchestrator | None = None
    hitl_processor: HITLProcessor | None = None

    # Thread-safe tracking for in-progress workflows
    _tracking_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _workflow_threads: dict[str, dict] = field(default_factory=dict)
    # Background task references to prevent GC of fire-and-forget tasks
    _background_tasks: set[asyncio.Task] = field(default_factory=set)

    async def register_workflow(
        self, job_id: str, thread_id: str, workflow_type: str
    ) -> None:
        """Register an in-progress workflow for status tracking."""
        from datetime import datetime, timezone

        async with self._tracking_lock:
            self._workflow_threads[job_id] = {
                "thread_id": thread_id,
                "workflow_type": workflow_type,
                "created_at": datetime.now(tz=timezone.utc),
            }

    async def get_workflow_thread(self, job_id: str) -> dict | None:
        """Get workflow tracking info for a job_id."""
        async with self._tracking_lock:
            return self._workflow_threads.get(job_id)

    async def get_all_workflow_threads(self) -> dict[str, dict]:
        """Get a snapshot of all tracked workflows."""
        async with self._tracking_lock:
            return dict(self._workflow_threads)

    def create_background_task(self, coro) -> asyncio.Task:
        """Create an asyncio task and keep a reference to prevent GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task


def create_app_context(
    settings: Settings | None = None,
) -> AppContext:
    """Build and return a fully initialized AppContext.

    Args:
        settings: Optional settings override. Uses get_settings() if None.

    Returns:
        AppContext with repository, workflows, and queue wired up.
    """
    from src.agents.preparation_workflow import create_preparation_workflow
    from src.agents.retry_workflow import create_retry_workflow
    from src.services.auth import AuthService
    from src.services.hitl_processor import HITLProcessor
    from src.services.job_orchestrator import JobOrchestrator
    from src.services.job_queue import JobQueue
    from src.services.job_repository import get_repository
    from src.services.user_repository import UserRepository

    if settings is None:
        settings = get_settings()

    repository = get_repository(
        repo_type=settings.repo_type,
        db_path=settings.db_path,
    )

    prep_workflow = create_preparation_workflow()  # type: ignore[arg-type]
    retry_workflow = create_retry_workflow()  # type: ignore[arg-type]
    job_queue = JobQueue()

    user_repository = UserRepository()
    auth_service = AuthService(settings, user_repository)

    ctx = AppContext(
        repository=repository,
        settings=settings,
        prep_workflow=prep_workflow,
        retry_workflow=retry_workflow,
        user_repository=user_repository,
        auth_service=auth_service,
        job_queue=job_queue,
    )

    # Wire domain services (they need the full context)
    ctx.orchestrator = JobOrchestrator(ctx)
    ctx.hitl_processor = HITLProcessor(ctx)

    return ctx
