"""CV data models"""

from typing import List, Optional, Literal
from datetime import date
from pydantic import BaseModel, Field, EmailStr


class ContactInfo(BaseModel):
    """Contact information"""
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None


class CompanyContext(BaseModel):
    """Optional company context information"""
    industry: Optional[str] = None
    size: Optional[str] = None
    notable_clients: List[str] = Field(default_factory=list)


class ExperienceProject(BaseModel):
    """Project within a work experience"""
    name: str
    role: Optional[str] = None
    description: str
    achievements: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    duration: Optional[str] = None  # e.g., "2021-2023" or "2+ years"


class Experience(BaseModel):
    """Work experience entry"""
    company: str
    position: str
    start_date: date
    end_date: Optional[date] = None
    is_current: bool = False
    location: Optional[str] = None
    description: str
    achievements: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    projects: List[ExperienceProject] = Field(default_factory=list)  # NEW
    company_context: Optional[CompanyContext] = None  # NEW


class Education(BaseModel):
    """Education entry"""
    institution: str
    degree: str
    field_of_study: str
    start_date: date
    end_date: Optional[date] = None
    gpa: Optional[str] = None
    achievements: List[str] = Field(default_factory=list)


class Project(BaseModel):
    """Project entry"""
    name: str
    description: str
    url: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    status: Optional[Literal["active", "archived", "production", "completed"]] = None  # NEW
    last_updated: Optional[date] = None  # NEW
    role: Optional[str] = None  # NEW: e.g., "Creator & Maintainer", "Contributor"
    architecture: List[str] = Field(default_factory=list)  # NEW: e.g., ["Microservices", "Event-driven"]
    visibility: Optional[Literal["public", "private"]] = None  # NEW


class Skill(BaseModel):
    """Skill entry"""
    name: str
    category: str  # e.g., "Programming Languages", "Frameworks", "Tools"
    proficiency: Optional[str] = None  # e.g., "Expert", "Intermediate", "Beginner"
    years_of_experience: Optional[str] = None  # NEW: e.g., "10+", "5-7", "3"
    use_cases: List[str] = Field(default_factory=list)  # NEW: Optional usage examples


class Certification(BaseModel):
    """Certification entry"""
    name: str
    issuer: str  # NEW: e.g., "Amazon", "AlgoExpert"
    date: Optional[str] = None  # NEW: e.g., "2020-06" or "2020"
    description: Optional[str] = None  # NEW
    topics: List[str] = Field(default_factory=list)  # NEW: Topics covered


class Language(BaseModel):
    """Language proficiency"""
    language: str
    level: str  # e.g., "Native", "Professional Working Proficiency", "B2"


class Interests(BaseModel):
    """Interests and hobbies"""
    technical: List[str] = Field(default_factory=list)
    sports: List[str] = Field(default_factory=list)
    other: List[str] = Field(default_factory=list)


class CV(BaseModel):
    """Complete CV model"""
    contact: ContactInfo
    summary: str
    experiences: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)  # UPDATED: now objects
    languages: List[Language] = Field(default_factory=list)  # UPDATED: now objects
    interests: Optional[Interests] = None  # NEW


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
    end_date: Optional[str] = None
    is_current: bool = False
    location: Optional[str] = None
    description: str
    achievements: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    projects: List[ExperienceProject] = Field(default_factory=list)
    company_context: Optional[CompanyContext] = None


class EducationLLM(BaseModel):
    """Education entry as returned by LLM (string dates)"""
    institution: str
    degree: str
    field_of_study: str
    start_date: str
    end_date: Optional[str] = None
    is_current: bool = False
    location: Optional[str] = None
    gpa: Optional[str] = None
    achievements: List[str] = Field(default_factory=list)


class ProjectLLM(BaseModel):
    """Project entry as returned by LLM (string dates)"""
    name: str
    description: str
    url: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    status: Optional[str] = None  # Relaxed from Literal for LLM flexibility
    last_updated: Optional[str] = None
    role: Optional[str] = None
    architecture: List[str] = Field(default_factory=list)
    visibility: Optional[str] = None


class CVLLMOutput(BaseModel):
    """
    Schema for LLM-generated CV sections.

    This model represents the structure expected from the LLM when generating
    a tailored CV. Uses string types for dates since LLMs output date strings
    that the main CV model will parse.

    Usage:
        schema = CVLLMOutput.model_json_schema()
        result = llm.generate_json(prompt, schema=schema)
    """
    summary: str
    experiences: List[ExperienceLLM] = Field(default_factory=list)
    education: List[EducationLLM] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)  # No date fields, reuse
    projects: List[ProjectLLM] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)  # No date fields


class ExperienceRequirements(BaseModel):
    """Experience requirements from job description"""
    years: Optional[int] = None
    level: Optional[str] = None  # "junior", "mid", "senior", "lead", "staff"


class JobSummary(BaseModel):
    """Structured summary of job requirements extracted from job description"""
    technical_skills: List[str] = Field(default_factory=list)
    soft_skills: List[str] = Field(default_factory=list)
    education_reqs: List[str] = Field(default_factory=list)
    experience_reqs: ExperienceRequirements = Field(default_factory=ExperienceRequirements)
    responsibilities: List[str] = Field(default_factory=list)
    nice_to_have: List[str] = Field(default_factory=list)


class TailoredCV(BaseModel):
    """Tailored CV for a specific job"""
    job_id: str
    cv: CV
    tailoring_notes: str
    created_at: str
