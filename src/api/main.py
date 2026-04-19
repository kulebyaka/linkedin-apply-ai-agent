"""FastAPI application for HITL UI and job submission.

Provides unified endpoints (/api/jobs/*, /api/hitl/*) for the two-workflow pipeline.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import get_settings
from src.context import AppContext, create_app_context
from src.models.job_filter import GeneratePromptRequest, UserFilterPreferences
from src.models.state_machine import BusinessState, WorkflowStep
from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    PendingApproval,
)
from src.models.user import (
    AuthResponse,
    LoginRequest,
    LoginResponse,
    User,
    UserSearchPreferences,
    UserUpdateRequest,
)
from src.services.jobs.hitl_processor import HITLProcessor
from src.services.jobs.job_orchestrator import JobOrchestrator
from src.services.jobs.job_queue import ConsumerManager
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

# Module-level state for consumer management (not business state)
_consumer_manager = ConsumerManager()
_linkedin_init_lock = asyncio.Lock()

def _get_ctx(request: Request) -> AppContext:
    """Helper to retrieve AppContext from request."""
    return request.app.state.ctx


async def get_current_user(
    request: Request,
    auth_token: str | None = Cookie(default=None),
) -> User:
    """FastAPI dependency: require authenticated user.

    Reads JWT from the auth_token cookie, decodes it, and looks up
    the user in the repository. Raises 401 if not authenticated.
    """
    if not auth_token:
        raise HTTPException(401, "Not authenticated")

    ctx = _get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")
    if ctx.user_repository is None:
        raise HTTPException(500, "User repository not initialized")

    try:
        claims = ctx.auth_service.decode_jwt(auth_token)
    except ValueError:
        raise HTTPException(401, "Invalid or expired token") from None

    user = await ctx.user_repository.get_by_id(claims["user_id"])
    if user is None:
        raise HTTPException(401, "User not found")

    return user


async def get_optional_user(
    request: Request,
    auth_token: str | None = Cookie(default=None),
) -> User | None:
    """FastAPI dependency: optionally authenticated user.

    Returns User if valid auth cookie present, None otherwise.
    Does not raise 401 — for public endpoints that behave
    differently when authenticated.
    """
    if not auth_token:
        return None

    ctx = _get_ctx(request)
    if ctx.auth_service is None or ctx.user_repository is None:
        return None

    try:
        claims = ctx.auth_service.decode_jwt(auth_token)
    except ValueError:
        return None

    return await ctx.user_repository.get_by_id(claims["user_id"])


# Type aliases for dependency injection (avoids B008 ruff error)
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create AppContext, initialize, yield, cleanup."""
    ctx = create_app_context(settings)
    app.state.ctx = ctx
    app.state.consumer_manager = _consumer_manager

    logger.info(f"Starting up with repository type: {settings.repo_type}")
    await ctx.repository.initialize()
    # Ensure user tables are initialized (SQLiteJobRepository already does this,
    # but InMemoryJobRepository doesn't set up the Piccolo engine for user tables)
    if ctx.user_repository:
        await ctx.user_repository.initialize(db_path=settings.db_path)
    logger.info("Repository initialized successfully")

    # Schedule periodic cleanup of expired magic link tokens (every 24 hours)
    async def _cleanup_magic_links_loop():
        while True:
            if ctx.user_repository:
                try:
                    deleted = await ctx.user_repository.cleanup_expired_magic_links()
                    if deleted:
                        logger.info("Cleaned up %d expired magic link tokens", deleted)
                except Exception:
                    logger.warning("Failed to clean up expired magic link tokens", exc_info=True)
            await asyncio.sleep(86400)  # 24 hours

    ctx.create_background_task(_cleanup_magic_links_loop())

    # Fixture replay mode: seed jobs from file, skip LinkedIn entirely
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
        if result["enqueued"] > 0:
            _consumer_manager.start(ctx)
    elif settings.linkedin_search_schedule_enabled:
        try:
            from src.services.linkedin.browser_automation import LinkedInAutomation
            from src.services.linkedin.linkedin_scraper import LinkedInJobScraper
            from src.services.jobs.scheduler import LinkedInSearchScheduler

            browser = LinkedInAutomation(settings)
            await browser.initialize()
            ctx.browser = browser

            scraper = LinkedInJobScraper(browser, settings)
            scheduler = LinkedInSearchScheduler(
                settings, scraper, ctx.job_queue,
                user_repository=ctx.user_repository,
            )
            scheduler.start()
            ctx.scheduler = scheduler

            _consumer_manager.start(ctx)

            logger.info("LinkedIn search scheduler started")
        except Exception:
            logger.exception("Failed to start LinkedIn search scheduler")

    yield

    # Shutdown
    _consumer_manager.stop()
    await _consumer_manager.wait_stopped()

    if ctx.scheduler:
        ctx.scheduler.stop()

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

# CORS middleware
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

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_ms = (time.time() - start_time) * 1000

    # Log request details
    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} ({duration_ms:.1f}ms)"
    )

    return response




@app.get("/api/health")
async def health():
    """Health check endpoint with consumer health status."""
    return {
        "status": "running",
        "message": "LinkedIn Job Application Agent API",
        **_consumer_manager.health_check(),
    }


# =============================================================================
# LLM Model Catalog
# =============================================================================


@app.get("/api/llm/models")
async def list_llm_models(
    operation: Annotated[str | None, Query()] = None,
):
    """Return the LLM model catalog, optionally filtered by operation.

    Public endpoint (no auth) — exposes only model names, display names, and
    pricing. Never exposes API keys or user data.
    """
    from src.llm.provider import LLMProvider
    from src.llm.model_catalog import (
        OPERATIONS,
        build_label,
        get_catalog_for_operation,
    )

    if operation is not None and operation not in OPERATIONS:
        raise HTTPException(
            422,
            f"Invalid operation: {operation!r}. "
            f"Must be one of: {', '.join(OPERATIONS)} (or omit for full catalog)",
        )

    entries = get_catalog_for_operation(operation)  # type: ignore[arg-type]

    # Resolve the global .env default so the UI can pre-select it when the
    # user has no stored preference for an operation.
    try:
        default_provider = LLMProvider(settings.primary_llm_provider)
    except ValueError:
        default_provider = LLMProvider.OPENAI
    provider_to_model = {
        LLMProvider.OPENAI: settings.openai_model,
        LLMProvider.DEEPSEEK: settings.deepseek_model,
        LLMProvider.GROK: settings.grok_model,
        LLMProvider.ANTHROPIC: settings.anthropic_model,
    }
    default_model = provider_to_model.get(default_provider, "")

    return {
        "models": [
            {
                "provider": e.provider.value,
                "model": e.model,
                "display_name": e.display_name,
                "label": build_label(e),
                "input_cost_per_1m": e.input_cost_per_1m,
                "output_cost_per_1m": e.output_cost_per_1m,
                "supports_strict_schema": e.supports_strict_schema,
                "supports_json_object": e.supports_json_object,
            }
            for e in entries
        ],
        "default": {
            "provider": default_provider.value,
            "model": default_model,
        },
    }


# =============================================================================
# Authentication Endpoints
# =============================================================================


@app.post("/api/auth/login", response_model=LoginResponse)
async def auth_login(body: LoginRequest, request: Request) -> LoginResponse:
    """Request a magic link login email."""
    ctx = _get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")

    try:
        await ctx.auth_service.send_magic_link(body.email)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None

    return LoginResponse(message="Check your email for a magic link")


@app.get("/api/auth/verify", response_model=AuthResponse)
async def auth_verify(
    request: Request,
    response: Response,
    token: str = Query(...),
) -> AuthResponse:
    """Verify a magic link token and set auth cookie."""
    ctx = _get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")

    try:
        user = await ctx.auth_service.verify_token(token)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    jwt_token = ctx.auth_service.create_jwt(user.id, user.email)

    response.set_cookie(
        key="auth_token",
        value=jwt_token,
        httponly=True,
        max_age=ctx.settings.jwt_expiry_days * 86400,
        samesite="lax",
        secure=ctx.settings.app_url.startswith("https://"),
        path="/",
    )

    return AuthResponse(user=user, message="Logged in successfully")


@app.get("/api/auth/me", response_model=User)
async def auth_me(user: CurrentUser) -> User:
    """Get the currently authenticated user."""
    return user


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Clear auth cookie to log out."""
    ctx = _get_ctx(request)
    response.delete_cookie(
        key="auth_token",
        path="/",
        httponly=True,
        samesite="lax",
        secure=ctx.settings.app_url.startswith("https://"),
    )
    return {"message": "Logged out"}


# =============================================================================
# User Settings Endpoints
# =============================================================================


@app.put("/api/users/me", response_model=User)
async def update_user_profile(
    body: UserUpdateRequest,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's profile (display_name, master_cv_json, search_preferences)."""
    ctx = _get_ctx(request)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return user

    updated = await ctx.user_repository.update(user.id, updates)
    return updated


@app.get("/api/users/me/search-preferences")
async def get_search_preferences(
    request: Request,
    user: CurrentUser,
):
    """Get current user's LinkedIn search preferences."""
    if user.search_preferences is None:
        return UserSearchPreferences().model_dump()
    return user.search_preferences.model_dump()


@app.put("/api/users/me/search-preferences", response_model=User)
async def update_search_preferences(
    prefs: UserSearchPreferences,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's LinkedIn search preferences."""
    ctx = _get_ctx(request)
    updated = await ctx.user_repository.update(user.id, {"search_preferences": prefs})
    return updated


@app.get("/api/users/me/filter-preferences", response_model=UserFilterPreferences)
async def get_filter_preferences(
    request: Request,
    user: CurrentUser,
):
    """Get current user's job filter preferences."""
    if user.filter_preferences is None:
        return UserFilterPreferences()
    return user.filter_preferences


@app.put("/api/users/me/filter-preferences", response_model=User)
async def update_filter_preferences(
    prefs: UserFilterPreferences,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's job filter preferences."""
    ctx = _get_ctx(request)
    updated = await ctx.user_repository.update(user.id, {"filter_preferences": prefs})
    return updated


@app.post("/api/users/me/filter-preferences/generate-prompt")
async def generate_filter_prompt(
    body: GeneratePromptRequest,
    request: Request,
    user: CurrentUser,
):
    """Generate a structured filter prompt from natural language preferences.

    Calls the LLM with a meta-prompt that converts the user's free-text
    description of their preferences into a concrete evaluation prompt.
    Returns the generated prompt string suitable for the filter prompt textarea.
    """
    try:
        from src.agents._shared import create_llm_client
        from src.services.jobs.job_filter import JobFilter, JobFilterError

        provider_override = None
        model_override = None
        if user.model_preferences and user.model_preferences.filter_prompt_generation:
            choice = user.model_preferences.filter_prompt_generation
            provider_override = choice.provider
            model_override = choice.model

        llm_client = create_llm_client(provider_override, model_override)
        job_filter = JobFilter(llm_client)

        prompt = await asyncio.to_thread(
            job_filter.generate_prompt_from_preferences,
            body.natural_language_prefs,
        )
        return {"prompt": prompt}
    except JobFilterError as e:
        raise HTTPException(500, f"Failed to generate prompt: {e}") from None
    except ValueError as e:
        raise HTTPException(503, f"LLM not configured: {e}") from None
    except Exception as e:
        logger.error(f"Failed to generate filter prompt: {e}", exc_info=True)
        raise HTTPException(500, "Failed to generate filter prompt") from None


# =============================================================================
# Job Submission Endpoints
# =============================================================================


def _get_orchestrator(request: Request) -> JobOrchestrator:
    """Helper to retrieve JobOrchestrator from request."""
    ctx = _get_ctx(request)
    if ctx.orchestrator is None:
        raise HTTPException(503, "JobOrchestrator not initialized")
    return ctx.orchestrator


def _get_hitl(request: Request) -> HITLProcessor:
    """Helper to retrieve HITLProcessor from request."""
    ctx = _get_ctx(request)
    if ctx.hitl_processor is None:
        raise HTTPException(503, "HITLProcessor not initialized")
    return ctx.hitl_processor


@app.options("/api/jobs/submit")
async def submit_job_options():
    """Handle CORS preflight for job submission."""
    return {}


@app.post("/api/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(
    job_request: JobSubmitRequest, http_request: Request, user: CurrentUser
) -> JobSubmitResponse:
    """Submit a job for CV generation."""
    try:
        # Load master CV from user's DB record, fall back to filesystem
        master_cv = user.master_cv_json
        if not master_cv:
            from src.agents._shared import load_master_cv
            master_cv = load_master_cv()

        orchestrator = _get_orchestrator(http_request)
        return await orchestrator.submit_job(
            job_request, user.id, master_cv, model_preferences=user.model_preferences
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, "Failed to submit job") from None


# =============================================================================
# LinkedIn Search Endpoints
# (must be defined BEFORE /api/jobs/{job_id} wildcard routes)
# =============================================================================


@app.post("/api/jobs/linkedin-search")
async def trigger_linkedin_search(request: Request, user: CurrentUser):
    """Trigger a LinkedIn search manually.

    Runs search in background and returns immediately.
    """
    ctx = _get_ctx(request)

    if settings.seed_jobs_from_file:
        raise HTTPException(
            409,
            "LinkedIn scraping is disabled in fixture replay mode "
            "(SEED_JOBS_FROM_FILE=true). Use POST /api/jobs/replay-fixtures instead.",
        )

    async with _linkedin_init_lock:
        if ctx.scheduler is None:
            # Create a temporary scheduler for one-off search
            try:
                from src.services.linkedin.browser_automation import LinkedInAutomation
                from src.services.linkedin.linkedin_scraper import LinkedInJobScraper
                from src.services.jobs.scheduler import LinkedInSearchScheduler

                if ctx.browser is None:
                    browser = LinkedInAutomation(settings)
                    await browser.initialize()
                    ctx.browser = browser

                scraper = LinkedInJobScraper(ctx.browser, settings)
                ctx.scheduler = LinkedInSearchScheduler(
                    settings, scraper, ctx.job_queue,
                    user_repository=ctx.user_repository,
                )
            except Exception:
                logger.exception("Failed to initialize LinkedIn search components")
                raise HTTPException(500, "Failed to initialize LinkedIn search components") from None

        # Ensure a queue consumer is running
        cm = _consumer_manager
        if cm.task is None or cm.task.done():
            cm.reset()
            cm.start(ctx)

    requesting_user_id = user.id

    async def _run_search():
        try:
            count = await ctx.scheduler.run_search(user_id=requesting_user_id)
            logger.info("Manual LinkedIn search completed: %d jobs found", count)
        except Exception:
            logger.exception("Manual LinkedIn search failed")

    ctx.create_background_task(_run_search())

    return {"status": "started", "message": "LinkedIn search triggered"}


@app.get("/api/jobs/linkedin-search/status")
async def get_linkedin_search_status(request: Request, user: CurrentUser):
    """Return current scheduler state."""
    ctx = _get_ctx(request)
    queue_size = ctx.job_queue.size() if ctx.job_queue else 0

    if ctx.scheduler is None:
        return {
            "enabled": settings.linkedin_search_schedule_enabled,
            "running": False,
            "last_run_time": None,
            "last_run_jobs": 0,
            "next_run_time": None,
            "queue_size": queue_size,
            "user_last_run": None,
        }

    user_run = ctx.scheduler.get_last_run_for_user(user.id)
    user_last_run = (
        {
            "time": user_run.time.isoformat(),
            "jobs_found": user_run.jobs_found,
            "reason": user_run.reason,
            "search_url": user_run.search_url,
            "message": user_run.message,
        }
        if user_run is not None
        else None
    )

    return {
        "enabled": settings.linkedin_search_schedule_enabled,
        "running": ctx.scheduler.is_running,
        "last_run_time": ctx.scheduler.last_run_time.isoformat()
        if ctx.scheduler.last_run_time
        else None,
        "last_run_jobs": ctx.scheduler.last_run_jobs,
        "next_run_time": ctx.scheduler.next_run_time.isoformat()
        if ctx.scheduler.next_run_time
        else None,
        "queue_size": queue_size,
        "user_last_run": user_last_run,
    }


@app.post("/api/jobs/replay-fixtures")
async def replay_fixtures(request: Request, user: CurrentUser, limit: Annotated[int, Query(ge=0)] = 0):
    """Load scraped jobs from fixture file and enqueue for processing.

    Useful for HITL testing and demos. Jobs already in the repository are skipped.
    """
    ctx = _get_ctx(request)

    from src.services.jobs.job_fixtures import enqueue_from_fixtures

    path = settings.scraped_jobs_path
    result = await enqueue_from_fixtures(
        path,
        ctx.job_queue,
        repository=ctx.repository,
        limit=limit,
        user_id=user.id,
    )

    if result["total_in_file"] == 0:
        raise HTTPException(404, f"Fixture file not found or empty: {path}")

    # Ensure queue consumer is running to process the enqueued jobs
    cm = _consumer_manager
    if result["enqueued"] > 0 and (cm.task is None or cm.task.done()):
        cm.reset()
        cm.start(ctx)

    return {
        "status": "ok",
        **result,
        "source": str(path),
    }


@app.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str, request: Request, user: CurrentUser
) -> JobStatusResponse:
    """Get status of a submitted job."""
    try:
        # Verify ownership
        ctx = _get_ctx(request)
        job_record = await ctx.repository.get_for_user(job_id, user.id)
        if job_record is None:
            # Also check workflow threads for in-progress jobs
            thread_info = await ctx.get_workflow_thread(job_id)
            if thread_info is None or thread_info.get("user_id", "") != user.id:
                raise KeyError(f"Job {job_id} not found")

        orchestrator = _get_orchestrator(request)
        return await orchestrator.get_status(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get job status") from None


@app.get("/api/jobs/{job_id}/pdf")
async def download_job_pdf(job_id: str, request: Request, user: CurrentUser):
    """
    Download generated CV PDF for a job.

    Returns 400 if job not completed or pending.
    Returns 404 if PDF file missing.
    """
    try:
        # Get job status (pass user for ownership check)
        status = await get_job_status(job_id, request, user)

        # Check if job is ready
        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        pdf_ready_statuses = {
            BusinessState.CV_READY,
            BusinessState.PENDING_REVIEW,
            BusinessState.APPROVED,
            BusinessState.RETRYING,
            BusinessState.APPLIED,
            WorkflowStep.PDF_GENERATED,
        }
        if status.status not in pdf_ready_statuses:
            raise HTTPException(400, f"PDF not ready yet (status: {status.status})")

        # Check PDF exists
        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path).resolve()
        allowed_dir = Path(settings.generated_cvs_dir).resolve()
        if not pdf_path.is_relative_to(allowed_dir):
            raise HTTPException(403, "Access denied")

        if not pdf_path.exists():
            raise HTTPException(404, "PDF file not found")

        # Return file
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=pdf_path.name,
            headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download PDF for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to download PDF") from None


@app.get("/api/jobs/{job_id}/html", response_class=HTMLResponse)
async def get_job_cv_html(job_id: str, request: Request, user: CurrentUser) -> HTMLResponse:
    """
    Return rendered HTML CV for a job.

    Uses the same Jinja2 template as PDF generation but returns HTML directly.
    Useful for frontend preview without downloading PDF.
    """
    try:
        ctx = _get_ctx(request)

        # Get job status (pass user for ownership check)
        status = await get_job_status(job_id, request, user)

        # Check if job is ready
        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        cv_ready_statuses = {
            BusinessState.CV_READY,
            BusinessState.PENDING_REVIEW,
            BusinessState.APPROVED,
            BusinessState.RETRYING,
            BusinessState.APPLIED,
            WorkflowStep.PDF_GENERATED,
        }
        if status.status not in cv_ready_statuses:
            raise HTTPException(400, f"CV not ready yet (status: {status.status})")

        # Check CV JSON exists
        if not status.cv_json:
            raise HTTPException(404, "CV JSON not found for this job")

        # Render HTML using existing template system
        from src.services.cv.pdf_generator import PDFGenerator

        # Get template name from job state
        template_name = "compact"  # Default template
        thread_info = await ctx.get_workflow_thread(job_id)
        if thread_info is not None:
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}
            workflow_type = thread_info.get("workflow_type", "preparation")
            if workflow_type == "retry":
                workflow = ctx.retry_workflow
            else:
                workflow = ctx.prep_workflow
            state = workflow.get_state(config).values
            raw_input = state.get("raw_input", {})
            template_name = raw_input.get("template_name") or "compact"

        generator = PDFGenerator(template_name=template_name)
        html = generator.render_html(status.cv_json)

        return HTMLResponse(content=html, media_type="text/html")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get CV HTML for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get CV HTML") from None


# =============================================================================
# HITL (Human-in-the-Loop) Endpoints
# =============================================================================


@app.get("/api/hitl/pending", response_model=list[PendingApproval])
async def get_hitl_pending(request: Request, user: CurrentUser) -> list[PendingApproval]:
    """Get all jobs pending HITL review for the authenticated user."""
    try:
        hitl = _get_hitl(request)
        return await hitl.get_pending(user.id)
    except Exception as e:
        logger.error(f"Failed to get pending jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get pending jobs") from None


@app.post("/api/hitl/{job_id}/decide", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    job_id: str, decision: HITLDecision, request: Request, user: CurrentUser
) -> HITLDecisionResponse:
    """Submit HITL decision for a pending job."""
    try:
        hitl = _get_hitl(request)
        return await hitl.process_decision(job_id, decision, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to process HITL decision for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to process decision") from None


@app.get("/api/hitl/history", response_model=list[ApplicationHistoryItem])
async def get_application_history(
    request: Request, user: CurrentUser, limit: int = 50, status: str | None = None
) -> list[ApplicationHistoryItem]:
    """Get application history for the authenticated user."""
    try:
        hitl = _get_hitl(request)
        return await hitl.get_history(user.id, limit=limit, status=status)
    except Exception as e:
        logger.error(f"Failed to get application history: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get history") from None



# =============================================================================
# Data Cleanup Endpoints
# =============================================================================


@app.delete("/api/jobs/cleanup")
async def cleanup_jobs(
    request: Request,
    user: CurrentUser,
    older_than_days: Annotated[int, Query(ge=1, description="Delete jobs older than this many days")] = 90,
    statuses: Annotated[
        list[str] | None, Query(description="Only delete jobs with these statuses")
    ] = None,
) -> dict:
    """Delete old jobs to prevent database bloat.

    This endpoint removes job records that are older than the specified number of days
    and have one of the specified statuses. Useful for data retention and cleanup.

    Args:
        older_than_days: Delete jobs older than this many days (default: 90, min: 1)
        statuses: Only delete jobs with these statuses (default: ["declined", "failed"])

    Returns:
        {"deleted": int, "message": str}
    """
    deletable_statuses = {
        BusinessState.DECLINED,
        BusinessState.FAILED,
        BusinessState.CV_READY,
        BusinessState.FILTERED_OUT,
    }
    try:
        if statuses is None:
            statuses = ["declined", "failed"]
        if not statuses:
            raise HTTPException(400, "At least one status must be provided")

        deletable_values = {s.value for s in deletable_statuses}
        invalid = set(statuses) - deletable_values
        if invalid:
            raise HTTPException(
                400,
                f"Cannot delete jobs with status: {', '.join(sorted(invalid))}. "
                f"Allowed: {', '.join(sorted(deletable_values))}",
            )

        ctx = _get_ctx(request)
        deleted = await ctx.repository.cleanup(older_than_days, statuses, user_id=user.id)
        return {"deleted": deleted, "message": f"Deleted {deleted} jobs"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to cleanup jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to cleanup jobs") from None


# =============================================================================
# Static File Serving for UI
# =============================================================================

# Mount static files for UI (SvelteKit build output)
# IMPORTANT: This must be the LAST route definition to avoid shadowing API routes
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
