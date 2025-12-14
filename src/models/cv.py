"""CV data models"""

from typing import List, Optional
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


class Skill(BaseModel):
    """Skill entry"""
    name: str
    category: str  # e.g., "Programming Languages", "Frameworks", "Tools"
    proficiency: Optional[str] = None  # e.g., "Expert", "Intermediate", "Beginner"


class CV(BaseModel):
    """Complete CV model"""
    contact: ContactInfo
    summary: str
    experiences: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    languages: List[dict] = Field(default_factory=list)  # {"language": "English", "level": "Native"}


class TailoredCV(BaseModel):
    """Tailored CV for a specific job"""
    job_id: str
    cv: CV
    tailoring_notes: str
    created_at: str
