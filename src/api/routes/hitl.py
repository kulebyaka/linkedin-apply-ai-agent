"""HITL (Human-in-the-Loop) endpoints: pending review queue, decisions, history."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.api.deps import CurrentUser, get_hitl_processor
from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    PendingApproval,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/hitl/pending", response_model=list[PendingApproval])
async def get_hitl_pending(request: Request, user: CurrentUser) -> list[PendingApproval]:
    """Get all jobs pending HITL review for the authenticated user."""
    try:
        hitl = get_hitl_processor(request)
        return await hitl.get_pending(user.id)
    except Exception as e:
        logger.error(f"Failed to get pending jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get pending jobs") from None


@router.post("/api/hitl/{job_id}/decide", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    job_id: str, decision: HITLDecision, request: Request, user: CurrentUser
) -> HITLDecisionResponse:
    """Submit HITL decision for a pending job."""
    try:
        hitl = get_hitl_processor(request)
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


@router.get("/api/hitl/history", response_model=list[ApplicationHistoryItem])
async def get_application_history(
    request: Request, user: CurrentUser, limit: int = 50, status: str | None = None
) -> list[ApplicationHistoryItem]:
    """Get application history for the authenticated user."""
    try:
        hitl = get_hitl_processor(request)
        return await hitl.get_history(user.id, limit=limit, status=status)
    except Exception as e:
        logger.error(f"Failed to get application history: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get history") from None
