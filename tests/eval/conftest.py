"""Eval-specific fixtures and configuration"""

import pytest
import json
import os
from pathlib import Path


# Automatically mark all tests in eval/ directory
def pytest_collection_modifyitems(items):
    """Automatically add 'eval' and 'llm' markers to all eval tests"""
    for item in items:
        if "eval" in str(item.fspath):
            item.add_marker(pytest.mark.eval)
            item.add_marker(pytest.mark.llm)
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session")
def deepeval_model():
    """
    DeepEval evaluator model (for running metrics)
    Uses environment variable or defaults to grok-4.1-fast
    """
    return os.getenv("DEEPEVAL_EVALUATOR_MODEL", "grok-4.1-fast")


@pytest.fixture
def eval_llm_client():
    """Real LLM client for evaluation tests"""
    from tests.helpers.llm_clients import create_real_llm_client
    return create_real_llm_client()


@pytest.fixture
def cv_composer_eval(eval_llm_client):
    """CV Composer with real LLM for eval tests"""
    from src.services.cv_composer import CVComposer
    return CVComposer(llm_client=eval_llm_client)


@pytest.fixture
def eval_scenarios():
    """Load predefined eval test scenarios"""
    scenarios_file = Path(__file__).parent / "fixtures" / "eval_scenarios.json"
    if scenarios_file.exists():
        with open(scenarios_file) as f:
            return json.load(f)
    return {}


@pytest.fixture
def eval_thresholds():
    """Get evaluation thresholds from environment or use defaults"""
    return {
        "answer_relevancy": float(os.getenv("EVAL_ANSWER_RELEVANCY_THRESHOLD", "0.7")),
        "faithfulness": float(os.getenv("EVAL_FAITHFULNESS_THRESHOLD", "0.9")),
        "contextual_relevancy": float(os.getenv("EVAL_CONTEXTUAL_RELEVANCY_THRESHOLD", "0.7")),
        "bias": float(os.getenv("EVAL_BIAS_THRESHOLD", "0.9")),
        "toxicity": float(os.getenv("EVAL_TOXICITY_THRESHOLD", "0.8")),
    }


@pytest.fixture(scope="session")
def skip_if_no_api_key():
    """Skip eval tests if required API keys are not set"""
    provider = os.getenv("LLM_PROVIDER", "grok").lower()
    api_key_map = {
        "grok": "XAI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }

    required_key = api_key_map.get(provider)
    if not required_key or not os.getenv(required_key):
        pytest.skip(f"Skipping eval test: {required_key} not set")
