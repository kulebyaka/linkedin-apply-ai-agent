"""Retry Workflow - Regenerate CV with user feedback.

This workflow is triggered when a HITL decision is "retry".
It reuses the existing job data and regenerates the CV incorporating user feedback.

Flow:
1. Load job data from repository
2. Compose CV with user feedback
3. Generate new PDF
4. Update repository (status="pending", increment retry_count)
5. Job goes back to HITL queue
"""

import logging
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ..config.settings import get_settings
from ..models.cv_attempt import CVCompositionAttempt
from ..models.state_machine import BusinessState, WorkflowStep
from ._shared import compose_cv, generate_pdf, get_repository_from_config, load_master_cv

logger = logging.getLogger(__name__)
settings = get_settings()


class RetryWorkflowState(TypedDict):
    """State structure for Retry Workflow."""

    # Input
    job_id: str
    user_feedback: str

    # Loaded from DB
    job_posting: dict
    master_cv: dict
    retry_count: int

    # Processing
    tailored_cv_json: dict
    tailored_cv_pdf_path: str

    # Status
    current_step: str
    error_message: str | None


def create_retry_workflow() -> StateGraph:
    """Create the Retry Workflow.

    Flow:
        load_from_db -> compose_cv -> generate_pdf -> update_db -> END

    Returns:
        Compiled LangGraph workflow.
    """
    workflow = StateGraph(RetryWorkflowState)

    # Add nodes
    workflow.add_node("load_from_db", load_from_db_node)
    workflow.add_node("compose_cv", compose_cv_node)
    workflow.add_node("generate_pdf", generate_pdf_node)
    workflow.add_node("update_db", update_db_node)

    # Define flow
    workflow.set_entry_point("load_from_db")
    workflow.add_edge("load_from_db", "compose_cv")
    workflow.add_edge("compose_cv", "generate_pdf")
    workflow.add_edge("generate_pdf", "update_db")
    workflow.add_edge("update_db", END)

    # Compile with checkpointer
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# =============================================================================
# Workflow Nodes
# =============================================================================

async def load_from_db_node(state: RetryWorkflowState, config: RunnableConfig | None = None) -> RetryWorkflowState:
    """Load existing job data from repository.

    Args:
        state: Current workflow state (must have job_id and user_feedback).

    Returns:
        Updated state with job_posting, master_cv, retry_count.
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Loading job data for retry: {job_id}")
    state["current_step"] = WorkflowStep.LOADING

    try:
        repo = get_repository_from_config(config or {})

        # Load job record
        job_record = await repo.get(job_id)

        if not job_record:
            raise ValueError(f"Job {job_id} not found in repository")

        # Update state with loaded data
        state["job_posting"] = job_record.job_posting

        # Derive retry count from CV attempts
        attempts = await repo.get_cv_attempts(job_id)
        state["retry_count"] = len(attempts) + 1

        # Load master CV
        state["master_cv"] = load_master_cv()

        state["current_step"] = WorkflowStep.LOADED
        logger.info(f"Loaded job data for {job_id}, retry #{state['retry_count']}")

    except Exception as e:
        logger.error(f"Failed to load job {job_id} for retry: {e}", exc_info=True)
        state["error_message"] = f"Failed to load job data: {str(e)}"
        state["current_step"] = BusinessState.FAILED

    return state


async def compose_cv_node(state: RetryWorkflowState) -> RetryWorkflowState:
    """Compose CV with user feedback.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with tailored_cv_json.
    """
    job_id = state.get("job_id", "unknown")
    user_feedback = state.get("user_feedback", "")
    retry_count = state.get("retry_count", 1)

    logger.info(f"Composing CV for retry #{retry_count} of job {job_id}")
    logger.info(f"User feedback: {user_feedback}")
    state["current_step"] = WorkflowStep.COMPOSING_CV

    # Check for previous errors
    if state.get("error_message"):
        logger.warning(f"Skipping CV composition due to previous error: {state['error_message']}")
        return state

    result = await compose_cv(
        state,
        job_id=job_id,
        user_feedback=user_feedback,
    )

    state["tailored_cv_json"] = result["tailored_cv_json"]
    if result["error_message"]:
        state["error_message"] = result["error_message"]
    else:
        state["current_step"] = WorkflowStep.CV_COMPOSED
        state["error_message"] = None
        logger.info(f"CV retry composition completed for job {job_id}")

    return state


async def generate_pdf_node(state: RetryWorkflowState) -> RetryWorkflowState:
    """Generate PDF from retried CV.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with tailored_cv_pdf_path.
    """
    job_id = state.get("job_id", "unknown")
    retry_count = state.get("retry_count", 1)
    logger.info(f"Generating PDF for retry #{retry_count} of job {job_id}")
    state["current_step"] = WorkflowStep.GENERATING_PDF

    result = await generate_pdf(state, job_id=job_id, version_suffix=f"_v{retry_count}")

    state["tailored_cv_pdf_path"] = result["tailored_cv_pdf_path"]
    if result["error_message"]:
        state["error_message"] = result["error_message"]
        if not result["tailored_cv_pdf_path"]:
            state["current_step"] = BusinessState.FAILED
    else:
        state["current_step"] = WorkflowStep.PDF_GENERATED
        logger.info(f"Retry PDF generated for job {job_id}: {result['tailored_cv_pdf_path']}")

    return state


async def update_db_node(state: RetryWorkflowState, config: RunnableConfig | None = None) -> RetryWorkflowState:
    """Update job record in repository after retry.

    Sets status back to "pending" so it appears in HITL queue again.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with final status.
    """
    job_id = state.get("job_id", "unknown")
    retry_count = state.get("retry_count", 1)
    logger.info(f"Updating job {job_id} after retry #{retry_count}")
    state["current_step"] = WorkflowStep.SAVING

    # Determine final status
    if state.get("error_message") and not state.get("tailored_cv_pdf_path"):
        final_status = BusinessState.FAILED
    else:
        final_status = BusinessState.PENDING_REVIEW  # Back to HITL queue

    try:
        repo = get_repository_from_config(config or {})

        cv_json = state.get("tailored_cv_json")
        pdf_path = state.get("tailored_cv_pdf_path")

        # Build update dict
        updates = {
            "status": final_status,
            "current_cv_json": cv_json,
            "current_pdf_path": pdf_path,
            "error_message": state.get("error_message"),
        }

        # Update repository
        await repo.update(job_id, updates)
        logger.info(f"Job {job_id} updated after retry: status={final_status}")

        # Create CV composition attempt record if we have CV data
        if cv_json:
            attempt = CVCompositionAttempt(
                job_id=job_id,
                attempt_number=retry_count,
                user_feedback=state.get("user_feedback"),
                cv_json=cv_json,
                pdf_path=pdf_path,
            )
            await repo.create_cv_attempt(attempt)
            logger.info(f"CV attempt #{retry_count} saved for job {job_id}")

        # Update state
        state["current_step"] = final_status
        logger.info(f"Retry workflow completed for job {job_id}: {final_status}")

    except Exception as e:
        logger.error(f"Failed to update job {job_id} after retry: {e}", exc_info=True)
        state["error_message"] = f"Failed to update job: {str(e)}"
        state["current_step"] = BusinessState.FAILED

    return state


