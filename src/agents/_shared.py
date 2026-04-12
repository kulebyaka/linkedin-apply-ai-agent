"""Shared workflow utilities.

Extracts common logic from preparation, retry, and application workflows
to eliminate code duplication. Provides shared functions for:
- Repository access from LangGraph config
- LLM client initialization
- Master CV loading
- CV composition
- PDF generation
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from ..config.settings import get_settings
from ..llm.provider import LLMClientFactory, LLMProvider
from ..services.cv_composer import CVComposer
from ..services.cv_validator import CVValidator, HallucinationPolicy
from ..services.job_repository import JobRepository
from ..services.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)
settings = get_settings()


def get_repository_from_config(config: dict) -> JobRepository:
    """Extract repository from LangGraph config['configurable'].

    Args:
        config: LangGraph RunnableConfig dict.

    Returns:
        JobRepository instance.

    Raises:
        RuntimeError: If repository not found in config.
    """
    configurable = config.get("configurable", {})
    repo = configurable.get("repository")
    if repo is None:
        raise RuntimeError(
            "Repository not found in workflow config. "
            "Pass it via config={'configurable': {'repository': repo}}"
        )
    return repo


def create_llm_client(llm_provider: str | None = None, llm_model: str | None = None):
    """Initialize LLM client based on settings or override parameters.

    Args:
        llm_provider: Optional provider override (openai, anthropic, deepseek, grok).
        llm_model: Optional model override (e.g., gpt-4.1-nano, claude-haiku-4.5).

    Returns:
        Initialized LLM client instance.
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
    """Load master CV from filesystem.

    Returns:
        Master CV as a dictionary.

    Raises:
        FileNotFoundError: If master CV file does not exist.
    """
    cv_path = Path(settings.master_cv_path)
    if not cv_path.exists():
        raise FileNotFoundError(f"Master CV not found at {cv_path}")

    with open(cv_path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_hallucination_policy() -> HallucinationPolicy:
    """Resolve hallucination policy from settings.

    Priority:
    1. Fine-grained cv_composer_hallucination_policy setting
    2. Boolean cv_composer_enable_hallucination_checks (True->STRICT, False->DISABLED)
    """
    policy_str = settings.cv_composer_hallucination_policy
    try:
        return HallucinationPolicy(policy_str)
    except ValueError:
        logger.warning(
            f"Unknown hallucination policy '{policy_str}', "
            f"falling back to enable_hallucination_checks boolean"
        )
        if settings.cv_composer_enable_hallucination_checks:
            return HallucinationPolicy.STRICT
        return HallucinationPolicy.DISABLED


async def compose_cv(
    state: dict,
    *,
    job_id: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    user_feedback: str | None = None,
) -> dict:
    """Compose a tailored CV using LLM.

    Shared logic used by both preparation and retry workflows.

    Args:
        state: Workflow state dict (must contain master_cv and job_posting).
        job_id: Job identifier for logging.
        llm_provider: Optional LLM provider override.
        llm_model: Optional LLM model override.
        user_feedback: Optional user feedback for retry composition.

    Returns:
        Dict with tailored_cv_json (dict or None), error_message (str or None).
    """
    start_time = time.time()

    try:
        llm_client = create_llm_client(llm_provider, llm_model)

        cv_composer = CVComposer(llm_client=llm_client, prompts_dir=settings.prompts_dir)

        master_cv = state.get("master_cv")
        job_posting = state.get("job_posting")

        if not master_cv:
            raise ValueError("Master CV not provided in workflow state")
        if not job_posting:
            raise ValueError("Job posting not provided in workflow state")

        # Resolve hallucination policy from settings
        policy = _resolve_hallucination_policy()
        validator = CVValidator(master_cv=master_cv, policy=policy)

        logger.info(
            f"Composing CV for job {job_id}: "
            f"{job_posting.get('title')} at {job_posting.get('company')}"
        )
        tailored_cv = await asyncio.to_thread(
            cv_composer.compose_cv,
            master_cv=master_cv,
            job_posting=job_posting,
            user_feedback=user_feedback,
            validator=validator,
        )

        elapsed = time.time() - start_time
        logger.info(f"CV composition completed for job {job_id} in {elapsed:.2f}s")

        return {
            "tailored_cv_json": tailored_cv.model_dump(),
            "error_message": None,
        }

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"CV composition failed for job {job_id} in {elapsed:.2f}s: {e}", exc_info=True)
        return {
            "tailored_cv_json": None,
            "error_message": f"CV composition failed: {str(e)}",
        }


async def generate_pdf(
    state: dict,
    *,
    job_id: str,
    version_suffix: str | None = None,
    template_name: str | None = None,
) -> dict:
    """Generate PDF from tailored CV JSON.

    Shared logic used by both preparation and retry workflows.

    Args:
        state: Workflow state dict (must contain tailored_cv_json and job_posting).
        job_id: Job identifier for logging.
        version_suffix: Optional version suffix for filename (e.g., "_v2" for retries).
        template_name: Optional template name override.

    Returns:
        Dict with tailored_cv_pdf_path (str or None), error_message (str or None).
    """
    start_time = time.time()

    cv_json = state.get("tailored_cv_json")
    if not cv_json:
        previous_error = state.get("error_message")
        if previous_error:
            error_msg = f"PDF generation skipped due to previous error: {previous_error}"
        else:
            error_msg = f"PDF generation skipped for job {job_id}: No CV data available"
        logger.error(error_msg)
        return {"tailored_cv_pdf_path": None, "error_message": error_msg}

    try:
        # Get job info for filename
        job_posting = state.get("job_posting", {})
        job_title = job_posting.get("title", "unknown")
        company = job_posting.get("company", "unknown")

        # Generate safe filename components
        safe_company = "".join(
            c for c in company if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        safe_title = "".join(
            c for c in job_title if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        candidate_name = cv_json.get("contact", {}).get("full_name", "Unknown")
        safe_name = "".join(
            c for c in candidate_name if c.isalnum() or c in (" ", "-", "_")
        ).strip()

        # Build filename
        suffix = version_suffix or ""
        pdf_filename = f"{safe_name}_{safe_company}_{safe_title}{suffix}.pdf".replace(" ", "_")
        output_dir = Path(settings.generated_cvs_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / pdf_filename

        # Resolve template name
        effective_template = template_name or settings.cv_template_name

        # Initialize PDF generator
        generator = PDFGenerator(
            template_dir=settings.cv_template_dir, template_name=effective_template
        )

        # Generate PDF (offload blocking WeasyPrint rendering to thread)
        logger.info(f"Generating PDF for job {job_id}: {output_path}")
        pdf_path = await asyncio.to_thread(
            generator.generate_pdf,
            cv_json=cv_json,
            output_path=str(output_path),
            metadata={
                "subject": f"Resume for {job_title} at {company}{' (Retry)' if version_suffix else ''}",
                "keywords": f"{company}, {job_title}",
            },
        )

        elapsed = time.time() - start_time
        logger.info(f"PDF generated for job {job_id} in {elapsed:.2f}s: {pdf_path}")

        return {"tailored_cv_pdf_path": pdf_path, "error_message": None}

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"PDF generation failed for job {job_id} in {elapsed:.2f}s: {e}", exc_info=True)
        return {
            "tailored_cv_pdf_path": None,
            "error_message": f"PDF generation failed: {str(e)}",
        }
