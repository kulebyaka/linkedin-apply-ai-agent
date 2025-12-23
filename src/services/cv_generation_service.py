"""Service for handling CV generation workflow (MVP)"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict
import asyncio
from concurrent.futures import ThreadPoolExecutor

from src.services.cv_composer import CVComposer
from src.services.pdf_generator import PDFGenerator
from src.llm.provider import LLMClientFactory, LLMProvider
from src.models.mvp import CVGenerationStatus, JobDescriptionInput
from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CVGenerationService:
    """Handles asynchronous CV generation workflow for MVP"""

    def __init__(self):
        """Initialize CV generation service"""
        # In-memory job status store (for MVP - would use DB in production)
        self.jobs: Dict[str, CVGenerationStatus] = {}

        # Thread pool for background processing
        self.executor = ThreadPoolExecutor(max_workers=3)

        # Initialize LLM client
        self.llm_client = self._init_llm_client()

        # Initialize CV composer
        self.cv_composer = CVComposer(
            llm_client=self.llm_client,
            prompts_dir=settings.prompts_dir
        )

        # Initialize PDF generator
        self.pdf_generator = PDFGenerator(
            template_dir=settings.cv_template_dir,
            template_name=settings.cv_template_name
        )

        # Ensure output directory exists
        Path(settings.generated_cvs_dir).mkdir(parents=True, exist_ok=True)

        logger.info("CVGenerationService initialized")

    def _init_llm_client(self):
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

    def submit_job(self, job_input: JobDescriptionInput) -> str:
        """
        Submit a new CV generation job

        Args:
            job_input: Job description input

        Returns:
            job_id for tracking the job
        """
        # Generate unique job ID
        job_id = str(uuid.uuid4())

        # Create initial status
        status = CVGenerationStatus(
            job_id=job_id,
            status="queued",
            created_at=datetime.now(),
            completed_at=None,
            error_message=None,
            pdf_path=None
        )

        self.jobs[job_id] = status
        logger.info(f"Job {job_id} submitted: {job_input.title} at {job_input.company}")

        # Start background processing
        asyncio.create_task(self._process_job_async(job_id, job_input))

        return job_id

    async def _process_job_async(self, job_id: str, job_input: JobDescriptionInput):
        """Process job asynchronously in background"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            self._process_job,
            job_id,
            job_input
        )

    def _process_job(self, job_id: str, job_input: JobDescriptionInput):
        """
        Process CV generation job in background thread

        Args:
            job_id: Job ID
            job_input: Job description input
        """
        try:
            logger.info(f"Processing job {job_id}")

            # Update status: analyzing job
            self._update_status(job_id, "analyzing_job")

            # Load master CV
            master_cv = self._load_master_cv()

            # Prepare job posting dict for CVComposer
            job_posting = {
                "title": job_input.title,
                "company": job_input.company,
                "description": job_input.description,
                "requirements": job_input.requirements or ""
            }

            # Update status: composing CV
            self._update_status(job_id, "composing_cv")

            # Generate tailored CV
            tailored_cv = self.cv_composer.compose_cv(
                master_cv=master_cv,
                job_posting=job_posting
            )

            # Update status: generating PDF
            self._update_status(job_id, "generating_pdf")

            # Generate PDF filename
            safe_company = "".join(c for c in job_input.company if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_title = "".join(c for c in job_input.title if c.isalnum() or c in (' ', '-', '_')).strip()
            pdf_filename = f"{job_id}_{safe_company}_{safe_title}.pdf".replace(" ", "_")
            pdf_path = Path(settings.generated_cvs_dir) / pdf_filename

            # Generate PDF
            self.pdf_generator.generate_pdf(
                cv_json=tailored_cv,
                output_path=str(pdf_path),
                metadata={
                    "subject": f"Resume for {job_input.title} at {job_input.company}",
                    "keywords": f"{job_input.company}, {job_input.title}"
                }
            )

            # Update status: completed
            self._update_status(
                job_id,
                "completed",
                pdf_path=str(pdf_path),
                completed_at=datetime.now()
            )

            logger.info(f"Job {job_id} completed successfully. PDF: {pdf_path}")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            self._update_status(
                job_id,
                "failed",
                error_message=str(e),
                completed_at=datetime.now()
            )

    def _load_master_cv(self) -> dict:
        """Load master CV from filesystem"""
        cv_path = Path(settings.master_cv_path)
        if not cv_path.exists():
            raise FileNotFoundError(f"Master CV not found at {cv_path}")

        with open(cv_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _update_status(
        self,
        job_id: str,
        status: str,
        error_message: str = None,
        pdf_path: str = None,
        completed_at: datetime = None
    ):
        """Update job status"""
        if job_id in self.jobs:
            self.jobs[job_id].status = status
            if error_message:
                self.jobs[job_id].error_message = error_message
            if pdf_path:
                self.jobs[job_id].pdf_path = pdf_path
            if completed_at:
                self.jobs[job_id].completed_at = completed_at

    def get_status(self, job_id: str) -> CVGenerationStatus:
        """
        Get status of a job

        Args:
            job_id: Job ID

        Returns:
            Job status

        Raises:
            KeyError: If job not found
        """
        if job_id not in self.jobs:
            raise KeyError(f"Job {job_id} not found")
        return self.jobs[job_id]

    def get_pdf_path(self, job_id: str) -> Path:
        """
        Get PDF path for a completed job

        Args:
            job_id: Job ID

        Returns:
            Path to PDF file

        Raises:
            KeyError: If job not found
            ValueError: If job not completed or failed
        """
        status = self.get_status(job_id)

        if status.status == "failed":
            raise ValueError(f"Job {job_id} failed: {status.error_message}")

        if status.status != "completed":
            raise ValueError(f"Job {job_id} not completed yet (status: {status.status})")

        if not status.pdf_path:
            raise ValueError(f"Job {job_id} completed but PDF path not set")

        pdf_path = Path(status.pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        return pdf_path
