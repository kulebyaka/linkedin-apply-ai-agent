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
from pathlib import Path
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from ..services.cv_composer import CVComposer
from ..services.pdf_generator import PDFGenerator
from ..services.job_repository import JobRepository, get_repository
from ..llm.provider import LLMClientFactory, LLMProvider
from ..config.settings import get_settings

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


# Reference to repository (shared with preparation workflow)
_repository: JobRepository | None = None


def set_repository(repo: JobRepository) -> None:
    """Set the repository instance."""
    global _repository
    _repository = repo


def get_repo() -> JobRepository:
    """Get repository instance."""
    global _repository
    if _repository is None:
        from .preparation_workflow import get_repository as get_prep_repo
        _repository = get_prep_repo()
    return _repository


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

def load_from_db_node(state: RetryWorkflowState) -> RetryWorkflowState:
    """Load existing job data from repository.

    Args:
        state: Current workflow state (must have job_id and user_feedback).

    Returns:
        Updated state with job_posting, master_cv, retry_count.
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Loading job data for retry: {job_id}")
    state["current_step"] = "loading"

    try:
        repo = get_repo()

        # Load job record
        import asyncio
        try:
            job_record = asyncio.run(repo.get(job_id))
        except NotImplementedError:
            # Repository not implemented - use stub data
            logger.warning(f"Repository not implemented, using stub for retry {job_id}")
            # For development, we'll need to pass data through state
            if not state.get("job_posting") or not state.get("master_cv"):
                raise ValueError(
                    "Repository not implemented and job_posting/master_cv not in state. "
                    "Pass these in the initial state for retry."
                )
            state["current_step"] = "loaded"
            return state

        if not job_record:
            raise ValueError(f"Job {job_id} not found in repository")

        # Update state with loaded data
        state["job_posting"] = job_record.job_posting
        state["retry_count"] = job_record.retry_count + 1

        # Load master CV (same as original)
        from .preparation_workflow import load_master_cv
        state["master_cv"] = load_master_cv()

        state["current_step"] = "loaded"
        logger.info(f"Loaded job data for {job_id}, retry #{state['retry_count']}")

    except Exception as e:
        logger.error(f"Failed to load job {job_id} for retry: {e}", exc_info=True)
        state["error_message"] = f"Failed to load job data: {str(e)}"
        state["current_step"] = "failed"

    return state


def compose_cv_node(state: RetryWorkflowState) -> RetryWorkflowState:
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
    state["current_step"] = "composing_cv"

    # Check for previous errors
    if state.get("error_message"):
        logger.warning(f"Skipping CV composition due to previous error: {state['error_message']}")
        return state

    try:
        # Initialize LLM client
        llm_client = _init_llm_client()

        # Initialize CV composer
        cv_composer = CVComposer(
            llm_client=llm_client,
            prompts_dir=settings.prompts_dir
        )

        # Get master CV and job posting from state
        master_cv = state.get("master_cv")
        job_posting = state.get("job_posting")

        if not master_cv:
            raise ValueError("Master CV not available for retry")
        if not job_posting:
            raise ValueError("Job posting not available for retry")

        # Compose tailored CV with feedback
        logger.info(
            f"Recomposing CV for job {job_id}: "
            f"{job_posting.get('title')} at {job_posting.get('company')}"
        )
        tailored_cv = cv_composer.compose_cv(
            master_cv=master_cv,
            job_posting=job_posting,
            user_feedback=user_feedback
        )

        # Update state
        state["tailored_cv_json"] = tailored_cv
        state["current_step"] = "cv_composed"
        state["error_message"] = None
        logger.info(f"CV retry composition completed for job {job_id}")

    except Exception as e:
        logger.error(f"CV retry composition failed for job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"CV composition failed: {str(e)}"
        state["tailored_cv_json"] = None

    return state


def generate_pdf_node(state: RetryWorkflowState) -> RetryWorkflowState:
    """Generate PDF from retried CV.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with tailored_cv_pdf_path.
    """
    job_id = state.get("job_id", "unknown")
    retry_count = state.get("retry_count", 1)
    logger.info(f"Generating PDF for retry #{retry_count} of job {job_id}")
    state["current_step"] = "generating_pdf"

    # Check if we have CV data
    cv_json = state.get("tailored_cv_json")
    if not cv_json:
        previous_error = state.get("error_message")
        if previous_error:
            error_msg = f"PDF generation skipped due to previous error: {previous_error}"
        else:
            error_msg = f"PDF generation skipped for job {job_id}: No CV data available"

        logger.error(error_msg)
        state["error_message"] = error_msg
        state["tailored_cv_pdf_path"] = None
        state["current_step"] = "failed"
        return state

    try:
        # Get job info for filename
        job_posting = state.get("job_posting", {})
        job_title = job_posting.get("title", "unknown")
        company = job_posting.get("company", "unknown")

        # Generate safe filename with retry indicator
        safe_company = "".join(
            c for c in company if c.isalnum() or c in (' ', '-', '_')
        ).strip()
        safe_title = "".join(
            c for c in job_title if c.isalnum() or c in (' ', '-', '_')
        ).strip()

        # Get candidate name from CV
        candidate_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        safe_name = "".join(
            c for c in candidate_name if c.isalnum() or c in (' ', '-', '_')
        ).strip()

        # Create filename with retry count
        pdf_filename = f"{safe_name}_{safe_company}_{safe_title}_v{retry_count}.pdf".replace(" ", "_")
        output_dir = Path(settings.generated_cvs_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / pdf_filename

        # Initialize PDF generator
        generator = PDFGenerator(
            template_dir=settings.cv_template_dir,
            template_name=settings.cv_template_name
        )

        # Generate PDF
        logger.info(f"Generating retry PDF for job {job_id}: {output_path}")
        pdf_path = generator.generate_pdf(
            cv_json=cv_json,
            output_path=str(output_path),
            metadata={
                "subject": f"Resume for {job_title} at {company} (Retry #{retry_count})",
                "keywords": f"{company}, {job_title}, retry"
            }
        )

        # Update state
        state["tailored_cv_pdf_path"] = pdf_path
        state["current_step"] = "pdf_generated"
        logger.info(f"Retry PDF generated for job {job_id}: {pdf_path}")

    except Exception as e:
        logger.error(f"PDF generation failed for retry of job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"PDF generation failed: {str(e)}"
        state["tailored_cv_pdf_path"] = None
        state["current_step"] = "failed"

    return state


def update_db_node(state: RetryWorkflowState) -> RetryWorkflowState:
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
    state["current_step"] = "saving"

    # Determine final status
    if state.get("error_message") and not state.get("tailored_cv_pdf_path"):
        final_status = "failed"
    else:
        final_status = "pending"  # Back to HITL queue

    try:
        repo = get_repo()

        # Build update dict
        updates = {
            "status": final_status,
            "cv_json": state.get("tailored_cv_json"),
            "pdf_path": state.get("tailored_cv_pdf_path"),
            "user_feedback": state.get("user_feedback"),
            "retry_count": retry_count,
            "error_message": state.get("error_message"),
            "updated_at": datetime.now(),
        }

        # Update repository
        import asyncio
        try:
            asyncio.run(repo.update(job_id, updates))
            logger.info(f"Job {job_id} updated after retry: status={final_status}")
        except NotImplementedError:
            logger.warning(f"Repository not implemented, job {job_id} not updated")

        # Update state
        state["current_step"] = final_status
        logger.info(f"Retry workflow completed for job {job_id}: {final_status}")

    except Exception as e:
        logger.error(f"Failed to update job {job_id} after retry: {e}", exc_info=True)
        state["error_message"] = f"Failed to update job: {str(e)}"
        state["current_step"] = "failed"

    return state


# =============================================================================
# Helper Functions
# =============================================================================

def _init_llm_client():
    """Initialize LLM client based on settings."""
    provider = LLMProvider(settings.primary_llm_provider)

    # Get API key and model based on provider
    if provider == LLMProvider.OPENAI:
        api_key = settings.openai_api_key
        model = settings.openai_model
    elif provider == LLMProvider.DEEPSEEK:
        api_key = settings.deepseek_api_key
        model = settings.deepseek_model
    elif provider == LLMProvider.GROK:
        api_key = settings.grok_api_key
        model = settings.grok_model
    elif provider == LLMProvider.ANTHROPIC:
        api_key = settings.anthropic_api_key
        model = settings.anthropic_model
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    if not api_key:
        raise ValueError(f"API key not configured for provider: {provider}")

    logger.info(f"Using LLM provider: {provider}, model: {model}")
    return LLMClientFactory.create(provider, api_key, model)
