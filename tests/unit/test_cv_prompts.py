"""Tests for CV prompt management"""

import pytest
from pathlib import Path
import tempfile
import shutil
from src.services.cv_prompts import PromptLoader, CVPromptManager


class TestPromptLoader:
    """Test PromptLoader class"""

    @pytest.fixture
    def temp_prompts_dir(self):
        """Create temporary prompts directory with test files"""
        temp_dir = tempfile.mkdtemp()
        prompts_dir = Path(temp_dir) / "prompts"
        prompts_dir.mkdir()

        # Create example prompts directory
        examples_dir = prompts_dir / "examples"
        examples_dir.mkdir()

        # Create test prompt files
        (examples_dir / "test_prompt.txt").write_text("This is a test prompt with $variable")
        (examples_dir / "another_prompt.txt").write_text("Another prompt: $name, $value")

        yield prompts_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    def test_init_creates_directory(self, temp_prompts_dir):
        """Test that PromptLoader creates prompts directory if it doesn't exist"""
        new_dir = temp_prompts_dir / "new_prompts"
        loader = PromptLoader(new_dir)

        assert new_dir.exists()
        assert loader.prompts_dir == new_dir

    def test_init_copies_examples(self, temp_prompts_dir):
        """Test that example prompts are copied to main directory"""
        loader = PromptLoader(temp_prompts_dir)

        # Check that examples were copied
        assert (temp_prompts_dir / "test_prompt.txt").exists()
        assert (temp_prompts_dir / "another_prompt.txt").exists()

    def test_load_prompt_success(self, temp_prompts_dir):
        """Test loading a prompt file successfully"""
        loader = PromptLoader(temp_prompts_dir)
        prompt = loader.load("test_prompt")

        assert "This is a test prompt with $variable" in prompt

    def test_load_prompt_not_found(self, temp_prompts_dir):
        """Test loading non-existent prompt raises FileNotFoundError"""
        loader = PromptLoader(temp_prompts_dir)

        with pytest.raises(FileNotFoundError) as exc_info:
            loader.load("nonexistent")

        assert "Prompt file not found" in str(exc_info.value)

    def test_load_with_cache(self, temp_prompts_dir):
        """Test that caching works correctly"""
        loader = PromptLoader(temp_prompts_dir)

        # Load twice
        prompt1 = loader.load("test_prompt", use_cache=True)
        prompt2 = loader.load("test_prompt", use_cache=True)

        assert prompt1 == prompt2
        assert "test_prompt" in loader._cache

    def test_load_without_cache(self, temp_prompts_dir):
        """Test loading without cache"""
        loader = PromptLoader(temp_prompts_dir)

        # Load with cache disabled
        prompt = loader.load("test_prompt", use_cache=False)

        assert "test_prompt" not in loader._cache

    def test_reload_specific_prompt(self, temp_prompts_dir):
        """Test reloading a specific prompt"""
        loader = PromptLoader(temp_prompts_dir)

        # Load and cache
        loader.load("test_prompt")
        assert "test_prompt" in loader._cache

        # Reload
        loader.reload("test_prompt")
        assert "test_prompt" not in loader._cache

    def test_reload_all_prompts(self, temp_prompts_dir):
        """Test reloading all prompts"""
        loader = PromptLoader(temp_prompts_dir)

        # Load multiple prompts
        loader.load("test_prompt")
        loader.load("another_prompt")

        assert len(loader._cache) == 2

        # Reload all
        loader.reload()
        assert len(loader._cache) == 0

    def test_list_available(self, temp_prompts_dir):
        """Test listing available prompts"""
        loader = PromptLoader(temp_prompts_dir)
        available = loader.list_available()

        assert "test_prompt" in available
        assert "another_prompt" in available

    def test_get_template_with_substitution(self, temp_prompts_dir):
        """Test template variable substitution"""
        loader = PromptLoader(temp_prompts_dir)

        result = loader.get_template("test_prompt", variable="test_value")

        assert "This is a test prompt with test_value" in result

    def test_get_template_multiple_variables(self, temp_prompts_dir):
        """Test template with multiple variables"""
        loader = PromptLoader(temp_prompts_dir)

        result = loader.get_template("another_prompt", name="John", value="42")

        assert "Another prompt: John, 42" in result

    def test_get_template_missing_variable(self, temp_prompts_dir):
        """Test template with missing variable (should use safe_substitute)"""
        loader = PromptLoader(temp_prompts_dir)

        # safe_substitute should leave unmatched variables as-is
        result = loader.get_template("test_prompt")

        assert "$variable" in result  # Variable not substituted


class TestCVPromptManager:
    """Test CVPromptManager class"""

    @pytest.fixture
    def temp_prompts_dir(self):
        """Create temporary prompts directory with CV prompt files"""
        temp_dir = tempfile.mkdtemp()
        prompts_dir = Path(temp_dir) / "prompts"
        prompts_dir.mkdir()

        # Create example prompts
        examples_dir = prompts_dir / "examples"
        examples_dir.mkdir()

        # Create all required prompt files
        prompts = {
            "job_summary": "Analyze job: $job_description",
            "summary": "Role: $current_role, Years: $years_experience, Skills: $key_skills",
            "experience": "Experiences: $experiences, Job: $job_summary",
            "education": "Education: $education, Job: $job_summary",
            "skills": "Skills: $skills, Job: $job_summary",
            "projects": "Projects: $projects, Job: $job_summary",
            "certifications": "Certs: $certifications, Job: $job_summary"
        }

        for name, content in prompts.items():
            (examples_dir / f"{name}.txt").write_text(content)

        yield prompts_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    def test_init(self, temp_prompts_dir):
        """Test CVPromptManager initialization"""
        manager = CVPromptManager(temp_prompts_dir)

        assert manager.loader is not None
        assert manager.loader.prompts_dir == temp_prompts_dir

    def test_get_job_summary_prompt(self, temp_prompts_dir):
        """Test getting job summary prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        prompt = manager.get_job_summary_prompt("Python developer needed")

        assert "Analyze job: Python developer needed" in prompt

    def test_get_summary_prompt(self, temp_prompts_dir):
        """Test getting summary prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        prompt = manager.get_summary_prompt(
            current_role="Software Engineer",
            years_experience=5,
            key_skills=["Python", "Django"],
            achievements=["Built system", "Led team"],
            job_summary={"technical_skills": ["Python"]}
        )

        assert "Role: Software Engineer" in prompt
        assert "Years: 5" in prompt
        assert "Skills: Python, Django" in prompt

    def test_get_experience_prompt(self, temp_prompts_dir):
        """Test getting experience prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        experiences = [{"company": "Tech Corp", "position": "Engineer"}]
        job_summary = {"technical_skills": ["Python"]}

        prompt = manager.get_experience_prompt(experiences, job_summary)

        assert "Experiences:" in prompt
        assert "Tech Corp" in prompt

    def test_get_education_prompt(self, temp_prompts_dir):
        """Test getting education prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        education = [{"institution": "Stanford", "degree": "BS"}]
        job_summary = {"education_reqs": ["Bachelor's"]}

        prompt = manager.get_education_prompt(education, job_summary)

        assert "Education:" in prompt
        assert "Stanford" in prompt

    def test_get_skills_prompt(self, temp_prompts_dir):
        """Test getting skills prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        skills = [{"name": "Python", "category": "Languages"}]
        job_summary = {"technical_skills": ["Python"]}

        prompt = manager.get_skills_prompt(skills, job_summary)

        assert "Skills:" in prompt
        assert "Python" in prompt

    def test_get_projects_prompt(self, temp_prompts_dir):
        """Test getting projects prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        projects = [{"name": "ML Library", "description": "Machine learning lib"}]
        job_summary = {"technical_skills": ["Python", "ML"]}

        prompt = manager.get_projects_prompt(projects, job_summary)

        assert "Projects:" in prompt
        assert "ML Library" in prompt

    def test_get_certifications_prompt(self, temp_prompts_dir):
        """Test getting certifications prompt"""
        manager = CVPromptManager(temp_prompts_dir)

        certifications = ["AWS Certified", "Docker Certified"]
        job_summary = {"technical_skills": ["AWS"]}

        prompt = manager.get_certifications_prompt(certifications, job_summary)

        assert "Certs:" in prompt
        assert "AWS Certified" in prompt
