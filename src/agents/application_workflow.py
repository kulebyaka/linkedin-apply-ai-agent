"""Application Workflow - Apply to jobs after HITL approval.

This workflow is triggered when a HITL decision is "approved".
It handles the actual job application process.

IMPLEMENTATION STATUS: Stub only - actual application logic is a future feature.

Flow:
1. Load job data from repository
2. Route by application type (deep_agent, linkedin, manual)
3. Execute application (stub)
4. Update repository with result

Application Types:
- deep_agent: Use Playwright MCP browser automation for generic job forms
- linkedin: LinkedIn Easy Apply automation
- manual: Just mark as "manual_required" (user applies manually)
"""

import logging
from typing import TypedDict, Literal
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from ..services.job_repository import JobRepository, get_repository
from ..config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ApplicationWorkflowState(TypedDict):
    """State structure for Application Workflow."""

    # Input
    job_id: str
    application_type: Literal["deep_agent", "linkedin", "manual"]

    # Loaded from DB
    application_url: str
    cv_json: dict
    pdf_path: str
    job_posting: dict

    # Result
    application_status: Literal["success", "failed", "manual_required"]
    application_result: dict | None
    error_message: str | None

    # Status
    current_step: str


# Reference to repository (shared with other workflows)
_repository: JobRepository | None = None


def set_repository(repo: JobRepository) -> None:
    """Set the repository instance."""
    global _repository
    _repository = repo


def get_repo() -> JobRepository:
    """Get repository instance."""
    global _repository
    if _repository is None:
        _repository = get_repository()
    return _repository


def create_application_workflow() -> StateGraph:
    """Create the Application Workflow.

    IMPLEMENTATION STATUS: Stub only.

    Flow:
        load_from_db -> route_by_type -> [apply_*] -> update_db -> END

    Returns:
        Compiled LangGraph workflow.
    """
    workflow = StateGraph(ApplicationWorkflowState)

    # Add nodes
    workflow.add_node("load_from_db", load_from_db_node)
    workflow.add_node("apply_deep_agent", apply_deep_agent_node)
    workflow.add_node("apply_linkedin", apply_linkedin_node)
    workflow.add_node("apply_manual", apply_manual_node)
    workflow.add_node("update_db", update_db_node)

    # Define flow
    workflow.set_entry_point("load_from_db")

    # Conditional routing after loading
    workflow.add_conditional_edges(
        "load_from_db",
        route_by_application_type,
        {
            "deep_agent": "apply_deep_agent",
            "linkedin": "apply_linkedin",
            "manual": "apply_manual",
            "error": END
        }
    )

    # All apply nodes lead to update_db
    workflow.add_edge("apply_deep_agent", "update_db")
    workflow.add_edge("apply_linkedin", "update_db")
    workflow.add_edge("apply_manual", "update_db")
    workflow.add_edge("update_db", END)

    # Compile with checkpointer
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def route_by_application_type(state: ApplicationWorkflowState) -> str:
    """Route to appropriate apply node based on application type.

    Args:
        state: Current workflow state.

    Returns:
        Next node name: "deep_agent", "linkedin", "manual", or "error".
    """
    if state.get("error_message"):
        return "error"

    app_type = state.get("application_type", "manual")

    if app_type == "deep_agent":
        return "deep_agent"
    elif app_type == "linkedin":
        return "linkedin"
    else:
        return "manual"


# =============================================================================
# Workflow Nodes
# =============================================================================

def load_from_db_node(state: ApplicationWorkflowState) -> ApplicationWorkflowState:
    """Load job data from repository.

    IMPLEMENTATION STATUS: Stub.

    Args:
        state: Current workflow state (must have job_id).

    Returns:
        Updated state with job data.
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Loading job data for application: {job_id}")
    state["current_step"] = "loading"

    try:
        repo = get_repo()

        # Load job record
        import asyncio
        try:
            job_record = asyncio.run(repo.get(job_id))
        except NotImplementedError:
            logger.warning(f"Repository not implemented for job {job_id}")
            state["error_message"] = "Repository not implemented"
            state["current_step"] = "failed"
            return state

        if not job_record:
            raise ValueError(f"Job {job_id} not found in repository")

        # Update state with loaded data
        state["application_url"] = job_record.application_url or ""
        state["cv_json"] = job_record.cv_json or {}
        state["pdf_path"] = job_record.pdf_path or ""
        state["job_posting"] = job_record.job_posting or {}

        state["current_step"] = "loaded"
        logger.info(f"Loaded job data for application: {job_id}")

    except Exception as e:
        logger.error(f"Failed to load job {job_id} for application: {e}", exc_info=True)
        state["error_message"] = f"Failed to load job data: {str(e)}"
        state["current_step"] = "failed"

    return state


def apply_deep_agent_node(state: ApplicationWorkflowState) -> ApplicationWorkflowState:
    """Apply using Deep Agent (Playwright MCP browser automation).

    IMPLEMENTATION STATUS: Stub only - future feature.

    This will use Playwright MCP to:
    1. Navigate to application URL
    2. Fill out application form using CV data
    3. Upload PDF resume
    4. Submit application

    Args:
        state: Current workflow state.

    Returns:
        Updated state with application result.
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Starting Deep Agent application for job {job_id}")
    state["current_step"] = "applying_deep_agent"

    # STUB: Deep Agent not implemented yet
    logger.warning(f"Deep Agent application not implemented for job {job_id}")
    state["application_status"] = "failed"
    state["application_result"] = None
    state["error_message"] = (
        "Deep Agent (Playwright MCP browser automation) is not yet implemented. "
        "Please apply manually."
    )

    return state


def apply_linkedin_node(state: ApplicationWorkflowState) -> ApplicationWorkflowState:
    """Apply using LinkedIn Easy Apply automation.

    IMPLEMENTATION STATUS: Stub only - future feature.

    This will use Playwright to:
    1. Navigate to LinkedIn job posting
    2. Click "Easy Apply" button
    3. Fill out application form
    4. Upload resume PDF
    5. Submit application

    Args:
        state: Current workflow state.

    Returns:
        Updated state with application result.
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Starting LinkedIn Easy Apply for job {job_id}")
    state["current_step"] = "applying_linkedin"

    # STUB: LinkedIn automation not implemented yet
    logger.warning(f"LinkedIn Easy Apply not implemented for job {job_id}")
    state["application_status"] = "failed"
    state["application_result"] = None
    state["error_message"] = (
        "LinkedIn Easy Apply automation is not yet implemented. "
        "Please apply manually on LinkedIn."
    )

    return state


def apply_manual_node(state: ApplicationWorkflowState) -> ApplicationWorkflowState:
    """Mark job as requiring manual application.

    For jobs that require manual application (e.g., external job boards
    with complex forms), this node marks the job as "manual_required"
    so the user knows to apply themselves.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with manual_required status.
    """
    job_id = state.get("job_id", "unknown")
    application_url = state.get("application_url", "")
    logger.info(f"Job {job_id} marked for manual application: {application_url}")
    state["current_step"] = "manual_required"

    state["application_status"] = "manual_required"
    state["application_result"] = {
        "message": "Please apply manually using the application URL.",
        "application_url": application_url,
    }
    state["error_message"] = None

    return state


def update_db_node(state: ApplicationWorkflowState) -> ApplicationWorkflowState:
    """Update job record in repository after application attempt.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with final status.
    """
    job_id = state.get("job_id", "unknown")
    app_status = state.get("application_status", "failed")
    logger.info(f"Updating job {job_id} after application: {app_status}")
    state["current_step"] = "saving"

    # Map application_status to job status
    if app_status == "success":
        job_status = "applied"
    elif app_status == "manual_required":
        job_status = "manual_required"
    else:
        job_status = "failed"

    try:
        repo = get_repo()

        # Build update dict
        updates = {
            "status": job_status,
            "error_message": state.get("error_message"),
            "updated_at": datetime.now(),
        }

        # Update repository
        import asyncio
        try:
            asyncio.run(repo.update(job_id, updates))
            logger.info(f"Job {job_id} updated after application: status={job_status}")
        except NotImplementedError:
            logger.warning(f"Repository not implemented, job {job_id} not updated")

        # Update state
        state["current_step"] = job_status
        logger.info(f"Application workflow completed for job {job_id}: {job_status}")

    except Exception as e:
        logger.error(f"Failed to update job {job_id} after application: {e}", exc_info=True)
        state["error_message"] = f"Failed to update job: {str(e)}"
        state["current_step"] = "failed"

    return state
