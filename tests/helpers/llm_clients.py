"""Helper functions for creating real LLM clients for evaluation tests"""

import os
import logging
from src.llm.provider import LLMClientFactory, LLMProvider

logger = logging.getLogger(__name__)


def create_real_llm_client():
    """
    Create a real LLM client for evaluation tests

    Uses environment variables for configuration:
    - LLM_PROVIDER: Provider to use (default: "grok")
    - XAI_API_KEY: API key for Grok
    - OPENAI_API_KEY: API key for OpenAI (if using OpenAI)
    - DEEPSEEK_API_KEY: API key for DeepSeek (if using DeepSeek)
    - ANTHROPIC_API_KEY: API key for Anthropic (if using Anthropic)
    - LLM_MODEL: Model to use (default: "grok-4.1-fast")
    - DEEPEVAL_EVALUATOR_MODEL: Model for DeepEval evaluations (default: same as LLM_MODEL)

    Returns:
        BaseLLMClient: Configured LLM client

    Raises:
        ValueError: If required API key is not set
        ImportError: If required package is not installed
    """
    # Read configuration from environment
    provider_str = os.getenv("LLM_PROVIDER", "grok").lower()
    model = os.getenv("LLM_MODEL", "grok-4.1-fast")

    # Map provider string to enum
    provider_map = {
        "openai": LLMProvider.OPENAI,
        "grok": LLMProvider.GROK,
        "deepseek": LLMProvider.DEEPSEEK,
        "anthropic": LLMProvider.ANTHROPIC,
    }

    provider = provider_map.get(provider_str)
    if not provider:
        raise ValueError(f"Unknown LLM provider: {provider_str}. Supported: {list(provider_map.keys())}")

    # Get API key based on provider
    api_key_map = {
        LLMProvider.OPENAI: "OPENAI_API_KEY",
        LLMProvider.GROK: "XAI_API_KEY",
        LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
        LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    }

    api_key_env = api_key_map[provider]
    api_key = os.getenv(api_key_env)

    if not api_key:
        raise ValueError(
            f"API key not set for {provider.value}. "
            f"Please set {api_key_env} environment variable."
        )

    # Create client using factory
    try:
        client = LLMClientFactory.create(
            provider=provider,
            api_key=api_key,
            model=model
        )
        logger.info(f"Created {provider.value} client with model {model}")
        return client

    except Exception as e:
        logger.error(f"Failed to create LLM client: {e}")
        raise


def get_deepeval_model():
    """
    Get the model name for DeepEval evaluations

    Returns:
        str: Model name for DeepEval (default: grok-4.1-fast)
    """
    return os.getenv("DEEPEVAL_EVALUATOR_MODEL", "grok-4.1-fast")


def should_run_eval_tests():
    """
    Check if evaluation tests should run

    Reads from RUN_EVAL_TESTS environment variable (default: false)

    Returns:
        bool: True if eval tests should run
    """
    return os.getenv("RUN_EVAL_TESTS", "false").lower() in ("true", "1", "yes")
