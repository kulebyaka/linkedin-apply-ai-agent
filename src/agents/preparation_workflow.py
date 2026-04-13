"""Preparation Workflow - Job source to CV generation pipeline.

This workflow handles the first half of the two-workflow pipeline:
1. Extract job data from source (URL/manual/LinkedIn)
2. Filter job (LinkedIn only - optional)
3. Compose tailored CV using LLM
4. Generate PDF
5. Save to repository (status="pending" for HITL or "completed" for MVP)

The workflow ends at the HITL boundary. Application is handled by a separate workflow.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ..config.settings import get_settings
from ..models.cv_attempt import CVCompositionAttempt
from ..models.state_machine import BusinessState, WorkflowStep
from ..models.unified import JobRecord
from ..services.job_fixtures import get_cached_llm_response, save_llm_response
from ..services.job_source import JobExtractionError, JobSourceFactory
from ._shared import (
    compose_cv,
    create_llm_client,
    generate_pdf,
    get_repository_from_config,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class PreparationWorkflowState(TypedDict):
    """State structure for Preparation Workflow."""

    # Input
    job_id: str
    user_id: str
    source: Literal["url", "manual", "linkedin"]
    mode: Literal["mvp", "full"]
    raw_input: dict  # URL, manual text, or LinkedIn job data

    # Processing
    job_posting: dict  # Normalized job data
    master_cv: dict
    tailored_cv_json: dict
    tailored_cv_pdf_path: str

    # For retry (passed from Retry Workflow)
    user_feedback: str | None
    retry_count: int

    # Status
    current_step: str
    error_message: str | None




def create_preparation_workflow() -> StateGraph:
    """Create the Preparation Workflow.

    Flow:
        extract_job -> [filter_job (LinkedIn only)] -> compose_cv -> generate_pdf -> save_to_db -> END

    Returns:
        Compiled LangGraph workflow.
    """
    workflow = StateGraph(PreparationWorkflowState)

    # Add nodes
    workflow.add_node("extract_job", extract_job_node)
    workflow.add_node("filter_job", filter_job_node)
    workflow.add_node("compose_cv", compose_cv_node)
    workflow.add_node("generate_pdf", generate_pdf_node)
    workflow.add_node("save_to_db", save_to_db_node)

    # Define flow
    workflow.set_entry_point("extract_job")

    # Conditional routing after extraction
    workflow.add_conditional_edges(
        "extract_job",
        route_after_extract,
        {"filter": "filter_job", "compose": "compose_cv", "error": END},
    )

    workflow.add_edge("filter_job", "compose_cv")
    workflow.add_edge("compose_cv", "generate_pdf")
    workflow.add_edge("generate_pdf", "save_to_db")
    workflow.add_edge("save_to_db", END)

    # Compile with checkpointer
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def route_after_extract(state: PreparationWorkflowState) -> str:
    """Route after job extraction.

    - If error occurred, go to END
    - If LinkedIn source, go to filter_job
    - Otherwise, go directly to compose_cv
    """
    if state.get("error_message"):
        return "error"
    if state.get("source") == "linkedin":
        return "filter"
    return "compose"


# =============================================================================
# Workflow Nodes
# =============================================================================


async def extract_job_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
    """Extract job data from source using appropriate adapter.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with job_posting.
    """
    start_time = time.time()
    job_id = state.get("job_id", "unknown")
    source = state.get("source", "unknown")
    logger.info(f"[TIMING] Starting extract_job_node for {job_id} from source: {source}")
    state["current_step"] = WorkflowStep.EXTRACTING

    try:
        # Extract job data
        raw_input = state.get("raw_input", {})

        # Get LLM provider/model from raw_input if specified
        llm_provider = raw_input.get("llm_provider")
        llm_model = raw_input.get("llm_model")

        # Initialize LLM client for URL extraction (with optional overrides)
        llm_client = create_llm_client(llm_provider, llm_model)

        # Get appropriate adapter
        factory = JobSourceFactory(llm_client=llm_client)
        adapter = factory.get_adapter(source)

        # For manual input, pass through directly (no async extraction needed)
        if source == "manual":
            # Manual adapter just normalizes the input
            job_posting = {
                "id": job_id,
                "title": raw_input.get("title", ""),
                "company": raw_input.get("company", ""),
                "description": raw_input.get("description", ""),
                "requirements": raw_input.get("requirements"),
                "location": raw_input.get("location", "Remote"),
                "url": raw_input.get("url", ""),
                "is_remote": True,
            }
            state["job_posting"] = job_posting
            state["current_step"] = WorkflowStep.JOB_EXTRACTED
            logger.info(f"Manual job data processed for {job_id}")
        else:
            # URL and LinkedIn extraction
            job_posting = await adapter.extract(raw_input)
            state["job_posting"] = job_posting
            state["current_step"] = WorkflowStep.JOB_EXTRACTED

    except JobExtractionError as e:
        logger.error(f"Job extraction failed for {job_id}: {e}")
        state["error_message"] = f"Job extraction failed: {e.message}"
        state["current_step"] = BusinessState.FAILED
    except NotImplementedError as e:
        logger.error(f"Job extraction not implemented for source '{source}': {e}")
        state["error_message"] = (
            f"Job extraction for source '{source}' is not yet implemented. "
            f"Use source='manual' instead."
        )
        state["current_step"] = BusinessState.FAILED
    except Exception as e:
        logger.error(f"Job extraction failed for {job_id}: {e}", exc_info=True)
        state["error_message"] = f"Job extraction failed: {str(e)}"
        state["current_step"] = BusinessState.FAILED

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] extract_job_node completed in {elapsed:.2f}s")
    return state


async def filter_job_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
    """Filter job using LLM (LinkedIn source only).

    This node evaluates if a job is suitable based on user preferences.
    Currently a stub - will be implemented for LinkedIn integration.

    Args:
        state: Current workflow state.

    Returns:
        Updated state (potentially with is_suitable flag in future).
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Filtering job {job_id} (LinkedIn source)")
    state["current_step"] = WorkflowStep.FILTERING

    # TODO: Implement LLM-based job filtering for LinkedIn jobs
    # For now, just pass through (all jobs are considered suitable)
    logger.warning(f"Job filtering not implemented, passing through for {job_id}")
    state["current_step"] = WorkflowStep.JOB_FILTERED

    return state


async def compose_cv_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
    """Compose tailored CV using LLM.

    Reuses logic from MVP workflow. Supports user_feedback for retry.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with tailored_cv_json.
    """
    start_time = time.time()
    job_id = state.get("job_id", "unknown")
    user_feedback = state.get("user_feedback")
    logger.info(f"[TIMING] Starting compose_cv_node for job {job_id}")
    if user_feedback:
        logger.info(f"Retry with feedback: {user_feedback}")
    state["current_step"] = WorkflowStep.COMPOSING_CV

    # In fixture replay mode, check LLM response cache first (skip retries)
    if settings.seed_jobs_from_file and not user_feedback:
        cached = get_cached_llm_response(job_id)
        if cached is not None:
            state["tailored_cv_json"] = cached
            state["current_step"] = WorkflowStep.CV_COMPOSED
            state["error_message"] = None
            elapsed = time.time() - start_time
            logger.info(
                f"[TIMING] compose_cv_node completed in {elapsed:.2f}s (LLM cache hit)"
            )
            return state

    # Get LLM provider/model from raw_input if specified
    raw_input = state.get("raw_input", {})
    llm_provider = raw_input.get("llm_provider")
    llm_model = raw_input.get("llm_model")

    result = await compose_cv(
        state,
        job_id=job_id,
        llm_provider=llm_provider,
        llm_model=llm_model,
        user_feedback=user_feedback,
    )

    state["tailored_cv_json"] = result["tailored_cv_json"]
    if result["error_message"]:
        state["error_message"] = result["error_message"]
    else:
        state["current_step"] = WorkflowStep.CV_COMPOSED
        state["error_message"] = None

        # Cache LLM response for future fixture replays
        if settings.seed_jobs_from_file:
            save_llm_response(job_id, state["tailored_cv_json"])

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] compose_cv_node completed in {elapsed:.2f}s")
    return state


async def generate_pdf_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
    """Generate PDF from tailored CV JSON.

    Reuses logic from MVP workflow.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with tailored_cv_pdf_path.
    """
    start_time = time.time()
    job_id = state.get("job_id", "unknown")
    logger.info(f"[TIMING] Starting generate_pdf_node for job {job_id}")
    state["current_step"] = WorkflowStep.GENERATING_PDF

    # Get template name from raw_input or fall back to settings
    raw_input = state.get("raw_input", {})
    template_name = raw_input.get("template_name") or settings.cv_template_name
    logger.info(f"Template selection - raw_input: {raw_input.get('template_name')}, using: {template_name}")

    result = await generate_pdf(state, job_id=job_id, template_name=template_name)

    state["tailored_cv_pdf_path"] = result["tailored_cv_pdf_path"]
    if result["error_message"]:
        state["error_message"] = result["error_message"]
        if not result["tailored_cv_pdf_path"]:
            state["current_step"] = BusinessState.FAILED
    else:
        state["current_step"] = WorkflowStep.PDF_GENERATED

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] generate_pdf_node completed in {elapsed:.2f}s")
    return state


async def save_to_db_node(state: PreparationWorkflowState, config: RunnableConfig | None = None) -> PreparationWorkflowState:
    """Save job record to repository.

    Sets status based on mode:
    - MVP mode: status="completed" (ready for download)
    - Full mode: status="pending" (awaiting HITL review)

    Args:
        state: Current workflow state.

    Returns:
        Updated state with final status.
    """
    job_id = state.get("job_id", "unknown")
    mode = state.get("mode", "mvp")
    logger.info(f"Saving job {job_id} to repository (mode: {mode})")
    state["current_step"] = WorkflowStep.SAVING

    # Determine final status
    if state.get("error_message") and not state.get("tailored_cv_pdf_path"):
        final_status = BusinessState.FAILED
    elif mode == "mvp":
        final_status = BusinessState.CV_READY
    else:
        final_status = BusinessState.PENDING_REVIEW

    try:
        cv_json = state.get("tailored_cv_json")
        pdf_path = state.get("tailored_cv_pdf_path")

        # Build job record
        job_record = JobRecord(
            job_id=job_id,
            user_id=state.get("user_id", ""),
            source=state.get("source", "manual"),
            mode=mode,
            status=final_status,
            job_posting=state.get("job_posting"),
            raw_input=state.get("raw_input"),
            current_cv_json=cv_json,
            current_pdf_path=pdf_path,
            application_url=state.get("job_posting", {}).get("url"),
            error_message=state.get("error_message"),
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

        # Save to repository
        repo = get_repository_from_config(config or {})
        await repo.create(job_record)
        logger.info(f"Job {job_id} saved to repository with status: {final_status}")

        # Create CV composition attempt record if we have CV data
        if cv_json:
            attempt = CVCompositionAttempt(
                job_id=job_id,
                user_id=state.get("user_id", ""),
                attempt_number=1,
                user_feedback=state.get("user_feedback"),
                cv_json=cv_json,
                pdf_path=pdf_path,
            )
            await repo.create_cv_attempt(attempt)
            logger.info(f"CV attempt #1 saved for job {job_id}")

        # Update state
        state["current_step"] = final_status
        logger.info(f"Preparation workflow completed for job {job_id}: {final_status}")

    except Exception as e:
        logger.error(f"Failed to save job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"Failed to save job: {str(e)}"
        state["current_step"] = BusinessState.FAILED

    return state


