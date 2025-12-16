"""Contextual Relevancy evaluation tests - ensure correct content selection

These tests verify that the CV composer selects the MOST RELEVANT content
from the master CV based on the job requirements. The system should prioritize
experiences and skills that match the job, not just include everything.
"""

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import ContextualRelevancyMetric


@pytest.mark.eval
class TestCVContextualRelevancy:
    """
    Contextual Relevancy tests

    These tests ensure the system selects the right content from the master CV.
    Not all experience is equally relevant - the tailored CV should prioritize
    what matters most for the specific job.
    """

    def test_experience_selection_relevancy(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that selected experiences are contextually relevant to job

        When a master CV has multiple experiences, the tailored CV should
        prioritize those most relevant to the job. The order and emphasis
        should reflect job requirements.

        Threshold: 0.7 (high relevancy expected for experience selection)
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Build retrieval context: ALL master experiences
        retrieval_context = []
        for exp in sample_master_cv['experiences']:
            context_text = (
                f"{exp['company']}: {exp['position']}\n"
                f"{exp.get('description', '')}\n"
                f"Technologies: {', '.join(exp.get('technologies', []))}"
            )
            retrieval_context.append(context_text)

        # Build output: TOP 3 selected/reordered experiences
        top_experiences = tailored_cv['experiences'][:3]
        expected_output = "\n\n".join([
            f"{exp['company']}: {exp['position']}\n{exp.get('description', '')}"
            for exp in top_experiences
        ])

        # Create test case
        test_case = LLMTestCase(
            input=sample_job_posting['description'],
            actual_output=expected_output,
            retrieval_context=retrieval_context
        )

        # Use Contextual Relevancy metric
        metric = ContextualRelevancyMetric(
            threshold=eval_thresholds['contextual_relevancy'],  # 0.7
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_project_selection_relevancy(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test that selected projects are contextually relevant

        If the master CV has multiple projects, the tailored CV should
        highlight those most relevant to the job posting.

        Threshold: 0.7 (high relevancy for project selection)
        """
        # Skip if no projects in master CV
        if not sample_master_cv.get('projects'):
            pytest.skip("No projects in sample master CV")

        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Build retrieval context: ALL master projects
        retrieval_context = []
        for proj in sample_master_cv.get('projects', []):
            context_text = (
                f"{proj['name']}: {proj.get('description', '')}\n"
                f"Technologies: {', '.join(proj.get('technologies', []))}"
            )
            retrieval_context.append(context_text)

        # Build output: Selected projects in tailored CV
        tailored_projects = tailored_cv.get('projects', [])
        if not tailored_projects:
            pytest.skip("No projects in tailored CV")

        expected_output = "\n\n".join([
            f"{proj['name']}: {proj.get('description', '')}"
            for proj in tailored_projects
        ])

        # Create test case
        test_case = LLMTestCase(
            input=sample_job_posting['description'],
            actual_output=expected_output,
            retrieval_context=retrieval_context
        )

        # Use Contextual Relevancy metric
        metric = ContextualRelevancyMetric(
            threshold=eval_thresholds['contextual_relevancy'],
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_skills_prioritization_relevancy(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model
    ):
        """
        Test that skills are prioritized based on job relevance

        The skills section should be reordered to show job-relevant skills first.
        This tests that the TOP skills in the tailored CV are contextually
        relevant to the job requirements.

        Threshold: 0.7
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Extract all skills from master CV
        master_skills = self._extract_all_skills(sample_master_cv)
        retrieval_context = [
            f"Available skills: {', '.join(master_skills)}"
        ]

        # Extract top 5 skills from tailored CV
        tailored_skills = self._extract_all_skills(tailored_cv)
        top_skills = tailored_skills[:5]

        # Create test case
        test_case = LLMTestCase(
            input=f"Job requirements: {sample_job_posting.get('requirements', sample_job_posting['description'])}",
            actual_output=f"Top candidate skills: {', '.join(top_skills)}",
            retrieval_context=retrieval_context
        )

        # Use Contextual Relevancy metric
        metric = ContextualRelevancyMetric(
            threshold=0.7,
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    # Helper methods

    def _extract_all_skills(self, cv: dict) -> list:
        """Extract all skill names from CV"""
        skills = []
        skills_data = cv.get('skills', [])

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


@pytest.mark.eval
class TestContextualRelevancyEdgeCases:
    """Edge cases for contextual relevancy"""

    def test_single_experience_still_relevant(
        self,
        cv_composer_eval,
        deepeval_model
    ):
        """
        Test that even with a single experience, it's contextually relevant

        When master CV has only one experience, the tailored CV should
        still present it in a way that's relevant to the job.
        """
        # Create minimal master CV with single experience
        minimal_cv = {
            "contact": {
                "email": "test@example.com",
                "phone": "555-0100"
            },
            "summary": "Software engineer with Python experience",
            "experiences": [
                {
                    "company": "Tech Corp",
                    "position": "Software Engineer",
                    "start_date": "2020-01-01",
                    "end_date": None,
                    "is_current": True,
                    "location": "Remote",
                    "description": "Full stack development with Python and React",
                    "achievements": [
                        "Built RESTful APIs with Django",
                        "Deployed on AWS with Docker"
                    ],
                    "technologies": ["Python", "Django", "AWS", "Docker", "React"]
                }
            ],
            "education": [],
            "skills": [{"name": "Python"}, {"name": "Django"}, {"name": "AWS"}],
            "projects": [],
            "certifications": [],
            "languages": [{"language": "English", "level": "Native"}]
        }

        job = {
            "title": "Backend Python Developer",
            "company": "Startup",
            "description": "Python backend developer needed for Django project",
            "requirements": "Python, Django, AWS experience required"
        }

        # Act
        tailored_cv = cv_composer_eval.compose_cv(minimal_cv, job)

        # The single experience should still be contextually relevant
        retrieval_context = [
            "Tech Corp: Software Engineer - Full stack development with Python and React"
        ]

        output = (
            f"{tailored_cv['experiences'][0]['company']}: "
            f"{tailored_cv['experiences'][0]['description']}"
        )

        test_case = LLMTestCase(
            input=job['description'],
            actual_output=output,
            retrieval_context=retrieval_context
        )

        metric = ContextualRelevancyMetric(
            threshold=0.7,
            model=deepeval_model,
            include_reason=True
        )

        assert_test(test_case, [metric])
