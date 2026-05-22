"""Admin endpoints (require role=admin). Job audit, queue/scheduler ops, user mgmt."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.api.deps import AdminUser, get_ctx, normalize_query_datetime
from src.context import AppContext
from src.models.state_machine import BusinessState
from src.models.user import User, UserRole
from src.services.auth.user_service import LastAdminError

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_user_summary(u: User) -> dict:
    """Compact user dict for admin endpoints."""
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role.value if hasattr(u.role, "value") else str(u.role),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "updated_at": u.updated_at.isoformat() if u.updated_at else None,
    }


@router.get("/api/admin/jobs")
async def admin_list_jobs(
    request: Request,
    admin: AdminUser,
    user_id: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List jobs across all users with optional filters."""
    ctx = get_ctx(request)
    created_from = normalize_query_datetime(created_from)
    created_to = normalize_query_datetime(created_to)
    items = await ctx.repository.list_all_jobs(
        user_ids=user_id,
        statuses=status,
        sources=source,
        created_from=created_from,
        created_to=created_to,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await ctx.repository.count_all_jobs(
        user_ids=user_id,
        statuses=status,
        sources=source,
        created_from=created_from,
        created_to=created_to,
        search=search,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/admin/jobs/{job_id}")
async def admin_get_job(
    job_id: str, request: Request, admin: AdminUser
) -> dict:
    """Return full job detail for any user. 404 if missing."""
    ctx = get_ctx(request)
    job = await ctx.repository.get(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return job.model_dump(mode="json")


@router.post("/api/admin/jobs/{job_id}/retry")
async def admin_retry_job(
    job_id: str, request: Request, admin: AdminUser
) -> dict:
    """Retry a failed job: transitions to queued and re-runs the workflow."""
    ctx = get_ctx(request)
    existing = await ctx.repository.get(job_id)
    if existing is None:
        raise HTTPException(404, f"Job {job_id} not found")
    if not existing.raw_input:
        raise HTTPException(409, "Job has no raw_input — cannot retry")

    # try_claim_failed_for_retry is the cross-worker guard; the lock is a
    # single-worker fast-path so duplicate retries don't both enqueue.
    async with ctx.admin_retry_lock:
        updated = await ctx.repository.try_claim_failed_for_retry(job_id)
        if updated is None:
            current = await ctx.repository.get(job_id)
            current_status = current.status if current else "missing"
            raise HTTPException(
                409,
                f"Job not retriable in status '{current_status}' (must be 'failed')",
            )

        ctx.create_background_task(_run_admin_retry(ctx, job_id))
    return updated.model_dump(mode="json")


async def _run_admin_retry(ctx: AppContext, job_id: str) -> None:
    """Re-invoke the preparation workflow for an admin-retried job."""
    import time as _time

    job = await ctx.repository.get(job_id)
    if job is None:
        return

    master_cv = None
    cv_provider: str | None = None
    cv_model: str | None = None
    if job.user_id and ctx.user_repository is not None:
        try:
            user = await ctx.user_repository.get_by_id(job.user_id)
            if user and user.master_cv_json:
                master_cv = user.master_cv_json
            if user and user.model_preferences and user.model_preferences.cv_generation:
                cv_provider = user.model_preferences.cv_generation.provider
                cv_model = user.model_preferences.cv_generation.model
        except Exception:
            logger.warning("Failed to load user %s for admin retry", job.user_id)

    if master_cv is None:
        # Do not fall back to the global filesystem CV — that would silently
        # tailor a CV for user X using whoever's master_cv.json happens to be
        # on the server. Fail the retry explicitly instead.
        logger.error(
            "Admin retry for job %s: no master CV available for user %s",
            job_id, job.user_id,
        )
        try:
            await ctx.repository.update(
                job_id,
                {
                    "status": BusinessState.FAILED,
                    "error_message": "Admin retry failed: user has no master CV",
                },
            )
        except Exception:
            logger.warning("Could not mark job %s FAILED for missing master CV", job_id)
        return

    raw_input = dict(job.raw_input or {})
    if cv_provider:
        raw_input["llm_provider"] = cv_provider
    if cv_model:
        raw_input["llm_model"] = cv_model

    initial_state = {
        "job_id": job_id,
        "user_id": job.user_id or "",
        "source": job.source,
        "mode": job.mode,
        "raw_input": raw_input,
        "master_cv": master_cv,
        "current_step": BusinessState.QUEUED,
        "retry_count": 0,
        "filter_result": None,
        "user_feedback": None,
        "error_message": None,
    }
    thread_id = f"admin-retry-{job_id}-{int(_time.time())}"

    dispatcher = ctx.workflow_dispatcher
    if dispatcher is None:
        logger.error("workflow_dispatcher not initialized; cannot run admin retry")
        return
    await dispatcher.dispatch_preparation(
        job_id=job_id,
        thread_id=thread_id,
        initial_state=initial_state,
        user_id=job.user_id or "",
    )


@router.delete("/api/admin/jobs/{job_id}")
async def admin_delete_job(
    job_id: str, request: Request, admin: AdminUser
) -> dict:
    """Cascade-delete any job and unlink its PDFs."""
    ctx = get_ctx(request)
    deleted = await ctx.repository.delete_cascade(job_id)
    if not deleted:
        raise HTTPException(404, f"Job {job_id} not found")
    return {"deleted": True, "job_id": job_id}


@router.post("/api/admin/jobs/bulk-delete")
async def admin_bulk_delete_jobs(
    request: Request,
    admin: AdminUser,
    body: Annotated[dict, Body(...)],
) -> dict:
    """Bulk-delete up to 100 jobs by ID. Returns counts + failures."""
    job_ids = body.get("job_ids")
    if not isinstance(job_ids, list) or not job_ids:
        raise HTTPException(400, "job_ids must be a non-empty list")
    if len(job_ids) > 100:
        raise HTTPException(400, "Cannot delete more than 100 jobs at once")
    if not all(isinstance(j, str) and j for j in job_ids):
        raise HTTPException(400, "job_ids must be a list of non-empty strings")

    ctx = get_ctx(request)
    deleted = 0
    failed: list[str] = []
    for jid in job_ids:
        try:
            ok = await ctx.repository.delete_cascade(jid)
            if ok:
                deleted += 1
            else:
                failed.append(jid)
        except Exception:
            logger.warning("Failed to delete job %s in bulk-delete", jid, exc_info=True)
            failed.append(jid)
    return {"deleted": deleted, "failed": failed}


@router.get("/api/admin/queue")
async def admin_get_queue(request: Request, admin: AdminUser) -> dict:
    """Return queue, consumer, and scheduler state for the admin dashboard."""
    ctx = get_ctx(request)
    if ctx.consumer_manager is None:
        raise HTTPException(500, "Consumer manager not initialized")

    snapshot = ctx.consumer_manager.snapshot()
    scheduler_state: list[dict] = []
    if ctx.scheduler is not None:
        try:
            scheduler_state = ctx.scheduler.get_jobs_state()
        except Exception:
            logger.warning("Failed to read scheduler state", exc_info=True)

    counts_24h = await ctx.repository.count_by_status_global(window_hours=24)
    counts_7d = await ctx.repository.count_by_status_global(window_hours=168)
    counts_all = await ctx.repository.count_by_status_global()
    return {
        "consumer": snapshot,
        "scheduler": scheduler_state,
        "counts": {
            "last_24h": counts_24h,
            "last_7d": counts_7d,
            "all_time": counts_all,
        },
    }


@router.post("/api/admin/scheduler/run/{user_id}")
async def admin_run_scheduler(
    user_id: str, request: Request, admin: AdminUser
) -> dict:
    """Manually fire a LinkedIn search for the given user."""
    ctx = get_ctx(request)
    if ctx.scheduler is None:
        raise HTTPException(503, "Scheduler not initialized")
    if ctx.user_repository is None:
        raise HTTPException(500, "User repository not initialized")

    target_user = await ctx.user_repository.get_by_id(user_id)
    if target_user is None:
        raise HTTPException(404, f"User {user_id} not found")
    if not target_user.search_preferences:
        raise HTTPException(
            409, f"User {user_id} has no LinkedIn search preferences configured"
        )

    if ctx.scheduler.search_in_progress:
        raise HTTPException(
            409,
            "Another LinkedIn search is already in progress — try again shortly",
        )

    async def _run() -> None:
        try:
            count = await ctx.scheduler.run_search(user_id=user_id)
            logger.info(
                "Admin-triggered LinkedIn search for user=%s: %d jobs", user_id, count
            )
        except Exception:
            logger.exception(
                "Admin-triggered LinkedIn search failed for user=%s", user_id
            )

    ctx.create_background_task(_run())
    return {"status": "started", "user_id": user_id}


@router.get("/api/admin/errors")
async def admin_list_errors(
    request: Request,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    since: Annotated[datetime | None, Query()] = None,
) -> dict:
    """List jobs with non-null error_message or last_scrape_error."""
    ctx = get_ctx(request)
    since = normalize_query_datetime(since)
    items = await ctx.repository.list_jobs_with_errors(
        limit=limit, offset=offset, since=since
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/admin/users")
async def admin_list_users(
    request: Request,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List users with per-user job counts and last_job_at."""
    ctx = get_ctx(request)
    if ctx.user_repository is None:
        raise HTTPException(500, "User repository not initialized")

    users = await ctx.user_repository.list_all_users(limit=limit, offset=offset)

    async def _per_user(u):
        last_job_task = ctx.repository.list_all_jobs(
            user_ids=[u.id], limit=1, offset=0
        )
        counts_task = ctx.repository.get_status_counts(u.id)
        all_jobs, counts = await asyncio.gather(
            last_job_task, counts_task, return_exceptions=True,
        )
        if isinstance(counts, BaseException):
            counts = {}
        if isinstance(all_jobs, BaseException):
            all_jobs = []
        last_job_at = all_jobs[0].created_at if all_jobs else None
        return {
            "user": _serialize_user_summary(u),
            "job_counts": counts,
            "last_job_at": last_job_at.isoformat() if last_job_at else None,
        }

    out = await asyncio.gather(*(_per_user(u) for u in users)) if users else []
    return {"items": list(out), "limit": limit, "offset": offset}


@router.put("/api/admin/users/{user_id}/role")
async def admin_set_user_role(
    user_id: str,
    request: Request,
    admin: AdminUser,
    body: Annotated[dict, Body(...)],
) -> dict:
    """Change a user's role. Refuses to demote the last remaining admin."""
    role_raw = body.get("role")
    try:
        target_role = UserRole(role_raw)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid role: {role_raw!r}") from None

    ctx = get_ctx(request)
    if ctx.user_service is None:
        raise HTTPException(500, "User service not initialized")

    async with ctx.admin_role_lock:
        try:
            updated = await ctx.user_service.set_role(user_id, target_role)
        except KeyError:
            raise HTTPException(404, f"User {user_id} not found") from None
        except LastAdminError as exc:
            raise HTTPException(409, str(exc)) from None
    return _serialize_user_summary(updated)
