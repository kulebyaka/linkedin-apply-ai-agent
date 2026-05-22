"""DeepSeek provider client — OpenAI-compatible API, json_object mode only."""

from __future__ import annotations

from typing import ClassVar

from ._openai_compatible import OpenAICompatibleClient


class DeepSeekClient(OpenAICompatibleClient):
    """DeepSeek client. No strict-schema support — falls back to json_object
    plus post-hoc validation via basic_validate_json_schema."""

    base_url: ClassVar[str | None] = "https://api.deepseek.com/v1"
    supports_strict_schema: ClassVar[bool] = False
    provider_label: ClassVar[str] = "DeepSeek"
