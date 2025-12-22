# Testing Guide - CV Composer

## Overview

This document describes the comprehensive test suite for the CV Composer implementation (Node 3).

## Test Coverage

### Components Tested

✅ **Prompt Management** (`test_cv_prompts.py`)
- PromptLoader class (12 tests)
- CVPromptManager class (8 tests)

✅ **CV Composition** (`test_cv_composer.py`)
- Job summarization (3 tests)
- Section composers (7 tests)
- Validation & hallucination detection (4 tests)
- Full CV composition integration (3 tests)
- Edge cases (2 tests)

**Total: 39 comprehensive tests**

## Quick Start

### 1. Install Test Dependencies

```bash
pip install -r requirements-test.txt
```

### 2. Run All Tests

```bash
pytest
```

### 3. Run with Coverage

```bash
pytest --cov=src/services --cov-report=html
open htmlcov/index.html  # View coverage report
```

## Test Structure

```
tests/
├── README.md                   # Detailed testing documentation
├── conftest.py                 # Shared fixtures and configuration
├── fixtures/                   # Test data
│   ├── master_cv.json         # Sample CV with 3 experiences
│   └── job_posting.json       # Sample job for Python engineer
├── test_cv_prompts.py         # 20 tests for prompt management
└── test_cv_composer.py        # 19 tests for CV composition
```

## Key Test Classes

### Prompt Management Tests

**TestPromptLoader**
- Tests file I/O, caching, and hot-reload
- Tests template variable substitution
- Tests directory auto-creation
- Tests example prompt copying

**TestCVPromptManager**
- Tests high-level prompt retrieval
- Tests JSON serialization in templates
- Tests all section-specific prompts

### CV Composer Tests

**TestJobSummarization**
- Validates LLM extraction of job requirements
- Tests Pydantic model validation
- Tests edge cases (empty descriptions)

**TestSectionComposers**
- Individual tests for each CV section:
  - Professional summary
  - Work experience
  - Education
  - Skills
  - Projects
  - Certifications
- Tests empty section handling

**TestValidation**
- Schema validation with Pydantic
- Hallucination detection for companies
- Hallucination detection for institutions
- Data integrity checks

**TestFullCVComposition**
- End-to-end workflow tests
- Contact info preservation
- Language info preservation
- All sections called correctly

**TestEdgeCases**
- Empty/minimal CVs
- Minimal job postings
- Missing data handling

## Mocking Strategy

### MockLLMClient

All tests use a mock LLM client to avoid real API calls:

```python
class MockLLMClient(BaseLLMClient):
    def set_response(self, prompt_keyword: str, response: dict):
        """Set mock response for prompts containing keyword"""
        self.responses[prompt_keyword] = response
```

**Benefits:**
- ✅ Fast test execution (no network calls)
- ✅ Deterministic results
- ✅ No API costs
- ✅ Ability to test error conditions
- ✅ Parallel test execution

### Example Usage

```python
def test_summarize_job(cv_composer, mock_llm_client, job_posting):
    # Arrange: Set up mock response
    mock_llm_client.set_response("job description", {
        "technical_skills": ["Python", "Django"],
        "soft_skills": ["Communication"],
        # ... rest of response
    })

    # Act: Call the method
    result = cv_composer._summarize_job(job_posting)

    # Assert: Verify output
    assert "Python" in result["technical_skills"]
```

## Test Fixtures

### Shared Fixtures (conftest.py)

- `fixtures_dir`: Path to fixtures directory
- `sample_master_cv`: Complete sample CV
- `sample_job_posting`: Sample job posting
- `sample_job_summary`: Sample job analysis
- `mock_llm_response_*`: Pre-configured mock responses

### Temporary Fixtures

Tests that need file I/O use temporary directories:

```python
@pytest.fixture
def temp_prompts_dir(self):
    temp_dir = tempfile.mkdtemp()
    # ... create test files
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)  # Cleanup
```

## Running Specific Tests

### By File

```bash
pytest tests/test_cv_prompts.py
pytest tests/test_cv_composer.py
```

### By Class

```bash
pytest tests/test_cv_composer.py::TestJobSummarization
pytest tests/test_cv_composer.py::TestValidation
```

### By Method

```bash
pytest tests/test_cv_composer.py::TestJobSummarization::test_summarize_job_basic
```

### By Marker

```bash
pytest -m unit              # Run unit tests only
pytest -m integration       # Run integration tests only
pytest -m "not slow"        # Exclude slow tests
```

## Coverage Goals

| Component | Target | Current |
|-----------|--------|---------|
| cv_composer.py | >95% | To be measured |
| cv_prompts.py | >95% | To be measured |
| Overall | >80% | To be measured |

### Measuring Coverage

```bash
# Generate coverage report
pytest --cov=src/services --cov-report=term-missing

# Generate HTML report
pytest --cov=src/services --cov-report=html

# Coverage with branch analysis
pytest --cov=src/services --cov-branch --cov-report=html
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-test.txt
      - name: Run tests
        run: pytest --cov=src --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

## MVP End-to-End Testing
  
> **Note**: This is a manual integration test flow for the On-Demand CV Generation API.

### 1. Start the API Server

```bash
python -m uvicorn src.api.main:app --reload
```

### 2. Submit a Job (PowerShell)

```powershell
$body = @{
    title = "Senior Python Engineer"
    company = "TechCorp"
    description = "We need a Python expert with FastAPI and AWS experience."
    requirements = "Python, FastAPI, Docker, AWS"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/cv/generate" -Method Post -Body $body -ContentType "application/json"
$job_id = $response.job_id
Write-Host "Job submitted. ID: $job_id"
```

### 3. Poll for Status

Run this loop to check status until completion (~3-5 minutes):

```powershell
while ($true) {
    $status = Invoke-RestMethod -Uri "http://localhost:8000/api/cv/status/$job_id"
    Write-Host "Status: $($status.status) | Step: $($status.current_step)"
    
    if ($status.status -eq "completed") {
        Write-Host "Job Completed! PDF Ready."
        break
    }
    if ($status.status -eq "failed") {
        Write-Host "Job Failed: $($status.error_message)"
        break
    }
    Start-Sleep -Seconds 5
}
```

### 4. Download PDF

```powershell
$downloadUrl = "http://localhost:8000/api/cv/download/$job_id"
Invoke-RestMethod -Uri $downloadUrl -OutFile "tailored_cv.pdf"
Write-Host "PDF downloaded to tailored_cv.pdf"
```

---

## Test Data

### Master CV

Realistic sample CV with:
- **Name**: John Doe
- **Experience**: 8+ years (3 positions)
- **Education**: Stanford BS in CS
- **Skills**: 11 skills across 4 categories
- **Projects**: 2 open source projects
- **Certifications**: 3 current certifications

### Job Posting

"Senior Python Backend Engineer" at "AI Startup" requiring:
- 5+ years Python experience
- Django/Flask expertise
- AWS, Docker, Kubernetes
- Microservices architecture

## Best Practices

### 1. Test Isolation
```python
# ✅ Good: Each test is independent
def test_feature_a(composer):
    result = composer.method_a()
    assert result == expected

def test_feature_b(composer):
    result = composer.method_b()
    assert result == expected

# ❌ Bad: Tests depend on each other
shared_state = None
def test_setup():
    global shared_state
    shared_state = setup()

def test_using_shared():
    assert shared_state.property == value
```

### 2. Clear Assertions
```python
# ✅ Good: One clear assertion per test
def test_summary_contains_skills(result):
    assert "Python" in result["technical_skills"]

# ❌ Bad: Multiple unrelated assertions
def test_everything(result):
    assert "Python" in result["technical_skills"]
    assert len(result["experiences"]) > 0
    assert result["education"] is not None
```

### 3. Descriptive Names
```python
# ✅ Good: Describes what is tested
def test_compose_experiences_reorders_by_relevance()

# ❌ Bad: Vague or unclear
def test_experiences()
def test_1()
```

### 4. AAA Pattern
```python
def test_feature():
    # Arrange: Set up test data
    cv = create_test_cv()
    job = create_test_job()

    # Act: Execute the code under test
    result = composer.compose_cv(cv, job)

    # Assert: Verify the results
    assert result["summary"] is not None
```

## Troubleshooting

### Import Errors

```bash
# Solution 1: Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Solution 2: Run as module
python -m pytest

# Solution 3: Install in editable mode
pip install -e .
```

### Fixtures Not Found

1. Check `conftest.py` exists in tests directory
2. Verify fixture is defined with `@pytest.fixture`
3. Check fixture name matches parameter name

### Mock Not Working

1. Verify keyword in `set_response()` matches prompt text
2. Check mock is set up before method is called
3. Ensure mock client is passed to composer

### Tests Failing Randomly

1. Check for shared state between tests
2. Verify temp directories are cleaned up
3. Look for race conditions in async code
4. Check for external dependencies (should all be mocked)

## Adding New Tests

### 1. Create Test File

```python
# tests/test_new_feature.py
import pytest
from src.services.new_feature import NewFeature

class TestNewFeature:
    """Tests for new feature"""

    def test_basic_functionality(self):
        feature = NewFeature()
        result = feature.process()
        assert result is not None
```

### 2. Add Fixtures

```python
# tests/conftest.py
@pytest.fixture
def new_feature_data():
    return {"key": "value"}
```

### 3. Run New Tests

```bash
pytest tests/test_new_feature.py -v
```

## Maintenance

### Updating Test Data

1. Edit `tests/fixtures/*.json`
2. Run tests to verify changes don't break existing tests
3. Update expected values in tests if needed

### Updating Mocks

When LLM provider changes:
1. Update `MockLLMClient` in `test_cv_composer.py`
2. Update mock responses to match new schema
3. Run full test suite

### Adding Coverage

To add coverage for uncovered code:
1. Run `pytest --cov=src --cov-report=term-missing`
2. Identify uncovered lines
3. Write tests to cover those lines
4. Verify coverage increased

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [Testing Best Practices](https://docs.python-guide.org/writing/tests/)
- [Mocking Guide](https://docs.python.org/3/library/unittest.mock.html)
- [Coverage.py](https://coverage.readthedocs.io/)
