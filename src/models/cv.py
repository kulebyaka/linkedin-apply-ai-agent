"""CV data models"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class ContactInfo(BaseModel):
    """Contact information"""

    full_name: str
    email: EmailStr
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class CompanyContext(BaseModel):
    """Optional company context information"""

    industry: str | None = None
    size: str | None = None
    notable_clients: list[str] = Field(default_factory=list)


class ExperienceProject(BaseModel):
    """Project within a work experience"""

    name: str
    role: str | None = None
    description: str
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    duration: str | None = None  # e.g., "2021-2023" or "2+ years"


class Experience(BaseModel):
    """Work experience entry"""

    company: str
    position: str
    start_date: date
    end_date: date | None = None
    is_current: bool = False
    location: str | None = None
    description: str
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    projects: list[ExperienceProject] = Field(default_factory=list)  # NEW
    company_context: CompanyContext | None = None  # NEW


class Education(BaseModel):
    """Education entry"""

    institution: str
    degree: str
    field_of_study: str
    start_date: date
    end_date: date | None = None
    gpa: str | None = None
    achievements: list[str] = Field(default_factory=list)


class Project(BaseModel):
    """Project entry"""

    name: str
    description: str
    url: str | None = None
    technologies: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    status: Literal["active", "archived", "production", "completed"] | None = None  # NEW
    last_updated: date | None = None  # NEW
    role: str | None = None  # NEW: e.g., "Creator & Maintainer", "Contributor"
    architecture: list[str] = Field(
        default_factory=list
    )  # NEW: e.g., ["Microservices", "Event-driven"]
    visibility: Literal["public", "private"] | None = None  # NEW


class Skill(BaseModel):
    """Skill entry"""

    name: str
    category: str  # e.g., "Programming Languages", "Frameworks", "Tools"
    proficiency: str | None = None  # e.g., "Expert", "Intermediate", "Beginner"
    years_of_experience: str | None = None  # NEW: e.g., "10+", "5-7", "3"
    use_cases: list[str] = Field(default_factory=list)  # NEW: Optional usage examples


class Certification(BaseModel):
    """Certification entry"""

    name: str
    issuer: str  # NEW: e.g., "Amazon", "AlgoExpert"
    date: str | None = None  # NEW: e.g., "2020-06" or "2020"
    description: str | None = None  # NEW
    topics: list[str] = Field(default_factory=list)  # NEW: Topics covered


class Language(BaseModel):
    """Language proficiency"""

    language: str
    level: str  # e.g., "Native", "Professional Working Proficiency", "B2"


class Interests(BaseModel):
    """Interests and hobbies"""

    technical: list[str] = Field(default_factory=list)
    sports: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


class CV(BaseModel):
    """Complete CV model"""

    contact: ContactInfo
    summary: str
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)  # UPDATED: now objects
    languages: list[Language] = Field(default_factory=list)  # UPDATED: now objects
    interests: Interests | None = None  # NEW


# =============================================================================
# LLM Output Models
# =============================================================================
# These models represent LLM-generated output with string dates.
# LLMs return date strings (e.g., "2020-01-15") which the main CV model
# will parse into Python date objects during final validation.


class ExperienceLLM(BaseModel):
    """Experience entry as returned by LLM (string dates)"""

    company: str
    position: str
    start_date: str  # LLM returns string, CV model parses to date
    end_date: str | None = None
    is_current: bool = False
    location: str | None = None
    description: str
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    projects: list[ExperienceProject] = Field(default_factory=list)
    company_context: CompanyContext | None = None


class EducationLLM(BaseModel):
    """Education entry as returned by LLM (string dates)"""

    institution: str
    degree: str
    field_of_study: str
    start_date: str
    end_date: str | None = None
    is_current: bool = False
    location: str | None = None
    gpa: str | None = None
    achievements: list[str] = Field(default_factory=list)


class ProjectLLM(BaseModel):
    """Project entry as returned by LLM (string dates)"""

    name: str
    description: str
    url: str | None = None
    technologies: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    status: str | None = None  # Relaxed from Literal for LLM flexibility
    last_updated: str | None = None
    role: str | None = None
    architecture: list[str] = Field(default_factory=list)
    visibility: str | None = None


class CVLLMOutput(BaseModel):
    """
    Schema for complete tailored CV including LLM-generated and pass-through fields.

    This model represents the complete CV structure after tailoring. LLM generates
    summary, experiences, education, skills, projects, and certifications.
    Contact, languages, and interests are pass-through from master CV.

    Uses string types for dates since LLMs output date strings.

    Usage:
        schema = CVLLMOutput.model_json_schema()
        result = llm.generate_json(prompt, schema=schema)
    """

    contact: ContactInfo | None = None  # Pass-through from master CV, not LLM-generated
    summary: str
    experiences: list[ExperienceLLM] = Field(default_factory=list)
    education: list[EducationLLM] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)  # No date fields, reuse
    projects: list[ProjectLLM] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)  # No date fields
    languages: list[Language] = Field(default_factory=list)  # Pass-through from master CV
    interests: Interests | None = None  # Pass-through from master CV


class ExperienceRequirements(BaseModel):
    """Experience requirements from job description"""

    years: int | None = None
    level: str | None = None  # "junior", "mid", "senior", "lead", "staff"


class JobSummary(BaseModel):
    """Structured summary of job requirements extracted from job description"""

    technical_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    education_reqs: list[str] = Field(default_factory=list)
    experience_reqs: ExperienceRequirements = Field(default_factory=ExperienceRequirements)
    responsibilities: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)


class TailoredCV(BaseModel):
    """Tailored CV for a specific job"""

    job_id: str
    cv: CV
    tailoring_notes: str
    created_at: str
