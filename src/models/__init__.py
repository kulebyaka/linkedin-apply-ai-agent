"""Data models and schemas"""

from .unified import (
    JobSubmitRequest,
    JobSubmitResponse,
    HITLDecision,
    HITLDecisionResponse,
    PendingApproval,
    JobStatus,
    JobRecord,
    JobStatusResponse,
    ApplicationHistoryItem,
)
from .mvp import JobDescriptionInput, CVGenerationResponse, CVGenerationStatus
from .job import JobPosting, JobFilter, JobEvaluation
from .application import ApplicationStatus, UserApproval, ApplicationResult
from .cv import CV, ContactInfo, Experience, Education, Skill, Project

__all__ = [
    # Unified models
    "JobSubmitRequest",
    "JobSubmitResponse",
    "HITLDecision",
    "HITLDecisionResponse",
    "PendingApproval",
    "JobStatus",
    "JobRecord",
    "JobStatusResponse",
    "ApplicationHistoryItem",
    # MVP models
    "JobDescriptionInput",
    "CVGenerationResponse",
    "CVGenerationStatus",
    # Job models
    "JobPosting",
    "JobFilter",
    "JobEvaluation",
    # Application models
    "ApplicationStatus",
    "UserApproval",
    "ApplicationResult",
    # CV models
    "CV",
    "ContactInfo",
    "Experience",
    "Education",
    "Skill",
    "Project",
]
