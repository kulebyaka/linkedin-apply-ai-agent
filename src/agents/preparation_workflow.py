"""Preparation Workflow - Job source to CV generation pipeline.

This workflow handles the first half of the two-workflow pipeline:
1. Extract job data from source (URL/manual/LinkedIn)
2. Filter job (LinkedIn only - optional)
3. Compose tailored CV using LLM
4. Generate PDF
5. Save to repository (status="pending" for HITL or "completed" for MVP)

The workflow ends at the HITL boundary. Application is handled by a separate workflow.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ..config.settings import get_settings
from ..llm.provider import LLMClientFactory, LLMProvider
from ..models.unified import JobRecord
from ..services.cv_composer import CVComposer
from ..services.job_repository import InMemoryJobRepository, JobRepository
from ..services.job_source import JobExtractionError, JobSourceFactory
from ..services.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)
settings = get_settings()


class PreparationWorkflowState(TypedDict):
    """State structure for Preparation Workflow."""

    # Input
    job_id: str
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


# Global repository instance (will be injected in production)
_repository: JobRepository | None = None


def set_repository(repo: JobRepository) -> None:
    """Set the repository instance for the workflow.

    Args:
        repo: JobRepository instance to use for persistence.
    """
    global _repository
    _repository = repo


def get_repository() -> JobRepository:
    """Get the current repository instance.

    Returns:
        JobRepository instance.

    Raises:
        RuntimeError: If repository not configured.
    """
    global _repository
    if _repository is None:
        # Default to in-memory for development
        _repository = InMemoryJobRepository()
    return _repository


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


def extract_job_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
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
    state["current_step"] = "extracting"

    try:
        # Extract job data
        raw_input = state.get("raw_input", {})

        # Get LLM provider/model from raw_input if specified
        llm_provider = raw_input.get("llm_provider")
        llm_model = raw_input.get("llm_model")

        # Initialize LLM client for URL extraction (with optional overrides)
        llm_client = _init_llm_client(llm_provider, llm_model)

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
            state["current_step"] = "job_extracted"
            logger.info(f"Manual job data processed for {job_id}")
        else:
            # URL and LinkedIn extraction - currently raises NotImplementedError
            # In the future, this will use async extraction
            # For now, we catch the NotImplementedError and provide a stub response
            try:
                import asyncio

                job_posting = asyncio.run(adapter.extract(raw_input))
                state["job_posting"] = job_posting
                state["current_step"] = "job_extracted"
            except NotImplementedError:
                # Stub: For URL source, try to use raw_input directly if it has required fields
                if source == "url" and "url" in raw_input:
                    logger.warning(f"URL extraction not implemented, using stub for {job_id}")
                    state["job_posting"] = {
                        "id": job_id,
                        "title": raw_input.get("title", "Position"),
                        "company": raw_input.get("company", "Company"),
                        "description": raw_input.get("description", ""),
                        "requirements": raw_input.get("requirements"),
                        "location": "Remote",
                        "url": raw_input.get("url", ""),
                        "is_remote": True,
                    }
                    state["current_step"] = "job_extracted"
                    state["error_message"] = (
                        "Note: URL extraction pending implementation. Using provided data."
                    )
                else:
                    raise

    except JobExtractionError as e:
        logger.error(f"Job extraction failed for {job_id}: {e}")
        state["error_message"] = f"Job extraction failed: {e.message}"
        state["current_step"] = "failed"
    except Exception as e:
        logger.error(f"Job extraction failed for {job_id}: {e}", exc_info=True)
        state["error_message"] = f"Job extraction failed: {str(e)}"
        state["current_step"] = "failed"

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] extract_job_node completed in {elapsed:.2f}s")
    return state


def filter_job_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
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
    state["current_step"] = "filtering"

    # TODO: Implement LLM-based job filtering for LinkedIn jobs
    # For now, just pass through (all jobs are considered suitable)
    logger.warning(f"Job filtering not implemented, passing through for {job_id}")
    state["current_step"] = "job_filtered"

    return state


def compose_cv_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
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
    state["current_step"] = "composing_cv"

    try:
        # Get LLM provider/model from raw_input if specified
        raw_input = state.get("raw_input", {})
        llm_provider = raw_input.get("llm_provider")
        llm_model = raw_input.get("llm_model")

        # Initialize LLM client with optional overrides
        llm_client = _init_llm_client(llm_provider, llm_model)

        # Initialize CV composer
        cv_composer = CVComposer(llm_client=llm_client, prompts_dir=settings.prompts_dir)

        # Get master CV and job posting from state
        master_cv = state.get("master_cv")
        job_posting = state.get("job_posting")

        if not master_cv:
            raise ValueError("Master CV not provided in workflow state")
        if not job_posting:
            raise ValueError("Job posting not provided in workflow state")

        # Compose tailored CV (with optional feedback for retry)
        logger.info(
            f"Composing CV for job {job_id}: "
            f"{job_posting.get('title')} at {job_posting.get('company')}"
        )
        tailored_cv = cv_composer.compose_cv(
            master_cv=master_cv, job_posting=job_posting, user_feedback=user_feedback
        )

        # Update state - convert Pydantic model to dict
        state["tailored_cv_json"] = tailored_cv.model_dump()
        state["current_step"] = "cv_composed"
        state["error_message"] = None  # Clear any previous errors
        logger.info(f"CV composition completed successfully for job {job_id}")

    except Exception as e:
        logger.error(f"CV composition failed for job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"CV composition failed: {str(e)}"
        state["tailored_cv_json"] = None

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] compose_cv_node completed in {elapsed:.2f}s")
    return state


def generate_pdf_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
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

        # Generate safe filename
        safe_company = "".join(c for c in company if c.isalnum() or c in (" ", "-", "_")).strip()
        safe_title = "".join(c for c in job_title if c.isalnum() or c in (" ", "-", "_")).strip()

        # Get candidate name from CV
        candidate_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        safe_name = "".join(
            c for c in candidate_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()

        # Create filename
        pdf_filename = f"{safe_name}_{safe_company}_{safe_title}.pdf".replace(" ", "_")
        output_dir = Path(settings.generated_cvs_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / pdf_filename

        # Get template name from raw_input or fall back to settings
        raw_input = state.get("raw_input", {})
        template_name = raw_input.get("template_name") or settings.cv_template_name
        logger.info(f"Template selection - raw_input: {raw_input.get('template_name')}, using: {template_name}")

        # Initialize PDF generator
        generator = PDFGenerator(
            template_dir=settings.cv_template_dir, template_name=template_name
        )

        # Generate PDF
        logger.info(f"Generating PDF for job {job_id}: {output_path}")
        pdf_path = generator.generate_pdf(
            cv_json=cv_json,
            output_path=str(output_path),
            metadata={
                "subject": f"Resume for {job_title} at {company}",
                "keywords": f"{company}, {job_title}",
            },
        )

        # Update state
        state["tailored_cv_pdf_path"] = pdf_path
        state["current_step"] = "pdf_generated"
        logger.info(f"PDF generated successfully for job {job_id}: {pdf_path}")

    except Exception as e:
        logger.error(f"PDF generation failed for job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"PDF generation failed: {str(e)}"
        state["tailored_cv_pdf_path"] = None
        state["current_step"] = "failed"

    elapsed = time.time() - start_time
    logger.info(f"[TIMING] generate_pdf_node completed in {elapsed:.2f}s")
    return state


def save_to_db_node(state: PreparationWorkflowState) -> PreparationWorkflowState:
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
    state["current_step"] = "saving"

    # Determine final status
    if state.get("error_message") and not state.get("tailored_cv_pdf_path"):
        final_status = "failed"
    elif mode == "mvp":
        final_status = "completed"
    else:
        final_status = "pending"  # Awaiting HITL review

    try:
        # Build job record
        job_record = JobRecord(
            job_id=job_id,
            source=state.get("source", "manual"),
            mode=mode,
            status=final_status,
            job_posting=state.get("job_posting"),
            raw_input=state.get("raw_input"),
            cv_json=state.get("tailored_cv_json"),
            pdf_path=state.get("tailored_cv_pdf_path"),
            application_url=state.get("job_posting", {}).get("url"),
            user_feedback=state.get("user_feedback"),
            retry_count=state.get("retry_count", 0),
            error_message=state.get("error_message"),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        # Save to repository
        repo = get_repository()
        try:
            import asyncio

            asyncio.run(repo.create(job_record))
            logger.info(f"Job {job_id} saved to repository with status: {final_status}")
        except NotImplementedError:
            # Repository not implemented yet - log and continue
            logger.warning(f"Repository not implemented, job {job_id} not persisted")

        # Update state
        state["current_step"] = final_status
        logger.info(f"Preparation workflow completed for job {job_id}: {final_status}")

    except Exception as e:
        logger.error(f"Failed to save job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"Failed to save job: {str(e)}"
        state["current_step"] = "failed"

    return state


# =============================================================================
# Helper Functions
# =============================================================================


def _init_llm_client(llm_provider: str | None = None, llm_model: str | None = None):
    """Initialize LLM client based on settings or override parameters.

    Args:
        llm_provider: Optional provider override (openai, anthropic)
        llm_model: Optional model override (e.g., gpt-4.1-nano, claude-haiku-4.5)
    """
    # Use override or fall back to settings
    provider_str = llm_provider or settings.primary_llm_provider
    provider = LLMProvider(provider_str)

    # Get API key and model based on provider
    if provider == LLMProvider.OPENAI:
        api_key = settings.openai_api_key
        model = llm_model or settings.openai_model
    elif provider == LLMProvider.DEEPSEEK:
        api_key = settings.deepseek_api_key
        model = llm_model or settings.deepseek_model
    elif provider == LLMProvider.GROK:
        api_key = settings.grok_api_key
        model = llm_model or settings.grok_model
    elif provider == LLMProvider.ANTHROPIC:
        api_key = settings.anthropic_api_key
        model = llm_model or settings.anthropic_model
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    if not api_key:
        raise ValueError(f"API key not configured for provider: {provider}")

    logger.info(f"Using LLM provider: {provider}, model: {model}")
    return LLMClientFactory.create(provider, api_key, model)


def load_master_cv() -> dict:
    """Load master CV from filesystem."""
    cv_path = Path(settings.master_cv_path)
    if not cv_path.exists():
        raise FileNotFoundError(f"Master CV not found at {cv_path}")

    with open(cv_path, encoding="utf-8") as f:
        return json.load(f)
