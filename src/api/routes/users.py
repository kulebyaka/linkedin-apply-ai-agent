"""User profile endpoints: settings, master CV upload, search/filter prefs."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from src.api.deps import CurrentUser, get_ctx
from src.config.settings import get_settings
from src.models.job_filter import (
    GeneratePromptRequest,
    RefinementProposal,
    UserFilterPreferences,
    apply_learned_block,
    extract_learned_block,
)
from src.services.jobs.refinement import NOTIFICATION_TYPE
from src.models.pdf_extraction import (
    CVExtractionStartResponse,
    CVExtractionStatusResponse,
)
from src.models.user import (
    User,
    UserSearchPreferences,
    UserUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.put("/api/users/me", response_model=User)
async def update_user_profile(
    body: UserUpdateRequest,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's profile (display_name, master_cv_json, search_preferences)."""
    ctx = get_ctx(request)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return user
    return await ctx.user_repository.update(user.id, updates)


def _resolve_cv_model_choice(user: User) -> tuple[str, str | None]:
    """Pick (provider, model) for CV extraction.

    Reuses the user's cv_generation model preference; falls back to the
    global default provider when no per-user preference is set. Returns
    (provider, model_or_None) — None defers model selection to the
    server-side default for that provider.
    """
    if user.model_preferences and user.model_preferences.cv_generation:
        choice = user.model_preferences.cv_generation
        return choice.provider, choice.model
    return get_settings().primary_llm_provider, None


@router.post(
    "/api/users/me/master-cv/extract",
    response_model=CVExtractionStartResponse,
    status_code=202,
)
async def start_cv_extraction(
    request: Request,
    user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
) -> CVExtractionStartResponse:
    """Kick off background AI extraction of a CV from an uploaded PDF.

    Validates MIME, provider capability, and the in-flight guard before
    reading the body, then validates size and page count once we have the
    bytes. Returns immediately with an extraction_id the client can poll
    via the status endpoint.
    """
    # Local imports: src.agents._shared pulls in WeasyPrint, which needs
    # native libs we don't want to require at import time.
    from src.agents._shared import create_llm_client
    from src.llm.provider import LLMClientFactory, LLMProvider
    from src.services.cv.pdf_extraction import run_extraction

    settings = get_settings()
    ctx = get_ctx(request)
    if ctx.cv_extraction_registry is None:
        raise HTTPException(500, "CV extraction registry not initialized")

    filename = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()
    if not (content_type == "application/pdf" or filename.endswith(".pdf")):
        raise HTTPException(400, "File must be a PDF")

    provider_str, model_override = _resolve_cv_model_choice(user)
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        raise HTTPException(
            400,
            "Configured CV model provider is unknown. "
            "Update Settings → Model preferences.",
        ) from None

    if not LLMClientFactory.supports_pdf(provider):
        raise HTTPException(
            400,
            "PDF extraction requires Anthropic Claude or OpenAI GPT-4. "
            "Update your CV composition model in Settings → Model preferences.",
        )

    task = await ctx.cv_extraction_registry.create_if_not_in_flight(user.id)
    if task is None:
        raise HTTPException(409, "An extraction is already in progress")

    pdf_bytes = await file.read()
    size = len(pdf_bytes)
    max_bytes = settings.pdf_cv_upload_max_bytes
    if size == 0:
        await ctx.cv_extraction_registry.update(
            task.id, status="failed", error_message="Uploaded PDF is empty"
        )
        raise HTTPException(400, "Uploaded PDF is empty")
    if size > max_bytes:
        await ctx.cv_extraction_registry.update(
            task.id, status="failed",
            error_message=f"File exceeds {max_bytes // (1024 * 1024)}MB limit",
        )
        raise HTTPException(
            400, f"File exceeds {max_bytes // (1024 * 1024)}MB limit"
        )

    try:
        from io import BytesIO

        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        reader = PdfReader(BytesIO(pdf_bytes))
        page_count = len(reader.pages)
    except PdfReadError as e:
        logger.warning("Could not parse uploaded PDF: %s", e)
        await ctx.cv_extraction_registry.update(
            task.id, status="failed", error_message="PDF could not be parsed",
        )
        raise HTTPException(400, "Could not read PDF — file may be corrupt") from None

    max_pages = settings.pdf_cv_upload_max_pages
    if page_count > max_pages:
        await ctx.cv_extraction_registry.update(
            task.id, status="failed",
            error_message=f"PDF exceeds {max_pages}-page limit",
        )
        raise HTTPException(400, f"PDF exceeds {max_pages}-page limit")
    if page_count == 0:
        await ctx.cv_extraction_registry.update(
            task.id, status="failed", error_message="PDF has no pages",
        )
        raise HTTPException(400, "PDF has no pages")

    try:
        llm_client = create_llm_client(provider_str, model_override)
    except ValueError as e:
        await ctx.cv_extraction_registry.update(
            task.id, status="failed", error_message=str(e),
        )
        raise HTTPException(400, str(e)) from None

    logger.info(
        "PDF extraction queued: user=%s task=%s file=%s size=%d pages=%d "
        "provider=%s model=%s",
        user.id, task.id, file.filename, size, page_count,
        provider_str, llm_client.model,
    )

    ctx.create_background_task(
        run_extraction(task, pdf_bytes, llm_client, ctx.cv_extraction_registry)
    )

    return CVExtractionStartResponse(extraction_id=task.id, status="pending")


@router.get(
    "/api/users/me/master-cv/extract/{extraction_id}",
    response_model=CVExtractionStatusResponse,
)
async def get_cv_extraction_status(
    extraction_id: str,
    request: Request,
    user: CurrentUser,
) -> CVExtractionStatusResponse:
    """Poll for the status/result of a PDF extraction task."""
    ctx = get_ctx(request)
    if ctx.cv_extraction_registry is None:
        raise HTTPException(500, "CV extraction registry not initialized")

    task = await ctx.cv_extraction_registry.get(extraction_id)
    if task is None:
        raise HTTPException(404, "Extraction not found")
    if task.user_id != user.id:
        raise HTTPException(403, "Not authorized to read this extraction")

    return CVExtractionStatusResponse(
        extraction_id=task.id,
        status=task.status,
        result_json=task.result_json,
        validation_errors=list(task.validation_errors),
        error_message=task.error_message,
    )


@router.get("/api/users/me/search-preferences")
async def get_search_preferences(
    request: Request,
    user: CurrentUser,
):
    """Get current user's LinkedIn search preferences."""
    if user.search_preferences is None:
        return UserSearchPreferences().model_dump()
    return user.search_preferences.model_dump()


@router.put("/api/users/me/search-preferences", response_model=User)
async def update_search_preferences(
    prefs: UserSearchPreferences,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's LinkedIn search preferences."""
    ctx = get_ctx(request)
    return await ctx.user_repository.update(user.id, {"search_preferences": prefs})


@router.get("/api/users/me/filter-preferences", response_model=UserFilterPreferences)
async def get_filter_preferences(
    request: Request,
    user: CurrentUser,
):
    """Get current user's job filter preferences."""
    if user.filter_preferences is None:
        return UserFilterPreferences()
    return user.filter_preferences


@router.put("/api/users/me/filter-preferences", response_model=User)
async def update_filter_preferences(
    prefs: UserFilterPreferences,
    request: Request,
    user: CurrentUser,
) -> User:
    """Update current user's job filter preferences."""
    ctx = get_ctx(request)
    return await ctx.user_repository.update(user.id, {"filter_preferences": prefs})


class RefinementView(BaseModel):
    """The pending refinement proposal plus the current learned block."""

    proposal: RefinementProposal | None = None
    current_learned_block: str | None = None


@router.get("/api/users/me/filter-preferences/refinement", response_model=RefinementView)
async def get_filter_refinement(
    request: Request,
    user: CurrentUser,
) -> RefinementView:
    """Return the user's pending filter-refinement proposal (or null)."""
    ctx = get_ctx(request)
    proposal = await ctx.user_repository.get_pending_proposal(user.id)
    current_block = None
    if user.filter_preferences:
        current_block = extract_learned_block(user.filter_preferences.custom_prompt)
    return RefinementView(proposal=proposal, current_learned_block=current_block)


async def _consume_proposal(ctx, user, proposal: RefinementProposal) -> None:
    """Clear the proposal, consume its signals, and clear its notification."""
    await ctx.user_repository.clear_pending_proposal(user.id)
    try:
        await ctx.repository.mark_refine_signals(proposal.signal_job_ids, "consumed")
    except Exception:
        logger.warning("Failed to mark refine signals consumed", exc_info=True)
    if ctx.notification_repository is not None:
        try:
            await ctx.notification_repository.mark_read_by_type(user.id, NOTIFICATION_TYPE)
        except Exception:
            logger.warning("Failed to clear refinement notification", exc_info=True)


@router.post("/api/users/me/filter-preferences/refinement/accept", response_model=User)
async def accept_filter_refinement(
    request: Request,
    user: CurrentUser,
) -> User:
    """Apply the proposed auto-learned block to ``custom_prompt`` and clear the proposal.

    Only the delimited auto-learned region is rewritten; the user's hand-written
    prompt is preserved verbatim.
    """
    ctx = get_ctx(request)
    proposal = await ctx.user_repository.get_pending_proposal(user.id)
    if proposal is None:
        raise HTTPException(404, "No pending refinement proposal")

    prefs = user.filter_preferences or UserFilterPreferences()
    new_custom_prompt = apply_learned_block(
        prefs.custom_prompt, proposal.proposed_learned_block
    )
    updated_prefs = prefs.model_copy(update={"custom_prompt": new_custom_prompt})

    updated_user = await ctx.user_repository.update(
        user.id, {"filter_preferences": updated_prefs}
    )
    await _consume_proposal(ctx, user, proposal)
    logger.info("Filter refinement accepted for user %s", user.id)
    return updated_user


@router.post("/api/users/me/filter-preferences/refinement/reject")
async def reject_filter_refinement(
    request: Request,
    user: CurrentUser,
) -> dict:
    """Discard the pending proposal without changing the filter prompt."""
    ctx = get_ctx(request)
    proposal = await ctx.user_repository.get_pending_proposal(user.id)
    if proposal is None:
        raise HTTPException(404, "No pending refinement proposal")
    await _consume_proposal(ctx, user, proposal)
    logger.info("Filter refinement rejected for user %s", user.id)
    return {"status": "rejected"}


@router.post("/api/users/me/filter-preferences/generate-prompt")
async def generate_filter_prompt(
    body: GeneratePromptRequest,
    request: Request,
    user: CurrentUser,
):
    """Generate a structured filter prompt from natural language preferences."""
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
            user.id,
        )
        return {"prompt": prompt}
    except JobFilterError as e:
        raise HTTPException(500, f"Failed to generate prompt: {e}") from None
    except ValueError as e:
        raise HTTPException(503, f"LLM not configured: {e}") from None
    except Exception as e:
        logger.error(f"Failed to generate filter prompt: {e}", exc_info=True)
        raise HTTPException(500, "Failed to generate filter prompt") from None
