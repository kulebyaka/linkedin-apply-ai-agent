# CV Composer Tests

Comprehensive test suite for the CV Composer service with two-tier testing strategy:
- **Unit Tests**: Fast, mocked tests for business logic (tests/unit/)
- **Evaluation Tests**: LLM-based quality evaluation with DeepEval (tests/eval/)

## Test Structure

```
tests/
├── conftest.py                      # Shared fixtures and pytest configuration
├── fixtures/                        # Test data fixtures
│   ├── master_cv.json              # Sample master CV (experienced)
│   ├── master_cv_minimal.json      # Sample CV (junior level)
│   └── job_posting.json            # Sample job posting
│
├── unit/                            # UNIT TESTS (fast, mocked)
│   ├── test_cv_prompts.py          # Prompt management tests
│   ├── test_cv_composer.py         # CV composition logic tests
│   └── test_models.py              # Pydantic model tests
│
├── eval/                            # EVALUATION TESTS (slow, real LLM)
│   ├── conftest.py                 # Eval-specific fixtures
│   ├── test_cv_faithfulness.py     # Hallucination detection (CRITICAL)
│   ├── test_cv_relevancy.py        # Answer relevancy metrics
│   ├── test_cv_contextual.py       # Contextual relevancy metrics
│   ├── test_cv_bias_toxicity.py    # Bias and toxicity detection
│   │
│   ├── metrics/                    # Custom DeepEval metrics
│   │   ├── cv_hallucination_guard.py    # Hallucination detection
│   │   └── cv_schema_compliance.py      # Schema validation
│   │
│   └── fixtures/
│       └── eval_scenarios.json     # Predefined test scenarios
│
└── helpers/                         # Shared utilities
    └── llm_clients.py              # Real LLM client factory
```

## Running Tests

### Install Test Dependencies

```bash
pip install -r requirements-test.txt
```

### Run All Tests

```bash
pytest
```

### Run Specific Test Files

```bash
# Test prompt management only
pytest tests/test_cv_prompts.py

# Test CV composer only
pytest tests/test_cv_composer.py
```

### Run Specific Test Classes

```bash
# Test job summarization
pytest tests/test_cv_composer.py::TestJobSummarization

# Test validation
pytest tests/test_cv_composer.py::TestValidation
```

### Run Specific Tests

```bash
pytest tests/unit/test_cv_composer.py::TestJobSummarization::test_summarize_job_basic
```

### Run with Coverage

```bash
pytest tests/unit/ --cov=src --cov-report=html --cov-report=term-missing
```

Coverage report will be generated in `htmlcov/index.html`

### Run Tests by Markers

```bash
# Run only unit tests (fast, mocked)
pytest -m unit
pytest tests/unit/  # Alternative

# Run only evaluation tests (slow, real LLM)
pytest -m eval
pytest tests/eval/  # Alternative

# Run only integration tests
pytest -m integration

# Exclude expensive tests
pytest -m "not expensive"

# Exclude slow tests
pytest -m "not slow"
```

---

## 🚀 **NEW: DeepEval Evaluation Tests**

### Overview

Evaluation tests use **DeepEval** with **real LLM calls (Grok 4.1-fast)** to measure CV quality:
- **Answer Relevancy**: Does CV address job requirements?
- **Faithfulness**: No hallucinations (fabricated companies/education)?
- **Contextual Relevancy**: Is selected content relevant to job?
- **Bias Detection**: Professional, unbiased language?
- **Toxicity**: No inappropriate content?

### Setup for Evaluation Tests

1. **Set API Key**
   ```bash
   # For Grok (default)
   export XAI_API_KEY="your-xai-api-key"

   # Or for other providers
   export LLM_PROVIDER="openai"  # or "deepseek", "anthropic"
   export OPENAI_API_KEY="your-openai-key"
   ```

2. **Configure Thresholds** (optional)
   ```bash
   export DEEPEVAL_EVALUATOR_MODEL="grok-4.1-fast"
   export EVAL_ANSWER_RELEVANCY_THRESHOLD="0.7"
   export EVAL_FAITHFULNESS_THRESHOLD="0.9"
   export EVAL_BIAS_THRESHOLD="0.9"
   export EVAL_TOXICITY_THRESHOLD="0.8"
   ```

### Running Evaluation Tests

```bash
# Run ALL eval tests (will make API calls, ~$0.10-0.50)
pytest tests/eval/ -v

# Run specific eval test file
pytest tests/eval/test_cv_faithfulness.py -v  # CRITICAL - hallucination tests
pytest tests/eval/test_cv_relevancy.py -v
pytest tests/eval/test_cv_contextual.py -v
pytest tests/eval/test_cv_bias_toxicity.py -v

# Run specific eval test
pytest tests/eval/test_cv_faithfulness.py::TestCVFaithfulness::test_no_hallucinated_companies -v

# Skip eval tests (for CI/local development)
pytest -m "not eval"
```

### Cost Management

| Test Type | API Calls | Cost/Run | When to Run |
|-----------|-----------|----------|-------------|
| Unit Tests (all) | 0 | $0.00 | Every commit |
| Eval Tests (all) | 100-200 | $0.10-0.50 | Weekly / Pre-release |
| Eval Tests (single file) | 20-40 | $0.02-0.10 | As needed |

**Monthly Estimate**: $5-20 (assuming weekly full runs + daily spot checks)

### Evaluation Test Categories

#### 1. **Faithfulness Tests** (tests/eval/test_cv_faithfulness.py) ⭐ **MOST CRITICAL**

Prevents CV fabrication - all failures are **CRITICAL BUGS**:
- `test_no_hallucinated_companies` - Companies must exist in master CV
- `test_no_hallucinated_institutions` - Schools must exist in master CV
- `test_faithfulness_with_custom_guard` - Custom hallucination detection
- `test_faithfulness_using_deepeval_metric` - LLM-based faithfulness check
- `test_institutions_with_custom_guard` - Institution validation
- `test_schema_compliance` - Output matches Pydantic schema

#### 2. **Relevancy Tests** (tests/eval/test_cv_relevancy.py)

Ensures CV addresses job requirements:
- `test_summary_relevancy_high` - Summary addresses job (threshold: 0.7)
- `test_experience_relevancy` - Experience emphasizes relevant skills (threshold: 0.6)
- `test_skills_relevancy` - Skills match job requirements (threshold: 0.7)
- `test_full_cv_addresses_job` - End-to-end relevancy check

#### 3. **Contextual Relevancy Tests** (tests/eval/test_cv_contextual.py)

Verifies correct content selection from master CV:
- `test_experience_selection_relevancy` - Right experiences prioritized
- `test_project_selection_relevancy` - Relevant projects highlighted
- `test_skills_prioritization_relevancy` - Skills reordered by relevance

#### 4. **Bias & Toxicity Tests** (tests/eval/test_cv_bias_toxicity.py)

Ensures professional tone:
- `test_summary_no_bias` - Unbiased professional summary
- `test_experience_descriptions_no_bias` - Objective experience descriptions
- `test_summary_no_toxicity` - Professional language (threshold: 0.8)
- `test_experience_no_toxicity` - No inappropriate language
- `test_full_cv_no_toxicity` - Overall professional tone
- `test_achievements_professional_tone` - Professional achievement statements

### Custom DeepEval Metrics

#### CVHallucinationGuard
Strict validation that all entities (companies, institutions, skills) exist in master CV:
```python
from tests.eval.metrics.cv_hallucination_guard import CVHallucinationGuard

guard = CVHallucinationGuard(
    threshold=1.0,
    check_type="companies",  # or "institutions", "skills"
    master_data=master_cv
)
```

#### CVSchemaComplianceGuard
Ensures output matches Pydantic CV model:
```python
from tests.eval.metrics.cv_schema_compliance import CVSchemaComplianceGuard

guard = CVSchemaComplianceGuard(threshold=1.0)
```

### Evaluation Scenarios

Predefined test scenarios in `tests/eval/fixtures/eval_scenarios.json`:
- **Happy Path**: Well-matched jobs (Python engineer, Full-stack developer)
- **Edge Cases**: Minimal job posting, unrelated job, minimal CV
- **Stress Tests**: Many requirements, keyword stuffing
- **Faithfulness Tests**: Jobs that might tempt hallucination

### CI/CD Integration

```yaml
# .github/workflows/test.yml

# Unit tests - run on every commit
- name: Run unit tests
  run: pytest tests/unit/ -v

# Eval tests - run on release branches only
- name: Run evaluation tests
  if: startsWith(github.ref, 'refs/heads/release/')
  env:
    XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
    RUN_EVAL_TESTS: "true"
  run: pytest tests/eval/ -v
```

## Test Categories

### Unit Tests

**`test_cv_prompts.py`**
- `TestPromptLoader`: Tests for loading prompts from files
  - Directory creation and initialization
  - Loading and caching prompts
  - Template variable substitution
  - Reloading and hot-reload functionality

- `TestCVPromptManager`: Tests for high-level prompt management
  - Getting prompts for each CV section
  - Template rendering with job data
  - Integration with PromptLoader

**`test_cv_composer.py`**
- `TestJobSummarization`: Tests for job description analysis
  - Basic summarization
  - Pydantic validation
  - Edge cases (empty descriptions)

- `TestSectionComposers`: Tests for individual CV section generation
  - Professional summary composition
  - Experience tailoring
  - Education optimization
  - Skills prioritization
  - Projects highlighting
  - Certifications filtering

- `TestValidation`: Tests for output validation
  - Schema validation with Pydantic
  - Hallucination detection (fake companies/institutions)
  - Data integrity checks

### Integration Tests

- `TestFullCVComposition`: End-to-end CV composition tests
  - Complete workflow from job posting to tailored CV
  - Preservation of contact information
  - Validation of all section generation

- `TestEdgeCases`: Edge case and error handling
  - Empty CVs
  - Minimal job postings
  - Missing data handling

## Test Fixtures

### Master CV (`fixtures/master_cv.json`)
Sample CV with:
- Contact information
- Professional summary
- 3 work experiences (Tech Corp, Startup Inc, Enterprise Solutions)
- Education (Stanford University)
- Skills across multiple categories
- 2 projects
- 3 certifications
- Language proficiencies

### Job Posting (`fixtures/job_posting.json`)
Sample job for "Senior Python Backend Engineer" requiring:
- Python, Django, AWS expertise
- 5+ years experience
- Microservices architecture knowledge
- Docker/Kubernetes skills

## Mocking Strategy

Tests use `MockLLMClient` to simulate LLM API responses without making actual API calls:

```python
from tests.test_cv_composer import MockLLMClient

mock_client = MockLLMClient()
mock_client.set_response("job description", {
    "technical_skills": ["Python", "Django"],
    # ... mock response data
})
```

This ensures:
- ✅ Tests are fast and deterministic
- ✅ No API costs during testing
- ✅ Consistent results across test runs
- ✅ Ability to test edge cases and error conditions

## Writing New Tests

### Adding a Unit Test

```python
def test_new_feature(cv_composer, mock_llm_client, master_cv):
    """Test description"""
    # Arrange
    mock_llm_client.set_response("keyword", {"expected": "response"})

    # Act
    result = cv_composer.some_method(master_cv)

    # Assert
    assert result["expected"] == "value"
```

### Adding Test Fixtures

Add new fixtures to `conftest.py`:

```python
@pytest.fixture
def custom_fixture():
    """Description"""
    return {"key": "value"}
```

### Adding Test Data

Add JSON files to `fixtures/` directory and load them in tests:

```python
@pytest.fixture
def custom_data(fixtures_dir):
    with open(fixtures_dir / "custom.json") as f:
        return json.load(f)
```

## Continuous Integration

Tests can be integrated into CI/CD pipelines:

### GitHub Actions Example

```yaml
- name: Run tests
  run: |
    pip install -r requirements-test.txt
    pytest --cov=src --cov-report=xml

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

## Test Coverage Goals

Target coverage levels:
- **Overall**: >80%
- **Critical paths** (CV composition, validation): >95%
- **Utility functions**: >70%

## Troubleshooting

### Import Errors

Ensure the project root is in PYTHONPATH:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

Or use pytest's automatic path detection:

```bash
python -m pytest
```

### Fixture Not Found

Make sure `conftest.py` exists in the tests directory and fixtures are properly defined.

### Mock Responses Not Working

Verify the keyword used in `set_response()` matches text in the actual prompt:

```python
# Prompt contains "job description"
mock_client.set_response("job description", {...})
```

## Best Practices

1. **Isolate tests**: Each test should be independent
2. **Use fixtures**: Reuse common test data via fixtures
3. **Mock external calls**: Never make real API calls in tests
4. **Test edge cases**: Empty inputs, invalid data, error conditions
5. **Clear assertions**: Test one thing per test method
6. **Descriptive names**: Test names should describe what they test
7. **Comments**: Explain non-obvious test logic

## Future Enhancements

- [ ] Performance benchmarks
- [ ] Load testing for concurrent operations
- [ ] Property-based testing with Hypothesis
- [ ] Mutation testing for test quality
- [ ] Visual regression testing for PDF output
