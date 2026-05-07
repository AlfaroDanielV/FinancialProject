"""Extraction services for parsed financial events.

Two specialized extractors live here:

    email_extractor.py  — bank notification email body → ExtractedEmailTransaction.

The Phase 5b chat extractor (`api.services.llm_extractor`) is intentionally
NOT shared. Different prompts, different schemas, different metrics tables.
See docs/phase-6b-decisions.md (entry 2026-05-06 "Extractor de emails
separado del de chat").
"""

from .email_extractor import (
    ExtractedEmailTransaction,
    EmailExtractionError,
    extract_from_email_body,
    EMAIL_SYSTEM_PROMPT,
    EMAIL_TOOL_DEFINITION,
    TRANSACTION_TYPES,
    EXPENSE_TYPES,
    INCOME_TYPES,
)

__all__ = [
    "ExtractedEmailTransaction",
    "EmailExtractionError",
    "extract_from_email_body",
    "EMAIL_SYSTEM_PROMPT",
    "EMAIL_TOOL_DEFINITION",
    "TRANSACTION_TYPES",
    "EXPENSE_TYPES",
    "INCOME_TYPES",
]
