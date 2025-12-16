"""Faithfulness evaluation tests - prevent hallucinations in CV generation

CRITICAL: These tests ensure the CV composer does not fabricate:
- Companies or job titles
- Educational institutions
- Skills or technologies
- Achievements or responsibilities

All data must be traceable to the master CV.
"""

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric
from tests.eval.metrics.cv_hallucination_guard import CVHallucinationGuard
from tests.eval.metrics.cv_schema_compliance import CVSchemaComplianceGuard


@pytest.mark.eval
class TestCVFaithfulness:
    """
    Faithfulness tests - the MOST CRITICAL test suite

    These tests prevent the LLM from inventing experience, education, or skills.
    All failures in this test class should be treated as CRITICAL bugs.
    """

    def test_no_hallucinated_companies(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting
    ):
        """
        CRITICAL: Ensure no companies are fabricated

        Test that all companies in the tailored CV exist in the master CV.
        This is a hard requirement - any fabricated company is a critical failure.
        """
        # Act - Generate tailored CV
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Extract companies from both CVs
        master_companies = {exp['company'] for exp in sample_master_cv['experiences']}
        tailored_companies = {exp['company'] for exp in tailored_cv['experiences']}

        # Assert - Direct strict check
        hallucinated = tailored_companies - master_companies
        assert not hallucinated, (
            f"CRITICAL: Fabricated companies detected: {hallucinated}\n"
            f"Master CV companies: {master_companies}\n"
            f"Tailored CV companies: {tailored_companies}"
        )

    def test_no_hallucinated_institutions(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting
    ):
        """
        CRITICAL: Ensure no educational institutions are fabricated

        Test that all institutions in the tailored CV exist in the master CV.
        Fabricating education credentials is a serious ethical issue.
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Extract institutions
        master_institutions = {edu['institution'] for edu in sample_master_cv.get('education', [])}
        tailored_institutions = {edu['institution'] for edu in tailored_cv.get('education', [])}

        # Assert
        hallucinated = tailored_institutions - master_institutions
        assert not hallucinated, (
            f"CRITICAL: Fabricated institutions detected: {hallucinated}\n"
            f"Master CV institutions: {master_institutions}\n"
            f"Tailored CV institutions: {tailored_institutions}"
        )

    def test_faithfulness_with_custom_guard(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting
    ):
        """
        Test faithfulness using custom CVHallucinationGuard for companies

        This uses our custom DeepEval metric to validate companies.
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Create test case
        test_case = LLMTestCase(
            input=str(sample_job_posting),
            actual_output=tailored_cv  # Pass dict directly
        )

        # Use custom hallucination guard
        guard = CVHallucinationGuard(
            threshold=1.0,
            check_type="companies",
            master_data=sample_master_cv
        )

        # Assert with DeepEval
        assert_test(test_case, [guard])

    def test_faithfulness_using_deepeval_metric(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting,
        deepeval_model,
        eval_thresholds
    ):
        """
        Test overall faithfulness using DeepEval's FaithfulnessMetric

        This uses LLM-based evaluation to check if the tailored CV
        is faithful to the master CV content.
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Build CV text representations
        cv_text = self._build_cv_text(tailored_cv)
        master_text = self._build_cv_text(sample_master_cv)

        # Create test case
        test_case = LLMTestCase(
            input=sample_job_posting['description'],
            actual_output=cv_text,
            retrieval_context=[master_text]
        )

        # Use DeepEval faithfulness metric
        metric = FaithfulnessMetric(
            threshold=eval_thresholds['faithfulness'],  # 0.9 from config
            model=deepeval_model,
            include_reason=True
        )

        # Assert
        assert_test(test_case, [metric])

    def test_institutions_with_custom_guard(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting
    ):
        """
        Test that institutions are not hallucinated using custom guard
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Create test case
        test_case = LLMTestCase(
            input=str(sample_job_posting),
            actual_output=tailored_cv
        )

        # Use custom hallucination guard for institutions
        guard = CVHallucinationGuard(
            threshold=1.0,
            check_type="institutions",
            master_data=sample_master_cv
        )

        # Assert
        assert_test(test_case, [guard])

    def test_schema_compliance(
        self,
        cv_composer_eval,
        sample_master_cv,
        sample_job_posting
    ):
        """
        Test that tailored CV complies with Pydantic schema

        This ensures the output structure is valid, which is a
        prerequisite for all other checks.
        """
        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, sample_job_posting)

        # Create test case
        test_case = LLMTestCase(
            input=str(sample_job_posting),
            actual_output=tailored_cv
        )

        # Use schema compliance guard
        guard = CVSchemaComplianceGuard(threshold=1.0)

        # Assert
        assert_test(test_case, [guard])

    # Helper methods

    def _build_cv_text(self, cv: dict) -> str:
        """Build human-readable CV text for LLM evaluation"""
        sections = []

        # Professional Summary
        if cv.get('summary'):
            sections.append(f"SUMMARY:\n{cv['summary']}")

        # Experience
        if cv.get('experiences'):
            exp_text = "EXPERIENCE:\n"
            for exp in cv['experiences']:
                exp_text += f"\n{exp['position']} at {exp['company']}\n"
                exp_text += f"  {exp.get('description', '')}\n"
                if exp.get('achievements'):
                    exp_text += "  Achievements:\n"
                    for ach in exp['achievements']:
                        exp_text += f"    - {ach}\n"
            sections.append(exp_text)

        # Education
        if cv.get('education'):
            edu_text = "EDUCATION:\n"
            for edu in cv['education']:
                edu_text += f"\n{edu['degree']} in {edu.get('field_of_study', 'N/A')}\n"
                edu_text += f"  {edu['institution']}\n"
            sections.append(edu_text)

        # Skills
        if cv.get('skills'):
            skills_list = []
            for skill_item in cv['skills']:
                if isinstance(skill_item, dict) and 'name' in skill_item:
                    skills_list.append(skill_item['name'])
                elif isinstance(skill_item, str):
                    skills_list.append(skill_item)
            if skills_list:
                sections.append(f"SKILLS:\n{', '.join(skills_list)}")

        # Projects
        if cv.get('projects'):
            proj_text = "PROJECTS:\n"
            for proj in cv['projects']:
                proj_text += f"\n{proj['name']}: {proj.get('description', '')}\n"
            sections.append(proj_text)

        return "\n\n".join(sections)


@pytest.mark.eval
@pytest.mark.expensive
class TestFaithfulnessEdgeCases:
    """
    Edge case testing for faithfulness

    These tests check corner cases and potential failure modes.
    """

    def test_empty_job_posting_no_hallucination(
        self,
        cv_composer_eval,
        sample_master_cv
    ):
        """
        Test that even with minimal job posting, no data is fabricated
        """
        minimal_job = {
            "title": "Software Engineer",
            "company": "Tech Company",
            "description": "Looking for a software engineer.",
            "requirements": "Programming experience required."
        }

        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, minimal_job)

        # Check companies
        master_companies = {exp['company'] for exp in sample_master_cv['experiences']}
        tailored_companies = {exp['company'] for exp in tailored_cv['experiences']}

        assert tailored_companies.issubset(master_companies), \
            "Even with minimal job posting, should not fabricate companies"

    def test_unrelated_job_no_hallucination(
        self,
        cv_composer_eval,
        sample_master_cv
    ):
        """
        Test that for completely unrelated job, no skills/companies are invented

        When CV doesn't match job at all, the system should still use
        real data from master CV, not fabricate matching experience.
        """
        unrelated_job = {
            "title": "Marine Biologist",
            "company": "Ocean Research Institute",
            "description": "Research marine life and ecosystems. Study coral reefs.",
            "requirements": "PhD in Marine Biology, SCUBA certification, field research experience"
        }

        # Act
        tailored_cv = cv_composer_eval.compose_cv(sample_master_cv, unrelated_job)

        # Check that no new companies appeared
        master_companies = {exp['company'] for exp in sample_master_cv['experiences']}
        tailored_companies = {exp['company'] for exp in tailored_cv['experiences']}

        assert tailored_companies.issubset(master_companies), \
            "Should not invent relevant-looking companies even when CV doesn't match job"
