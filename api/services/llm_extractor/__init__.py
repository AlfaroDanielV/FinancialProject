"""LLM extractor for the Telegram bot.

Turns a user's natural-language message into a validated ExtractionResult.
Downstream consumers (the dispatcher) operate on the structured object only
— they never see the raw text.

Rule from the Phase 5b spec: the LLM extracts, the dispatcher decides. No
branch of this module makes routing, policy, or write decisions.
"""
from .schema import (
    EXPECTED_QUERY_WINDOW_PREFIX,
    VALID_QUERY_WINDOWS,
    ExtractionResult,
    Intent,
)
from .client import (
    AnthropicLLMClient,
    FixtureLLMClient,
    LLMClient,
    LLMClientError,
    RecordedLLMResponse,
)
from .prompt import SYSTEM_PROMPT, TOOL_DEFINITION
from .runner import extract_finance_intent

__all__ = [
    "ExtractionResult",
    "Intent",
    "VALID_QUERY_WINDOWS",
    "EXPECTED_QUERY_WINDOW_PREFIX",
    "LLMClient",
    "AnthropicLLMClient",
    "FixtureLLMClient",
    "LLMClientError",
    "RecordedLLMResponse",
    "SYSTEM_PROMPT",
    "TOOL_DEFINITION",
    "extract_finance_intent",
]
