"""LangGraph workflow definition for job application automation"""

from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph


class WorkflowState(TypedDict):
    """State structure for the job application workflow"""

    job_posting: dict
    filters: dict
    master_cv: dict
    is_suitable: bool
    tailored_cv_json: dict
    tailored_cv_pdf_path: str
    user_approval: Literal["approved", "declined", "retry"] | None
    user_feedback: str | None
    application_status: str
    error_message: str | None


def create_workflow() -> StateGraph:
    """
    Create and return the LangGraph workflow for job application automation.

    Workflow steps:
    1. Fetch Jobs
    2. Filter Job (LLM)
    3. Compose Tailored CV (LLM)
    4. Generate PDF
    5. Human Review (HITL)
    6. Apply on LinkedIn
    7. Notification (on error)
    """
    workflow = StateGraph(WorkflowState)

    # Add nodes
    workflow.add_node("fetch_jobs", fetch_jobs_node)
    workflow.add_node("filter_job", filter_job_node)
    workflow.add_node("compose_cv", compose_cv_node)
    workflow.add_node("generate_pdf", generate_pdf_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("apply_linkedin", apply_linkedin_node)
    workflow.add_node("send_notification", send_notification_node)

    # Add edges and conditional routing
    workflow.set_entry_point("fetch_jobs")
    workflow.add_edge("fetch_jobs", "filter_job")

    # Conditional edge: if suitable, continue; else end
    workflow.add_conditional_edges(
        "filter_job", route_after_filter, {"suitable": "compose_cv", "not_suitable": END}
    )

    workflow.add_edge("compose_cv", "generate_pdf")
    workflow.add_edge("generate_pdf", "human_review")

    # Conditional edge: based on user approval
    workflow.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {"approved": "apply_linkedin", "declined": END, "retry": "compose_cv"},
    )

    workflow.add_conditional_edges(
        "apply_linkedin", route_after_application, {"success": END, "failure": "send_notification"}
    )

    workflow.add_edge("send_notification", END)

    return workflow


def fetch_jobs_node(state: WorkflowState) -> WorkflowState:
    """Fetch job postings from LinkedIn"""
    # TODO: Implement job fetching logic
    return state


def filter_job_node(state: WorkflowState) -> WorkflowState:
    """Use LLM to filter job posting based on criteria"""
    # TODO: Implement LLM-based job filtering
    return state


def compose_cv_node(state: WorkflowState) -> WorkflowState:
    """Use LLM to compose tailored CV based on job description"""
    # TODO: Implement CV composition with LLM
    return state


def generate_pdf_node(state: WorkflowState) -> WorkflowState:
    """Generate PDF from tailored CV JSON"""
    from pathlib import Path

    from src.config.settings import get_settings
    from src.services.pdf_generator import PDFGenerator

    settings = get_settings()
    cv_json = state.get("tailored_cv_json")
    job_id = state.get("job_posting", {}).get("id", "unknown")

    if not cv_json:
        logger.error("No tailored CV JSON found in workflow state")
        state["error_message"] = "PDF generation skipped: No CV data available"
        state["tailored_cv_pdf_path"] = None
        return state

    try:
        # Generate unique output filename
        candidate_name = cv_json.get("contact", {}).get("full_name", "Unknown").replace(" ", "_")
        output_dir = Path(settings.generated_cvs_dir)
        output_path = output_dir / f"cv_{job_id}_{candidate_name}.pdf"

        # Initialize PDF generator
        generator = PDFGenerator(
            template_dir=settings.cv_template_dir, template_name=settings.cv_template_name
        )

        # Generate PDF
        pdf_path = generator.generate_pdf(cv_json, output_path)

        # Update state
        state["tailored_cv_pdf_path"] = pdf_path
        logger.info(f"PDF generated successfully: {pdf_path}")

    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)
        state["error_message"] = f"PDF generation failed: {e}"
        state["tailored_cv_pdf_path"] = None

    return state


def human_review_node(state: WorkflowState) -> WorkflowState:
    """Pause workflow for human approval (HITL)"""
    # TODO: Implement HITL mechanism
    return state


def apply_linkedin_node(state: WorkflowState) -> WorkflowState:
    """Apply to job on LinkedIn using browser automation"""
    # TODO: Implement LinkedIn application automation
    return state


def send_notification_node(state: WorkflowState) -> WorkflowState:
    """Send notification on failure"""
    # TODO: Implement notification sending
    return state


def route_after_filter(state: WorkflowState) -> str:
    """Route based on job suitability"""
    return "suitable" if state.get("is_suitable", False) else "not_suitable"


def route_after_human_review(state: WorkflowState) -> str:
    """Route based on user approval decision"""
    approval = state.get("user_approval")
    if approval == "approved":
        return "approved"
    elif approval == "retry":
        return "retry"
    else:
        return "declined"


def route_after_application(state: WorkflowState) -> str:
    """Route based on application success"""
    status = state.get("application_status", "")
    return "success" if status == "success" else "failure"
