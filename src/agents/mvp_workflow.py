"""Simplified LangGraph workflow for MVP CV generation"""

import logging
import json
from typing import TypedDict
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from src.services.cv_composer import CVComposer
from src.services.pdf_generator import PDFGenerator
from src.llm.provider import LLMClientFactory, LLMProvider
from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MVPWorkflowState(TypedDict):
    """State structure for MVP CV generation workflow"""
    # Tracking
    job_id: str

    # Input
    job_posting: dict  # {title, company, description, requirements}
    master_cv: dict

    # Processing
    tailored_cv_json: dict
    tailored_cv_pdf_path: str

    # Status
    current_step: str
    error_message: str | None


def create_mvp_workflow() -> StateGraph:
    """
    Create simplified workflow for MVP:
    1. Compose Tailored CV (LLM)
    2. Generate PDF
    3. END (return PDF path)

    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(MVPWorkflowState)

    # Add nodes
    workflow.add_node("compose_cv", compose_cv_node)
    workflow.add_node("generate_pdf", generate_pdf_node)

    # Define flow
    workflow.set_entry_point("compose_cv")
    workflow.add_edge("compose_cv", "generate_pdf")
    workflow.add_edge("generate_pdf", END)

    # Compile with checkpointer for state persistence
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def compose_cv_node(state: MVPWorkflowState) -> MVPWorkflowState:
    """
    Compose tailored CV using LLM based on job description

    Args:
        state: Current workflow state

    Returns:
        Updated state with tailored_cv_json
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Starting CV composition for job {job_id}")
    state["current_step"] = "composing_cv"

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
            raise ValueError("Master CV not provided in workflow state")
        if not job_posting:
            raise ValueError("Job posting not provided in workflow state")

        # Compose tailored CV
        logger.info(f"Composing CV for job {job_id}: {job_posting.get('title')} at {job_posting.get('company')}")
        tailored_cv = cv_composer.compose_cv(
            master_cv=master_cv,
            job_posting=job_posting
        )

        # Update state
        state["tailored_cv_json"] = tailored_cv
        state["current_step"] = "cv_composed"
        logger.info(f"CV composition completed successfully for job {job_id}")

    except Exception as e:
        logger.error(f"CV composition failed for job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"CV composition failed: {str(e)}"
        state["tailored_cv_json"] = None

    return state


def generate_pdf_node(state: MVPWorkflowState) -> MVPWorkflowState:
    """
    Generate PDF from tailored CV JSON

    Args:
        state: Current workflow state

    Returns:
        Updated state with tailored_cv_pdf_path
    """
    job_id = state.get("job_id", "unknown")
    logger.info(f"Starting PDF generation for job {job_id}")
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
        safe_company = "".join(c for c in company if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_title = "".join(c for c in job_title if c.isalnum() or c in (' ', '-', '_')).strip()

        # Get candidate name from CV
        candidate_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        safe_name = "".join(c for c in candidate_name if c.isalnum() or c in (' ', '-', '_')).strip()

        # Create filename
        pdf_filename = f"{safe_name}_{safe_company}_{safe_title}.pdf".replace(" ", "_")
        output_dir = Path(settings.generated_cvs_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / pdf_filename

        # Initialize PDF generator
        generator = PDFGenerator(
            template_dir=settings.cv_template_dir,
            template_name=settings.cv_template_name
        )

        # Generate PDF
        logger.info(f"Generating PDF for job {job_id}: {output_path}")
        pdf_path = generator.generate_pdf(
            cv_json=cv_json,
            output_path=str(output_path),
            metadata={
                "subject": f"Resume for {job_title} at {company}",
                "keywords": f"{company}, {job_title}"
            }
        )

        # Update state
        state["tailored_cv_pdf_path"] = pdf_path
        state["current_step"] = "completed"
        logger.info(f"PDF generated successfully for job {job_id}: {pdf_path}")

    except Exception as e:
        logger.error(f"PDF generation failed for job {job_id}: {e}", exc_info=True)
        state["error_message"] = f"PDF generation failed: {str(e)}"
        state["tailored_cv_pdf_path"] = None
        state["current_step"] = "failed"

    return state


def _init_llm_client():
    """Initialize LLM client based on settings"""
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


def load_master_cv() -> dict:
    """Load master CV from filesystem"""
    cv_path = Path(settings.master_cv_path)
    if not cv_path.exists():
        raise FileNotFoundError(f"Master CV not found at {cv_path}")

    with open(cv_path, 'r', encoding='utf-8') as f:
        return json.load(f)
