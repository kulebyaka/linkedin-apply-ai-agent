"""Answer Relevancy evaluation tests - ensure CV addresses job requirements

These tests verify that the tailored CV is relevant to the job posting.
The CV should highlight experience, skills, and achievements that match
the job requirements.
"""

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric


@pytest.mark.eval
class TestCVAnswerRelevancy:
    """
    Answer Relevancy tests

    These tests ensure the tailored CV is relevant to the job requirements.
    The professional summary, experience highlights, and skills should all
    align with what the job posting asks for.
    """

    def test_summary_relevancy_high(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that professional summary is highly relevant to job

        The summary is the first thing recruiters see. It should directly
        address the key requirements of the job posting.

        Threshold: 0.7 (high relevancy required)
        """
        # Act - Generate tailored CV
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)
        summary = tailored_cv['summary']

        # Build context: job description + requirements
        job_context = (
            f"Job Title: {sample_job_posting['title']}\n"
            f"Description: {sample_job_posting['description']}\n"
            f"Requirements: {sample_job_posting.get('requirements', 'N/A')}"
        )

        # Create test case
        test_case = LLMTestCase(
            input=job_context,
            actual_output=summary,
            context=[sample_master_cv['summary']]  # Original summary as context
        )

        # Use DeepEval Answer Relevancy metric
        metric = AnswerRelevancyMetric(
            threshold=eval_thresholds['answer_relevancy'],  # 0.7 from config
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_experience_relevancy(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that experience section emphasizes relevant experience

        The tailored experience should highlight roles and achievements
        that match the job requirements. Less relevant experiences may
        still be included but de-emphasized.

        Threshold: 0.6 (moderate - may include some general background)
        """
        # First, get job summary to understand requirements
        job_summary = cv_composer_eval._summarize_job(sample_job_posting)
        required_skills = job_summary.get('technical_skills', [])

        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)
        experiences = tailored_cv['experiences']

        # Build experience text focused on top entries
        experience_text = self._build_experience_text(experiences[:3])  # Top 3 most relevant

        # Build query about job requirements
        job_query = f"Job requires: {', '.join(required_skills)}"

        # Create test case
        test_case = LLMTestCase(
            input=job_query,
            actual_output=experience_text,
            retrieval_context=[str(sample_master_cv['experiences'])]
        )

        # Use Answer Relevancy metric with moderate threshold
        metric = AnswerRelevancyMetric(
            threshold=0.6,  # Moderate threshold for experience
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_skills_relevancy(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that skills are reordered by job relevance

        The skills section should prioritize skills mentioned in the job posting.
        Highly relevant skills should appear first.

        Threshold: 0.7 (high - skills should tightly match job)
        """
        # Get job requirements
        job_summary = cv_composer_eval._summarize_job(sample_job_posting)
        required_skills = job_summary.get('technical_skills', [])

        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Extract skill names from tailored CV
        skills_list = self._extract_skill_names(tailored_cv.get('skills', []))
        skills_text = ", ".join(skills_list[:10])  # Top 10 skills

        # Create test case
        test_case = LLMTestCase(
            input=f"Job requires these skills: {', '.join(required_skills)}",
            actual_output=f"Candidate has: {skills_text}",
            context=[str(sample_master_cv.get('skills', []))]
        )

        # Use Answer Relevancy metric
        metric = AnswerRelevancyMetric(
            threshold=eval_thresholds['answer_relevancy'],  # 0.7
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_full_cv_addresses_job(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that the full tailored CV addresses the job posting

        This is an end-to-end test checking that the complete CV
        is relevant to the job requirements.

        Threshold: 0.7 (high relevancy expected)
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Build full CV text
        cv_text = self._build_full_cv_text(tailored_cv)

        # Build job requirements text
        job_text = (
            f"Job Title: {sample_job_posting['title']}\n"
            f"Company: {sample_job_posting['company']}\n"
            f"Description: {sample_job_posting['description']}\n"
            f"Requirements: {sample_job_posting.get('requirements', '')}"
        )

        # Create test case
        test_case = LLMTestCase(
            input=job_text,
            actual_output=cv_text,
            context=[str(sample_master_cv)]
        )

        # Use Answer Relevancy metric
        metric = AnswerRelevancyMetric(
            threshold=eval_thresholds['answer_relevancy'],
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    # Helper methods

    def _build_experience_text(self, experiences: list) -> str:
        """Build text representation of experiences"""
        text_parts = []
        for exp in experiences:
            text = f"{exp['position']} at {exp['company']}: {exp.get('description', '')}"

            if exp.get('achievements'):
                text += " Achievements: " + "; ".join(exp['achievements'])

            if exp.get('technologies'):
                text += f" Technologies: {', '.join(exp['technologies'])}"

            text_parts.append(text)

        return "\n".join(text_parts)

    def _extract_skill_names(self, skills_data: list) -> list:
        """Extract skill names from skills data structure"""
        skills = []

        for item in skills_data:
            if isinstance(item, str):
                skills.append(item)
            elif isinstance(item, dict):
                if 'name' in item:
                    skills.append(item['name'])
                elif 'skills' in item and isinstance(item['skills'], list):
                    # Categorized skills
                    for skill in item['skills']:
                        if isinstance(skill, str):
                            skills.append(skill)
                        elif isinstance(skill, dict) and 'name' in skill:
                            skills.append(skill['name'])

        return skills

    def _build_full_cv_text(self, cv: dict) -> str:
        """Build complete CV text"""
        sections = []

        # Summary
        if cv.get('summary'):
            sections.append(f"PROFESSIONAL SUMMARY:\n{cv['summary']}")

        # Experience
        if cv.get('experiences'):
            exp_text = "EXPERIENCE:\n" + self._build_experience_text(cv['experiences'])
            sections.append(exp_text)

        # Education
        if cv.get('education'):
            edu_parts = []
            for edu in cv['education']:
                edu_parts.append(
                    f"{edu['degree']} in {edu.get('field_of_study', 'N/A')} from {edu['institution']}"
                )
            sections.append("EDUCATION:\n" + "\n".join(edu_parts))

        # Skills
        skills = self._extract_skill_names(cv.get('skills', []))
        if skills:
            sections.append(f"SKILLS:\n{', '.join(skills)}")

        # Projects
        if cv.get('projects'):
            proj_parts = []
            for proj in cv['projects']:
                proj_parts.append(f"{proj['name']}: {proj.get('description', '')}")
            sections.append("PROJECTS:\n" + "\n".join(proj_parts))

        return "\n\n".join(sections)
