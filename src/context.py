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

    from src.agents.dispatcher import WorkflowDispatcher
    from src.bridge import SessionStore, WsRelay
    from src.services.alerts import AdminAlertService
    from src.services.auth.auth import AuthService
    from src.services.auth.magic_link_repository import MagicLinkRepository
    from src.services.auth.user_repository import UserRepository
    from src.services.auth.user_service import UserService
    from src.services.cv.pdf_extraction import CVExtractionRegistry
    from src.services.db.job_repository import JobRepository
    from src.services.jobs.hitl_processor import HITLProcessor
    from src.services.jobs.job_orchestrator import JobOrchestrator
    from src.services.jobs.job_queue import ConsumerManager, JobQueue
    from src.services.jobs.refinement_scheduler import RefinementScheduler
    from src.services.jobs.scheduler import LinkedInSearchScheduler
    from src.services.linkedin.browser_automation import LinkedInAutomation
    from src.services.notifications.notification_repository import NotificationRepository

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
    magic_link_repository: MagicLinkRepository | None = None
    user_service: UserService | None = None
    auth_service: AuthService | None = None
    admin_alert_service: AdminAlertService | None = None
    job_queue: JobQueue | None = None
    scheduler: LinkedInSearchScheduler | None = None
    refinement_scheduler: RefinementScheduler | None = None
    notification_repository: NotificationRepository | None = None
    browser: LinkedInAutomation | None = None
    orchestrator: JobOrchestrator | None = None
    hitl_processor: HITLProcessor | None = None
    cv_extraction_registry: CVExtractionRegistry | None = None
    consumer_manager: ConsumerManager | None = None
    workflow_dispatcher: WorkflowDispatcher | None = None
    # Easy Apply browser bridge (extension WebSocket relay + session registry)
    session_store: SessionStore | None = None
    ws_relay: WsRelay | None = None

    # Lock for LinkedIn search/browser initialization (manual trigger path).
    linkedin_init_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serializes admin role changes so the last-admin guard is atomic with set_role.
    admin_role_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serializes admin retry start so the status check + re-queue + schedule are atomic.
    admin_retry_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Thread-safe tracking for in-progress workflows
    _tracking_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _workflow_threads: dict[str, dict] = field(default_factory=dict)
    # Background task references to prevent GC of fire-and-forget tasks
    _background_tasks: set[asyncio.Task] = field(default_factory=set)

    async def register_workflow(
        self, job_id: str, thread_id: str, workflow_type: str,
        *, user_id: str = "",
    ) -> None:
        """Register an in-progress workflow for status tracking."""
        from datetime import datetime, timezone

        async with self._tracking_lock:
            self._workflow_threads[job_id] = {
                "thread_id": thread_id,
                "workflow_type": workflow_type,
                "user_id": user_id,
                "created_at": datetime.now(tz=timezone.utc),
            }

    async def unregister_workflow(self, job_id: str) -> None:
        """Remove a completed workflow from tracking."""
        async with self._tracking_lock:
            self._workflow_threads.pop(job_id, None)

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
    from src.agents.dispatcher import WorkflowDispatcher
    from src.agents.preparation_workflow import create_preparation_workflow
    from src.agents.retry_workflow import create_retry_workflow
    from src.bridge import SessionStore, WsRelay
    from src.services.alerts import AdminAlertService
    from src.services.auth.auth import AuthService
    from src.services.auth.magic_link_repository import MagicLinkRepository
    from src.services.auth.user_repository import UserRepository
    from src.services.auth.user_service import UserService
    from src.services.cv.pdf_extraction import CVExtractionRegistry
    from src.services.db.job_repository import get_repository
    from src.services.jobs.hitl_processor import HITLProcessor
    from src.services.jobs.job_orchestrator import JobOrchestrator
    from src.services.jobs.job_queue import ConsumerManager, JobQueue
    from src.services.notifications.notification_repository import NotificationRepository

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
    magic_link_repository = MagicLinkRepository()
    user_service = UserService(user_repository)
    auth_service = AuthService(settings, user_repository, magic_link_repository)
    admin_alert_service = AdminAlertService(settings)

    session_store = SessionStore()
    ws_relay = WsRelay(session_store, auth_service)

    ctx = AppContext(
        repository=repository,
        settings=settings,
        prep_workflow=prep_workflow,
        retry_workflow=retry_workflow,
        user_repository=user_repository,
        magic_link_repository=magic_link_repository,
        user_service=user_service,
        auth_service=auth_service,
        admin_alert_service=admin_alert_service,
        job_queue=job_queue,
        notification_repository=NotificationRepository(),
        cv_extraction_registry=CVExtractionRegistry(),
        consumer_manager=ConsumerManager(),
        session_store=session_store,
        ws_relay=ws_relay,
    )

    # Wire domain services (they need the full context)
    ctx.workflow_dispatcher = WorkflowDispatcher(ctx)
    ctx.orchestrator = JobOrchestrator(ctx)
    ctx.hitl_processor = HITLProcessor(ctx)

    return ctx
