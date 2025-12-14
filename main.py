"""
LinkedIn Job Application Agent - Main Entry Point

This script runs the scheduled job application workflow.
It fetches jobs, filters them, tailors CVs, and manages the application process.
"""

import asyncio
import argparse
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config.settings import get_settings
from src.utils.logger import setup_logger
from src.agents.workflow import create_workflow
from src.models.job import JobFilter

# Initialize
settings = get_settings()
logger = setup_logger("linkedin-apply-ai-agent", level=settings.log_level)


async def run_workflow_once():
    """Run the job application workflow once"""
    logger.info("Starting job application workflow...")

    try:
        # TODO: Load job filters from config or database
        filters = JobFilter(
            keywords=["software engineer", "python developer"],
            remote_only=True,
            experience_levels=["mid-level", "senior"],
        )

        # TODO: Load master CV
        master_cv_path = Path(settings.master_cv_path)
        if not master_cv_path.exists():
            logger.error(f"Master CV not found at {master_cv_path}")
            logger.info("Please create your master CV at data/cv/master_cv.json")
            logger.info("See data/cv/master_cv.example.json for the required format")
            return

        # TODO: Initialize and run LangGraph workflow
        workflow = create_workflow()
        # workflow_result = await workflow.run(...)

        logger.info("Workflow completed successfully")

    except Exception as e:
        logger.error(f"Workflow failed: {e}", exc_info=True)


async def run_scheduler():
    """Run the workflow on a schedule"""
    logger.info(f"Starting LinkedIn Job Application Agent (scheduled mode)")
    logger.info(f"Fetch interval: every {settings.job_fetch_interval_hours} hour(s)")

    scheduler = AsyncIOScheduler()

    # Schedule the workflow
    scheduler.add_job(
        run_workflow_once,
        trigger=IntervalTrigger(hours=settings.job_fetch_interval_hours),
        id="job_application_workflow",
        name="LinkedIn Job Application Workflow",
        replace_existing=True,
    )

    # Run immediately on startup
    await run_workflow_once()

    # Start scheduler
    scheduler.start()

    logger.info("Scheduler started. Press Ctrl+C to exit.")

    # Keep running
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="LinkedIn Job Application Agent"
    )
    parser.add_argument(
        "--mode",
        choices=["once", "schedule"],
        default="schedule",
        help="Run mode: 'once' for single run, 'schedule' for continuous operation"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )

    args = parser.parse_args()

    if args.debug:
        settings.debug = True
        settings.log_level = "DEBUG"

    logger.info(f"LinkedIn Job Application Agent v0.1.0")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Debug: {settings.debug}")

    # Ensure data directories exist
    Path(settings.cv_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.jobs_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.generated_cvs_dir).mkdir(parents=True, exist_ok=True)

    if args.mode == "once":
        asyncio.run(run_workflow_once())
    else:
        asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
