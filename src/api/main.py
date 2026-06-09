"""FastAPI application entrypoint.

Composes routers from src/api/routes/ into the FastAPI app. Owns:
- Settings loading and logger setup.
- Application lifespan (AppContext creation, scheduler bootstrap, shutdown).
- CORS + request logging middleware.
- Static file mount for the SvelteKit UI build output.

Endpoint implementations live in src/api/routes/{auth,users,jobs,hitl,admin,system}.py.
Shared FastAPI dependencies (get_current_user, AdminUser, etc.) live in
src/api/deps.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.deps import (
    AdminUser,
    CurrentUser,
    OptionalUser,
    get_admin_user,
    get_ctx,
    get_current_user,
    get_hitl_processor,
    get_optional_user,
    get_orchestrator,
)
from src.api.routes import admin, auth, hitl, jobs, notifications, system, users
from src.config.settings import get_settings
from src.context import AppContext, create_app_context
from src.utils.logger import setup_api_logger

settings = get_settings()
logger = setup_api_logger(level="INFO")

# Ensure all src.* loggers propagate to a handler (scheduler, scraper, etc.)
_src_logger = logging.getLogger("src")
if not _src_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _src_logger.addHandler(_handler)
    _src_logger.setLevel(logging.INFO)


# Back-compat re-exports — tests historically imported these from src.api.main.
__all__ = [
    "AdminUser",
    "AppContext",
    "CurrentUser",
    "OptionalUser",
    "app",
    "get_admin_user",
    "get_ctx",
    "get_current_user",
    "get_hitl_processor",
    "get_optional_user",
    "get_orchestrator",
    "lifespan",
]


# Back-compat private alias — used by a few older tests / helpers.
_get_ctx = get_ctx


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create AppContext, initialize, yield, cleanup."""
    ctx = create_app_context(settings)
    app.state.ctx = ctx

    if settings.dev_auth_bypass:
        if not (
            settings.app_url.startswith("http://localhost")
            or settings.app_url.startswith("http://127.0.0.1")
        ):
            raise RuntimeError(
                "SECURITY: DEV_AUTH_BYPASS=true but APP_URL is not localhost "
                f"({settings.app_url!r}). The /api/auth/dev-login endpoint mints "
                "session JWTs without verification and must never be reachable "
                "outside local development."
            )
        logger.warning(
            "DEV_AUTH_BYPASS enabled — POST /api/auth/dev-login will mint a JWT "
            "for %s without email verification. Disable in production.",
            settings.dev_auth_email,
        )

    logger.info(f"Starting up with repository type: {settings.repo_type}")
    await ctx.repository.initialize()
    # Ensure user tables are initialized (SQLiteJobRepository already does this,
    # but InMemoryJobRepository doesn't set up the Piccolo engine for user tables)
    if ctx.user_repository:
        await ctx.user_repository.initialize(db_path=settings.db_path)
    logger.info("Repository initialized successfully")

    async def _cleanup_magic_links_loop():
        while True:
            if ctx.magic_link_repository:
                try:
                    deleted = await ctx.magic_link_repository.cleanup_expired_magic_links()
                    if deleted:
                        logger.info("Cleaned up %d expired magic link tokens", deleted)
                except Exception:
                    logger.warning("Failed to clean up expired magic link tokens", exc_info=True)
            await asyncio.sleep(86400)  # 24 hours

    ctx.create_background_task(_cleanup_magic_links_loop())

    # Start the auto-refining filter scheduler (independent of LinkedIn search).
    # Per-user opt-in (default OFF) still gates whether each user is processed.
    if settings.auto_refine_enabled and ctx.user_repository is not None:
        try:
            from src.services.jobs.refinement_scheduler import RefinementScheduler

            refinement_scheduler = RefinementScheduler(ctx)
            refinement_scheduler.start()
            ctx.refinement_scheduler = refinement_scheduler
        except Exception:
            logger.exception("Failed to start refinement scheduler")

    if settings.seed_jobs_from_file:
        logger.info(
            "Fixture replay mode enabled — LinkedIn scraping disabled. "
            "Loading jobs from %s", settings.scraped_jobs_path,
        )
        from src.services.jobs.job_fixtures import enqueue_from_fixtures

        result = await enqueue_from_fixtures(
            settings.scraped_jobs_path,
            ctx.job_queue,
            repository=ctx.repository,
            limit=settings.seed_jobs_limit,
        )
        logger.info(
            "Fixture replay: enqueued=%d, skipped=%d, total_in_file=%d",
            result["enqueued"], result["skipped"], result["total_in_file"],
        )
        if result["enqueued"] > 0 and ctx.consumer_manager is not None:
            ctx.consumer_manager.start(ctx)
    elif settings.linkedin_search_schedule_enabled:
        try:
            from src.services.jobs.scheduler import LinkedInSearchScheduler
            from src.services.linkedin.browser_automation import LinkedInAutomation
            from src.services.linkedin.linkedin_scraper import LinkedInJobScraper

            browser = LinkedInAutomation(settings)
            await browser.initialize()
            ctx.browser = browser

            scraper = LinkedInJobScraper(browser, settings)
            scheduler = LinkedInSearchScheduler(
                settings, scraper, ctx.job_queue,
                user_repository=ctx.user_repository,
                admin_alert_service=ctx.admin_alert_service,
                job_repository=ctx.repository,
            )
            scheduler.start()
            ctx.scheduler = scheduler

            if ctx.consumer_manager is not None:
                ctx.consumer_manager.start(ctx)

            logger.info("LinkedIn search scheduler started")
        except Exception:
            logger.exception("Failed to start LinkedIn search scheduler")

    # Recover any jobs left in non-terminal states from a prior process.
    # Best-effort: failures must not block app startup.
    try:
        from src.services.jobs.recovery import recover_in_flight_jobs
        report = await recover_in_flight_jobs(ctx)
        # If recovery re-enqueued anything but the consumer wasn't started
        # (e.g. scheduler disabled), spin it up so the recovered work
        # actually runs.
        if (
            report.recovered > 0
            and ctx.consumer_manager is not None
            and (ctx.consumer_manager.task is None or ctx.consumer_manager.task.done())
        ):
            ctx.consumer_manager.start(ctx)
            logger.info("Started consumer to process recovered jobs")
    except Exception:
        logger.exception("Startup recovery raised — continuing without it")

    yield

    if ctx.consumer_manager is not None:
        ctx.consumer_manager.stop()
        await ctx.consumer_manager.wait_stopped()

    if ctx.scheduler:
        ctx.scheduler.stop()

    if ctx.refinement_scheduler:
        ctx.refinement_scheduler.stop()

    if ctx.browser:
        await ctx.browser.close()

    logger.info("Shutting down repository...")
    await ctx.repository.close()
    logger.info("Repository closed")


app = FastAPI(
    title="LinkedIn Job Application Agent API",
    description="API for Human-in-the-Loop job application review",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all API requests with method, path, status, and duration."""
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} ({duration_ms:.1f}ms)"
    )
    return response


# Route inclusion order matters: LinkedIn search routes must precede the
# wildcard /api/jobs/{job_id}/* paths, which is preserved within jobs.py.
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(jobs.router)
app.include_router(hitl.router)
app.include_router(notifications.router)
app.include_router(admin.router)


# Static file serving — MUST be the last mount so it doesn't shadow API routes.
UI_BUILD_PATH = Path(__file__).parent.parent.parent / "ui" / "build"
if UI_BUILD_PATH.exists():
    app.mount("/", StaticFiles(directory=str(UI_BUILD_PATH), html=True), name="ui")
    logger.info(f"Mounted UI at / from {UI_BUILD_PATH}")
else:
    logger.warning(
        f"UI build directory not found at {UI_BUILD_PATH}. "
        "Run 'cd ui && npm run build' to build the UI."
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
