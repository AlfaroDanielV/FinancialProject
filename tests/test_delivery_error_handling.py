"""Tests for app.queries.delivery.handle_query_error.

One test per row of the catalog in docs/phase-6a-decisions.md
(2026-04-29 entry). Covers:
  - exception → user-facing Spanish message
  - logging emits at the documented level
  - no traceback / internal ID leaks into the message string
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import Mock

import pytest

from app.queries.delivery import (
    BudgetExceeded,
    ChunkOverflow,
    HTMLSanitizationFailed,
    ToolExecutionError,
    handle_query_error,
)
from app.queries.llm_client import (
    ERR_AUTH_ERROR,
    ERR_CLIENT_ERROR,
    ERR_RATE_LIMIT,
    ERR_SERVER_ERROR,
    ERR_TIMEOUT,
    ERR_UNKNOWN,
    IterationCapExceeded,
    QueryLLMClientError,
)


# Catalog assertions in one place so renames are loud.
_MSG_ITERATION = (
    "No pude completar tu consulta en el tiempo esperado. Probá reformulando."
)
_MSG_TRANSIENT = "Hubo un problema temporal. Probá de nuevo en un minuto."
_MSG_ADMIN = "El servicio está temporalmente fuera de línea. Avisale al admin."
_MSG_TOOL = "Algo se rompió consultando tus datos. Avisale al admin."
_MSG_BUDGET = "Llegaste al límite diario de consultas. Se renueva mañana."
_MSG_GENERIC = "Algo se rompió consultando tus datos. Avisale al admin."

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
QUERY_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ── catalog rows ─────────────────────────────────────────────────────


def test_iteration_cap_returns_iteration_message(caplog):
    exc = IterationCapExceeded(
        total_iterations=4,
        total_input_tokens=1000,
        total_output_tokens=200,
        tools_used=[],
        duration_ms=15000,
    )
    with caplog.at_level(logging.WARNING, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID, query_id=QUERY_ID)
    assert msg == _MSG_ITERATION
    assert any("iteration_cap_exceeded" in r.message for r in caplog.records)


def test_llm_timeout_returns_iteration_message(caplog):
    exc = QueryLLMClientError("query_timeout: …", category=ERR_TIMEOUT)
    with caplog.at_level(logging.WARNING, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    # Timeout reuses the iteration-cap message — both surface as
    # "no pude completar tu consulta".
    assert msg == _MSG_ITERATION


def test_llm_rate_limit_returns_transient(caplog):
    exc = QueryLLMClientError("query_rate_limit: …", category=ERR_RATE_LIMIT)
    with caplog.at_level(logging.WARNING, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_TRANSIENT


def test_llm_server_error_returns_transient(caplog):
    exc = QueryLLMClientError("query_server_error: …", category=ERR_SERVER_ERROR)
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_TRANSIENT
    assert any("llm_server_error" in r.message for r in caplog.records)


def test_llm_auth_error_returns_admin_message(caplog):
    exc = QueryLLMClientError("query_auth_error: …", category=ERR_AUTH_ERROR)
    with caplog.at_level(logging.CRITICAL, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_ADMIN
    assert any("llm_auth_error" in r.message for r in caplog.records)


def test_llm_client_error_returns_generic(caplog):
    exc = QueryLLMClientError("query_client_error: …", category=ERR_CLIENT_ERROR)
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_GENERIC


def test_llm_unknown_category_returns_generic(caplog):
    exc = QueryLLMClientError("weird thing", category=ERR_UNKNOWN)
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_GENERIC


def test_tool_execution_error_returns_tool_failure(caplog):
    exc = ToolExecutionError("DB connection lost")
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_TOOL


def test_budget_exceeded_returns_budget_message(caplog):
    exc = BudgetExceeded("99,500 / 100,000 tokens used today")
    with caplog.at_level(logging.INFO, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_BUDGET


def test_html_sanitization_failed_returns_generic(caplog):
    exc = HTMLSanitizationFailed("regex blew up")
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_GENERIC


def test_chunk_overflow_returns_generic_with_critical_log(caplog):
    exc = ChunkOverflow("4200 > 4096")
    with caplog.at_level(logging.CRITICAL, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_GENERIC
    assert any("chunk_overflow" in r.message for r in caplog.records)


def test_unknown_exception_returns_generic(caplog):
    exc = ValueError("totally unexpected")
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        msg = handle_query_error(exc, user_id=USER_ID)
    assert msg == _MSG_GENERIC
    assert any("unhandled_query_exception" in r.message for r in caplog.records)


# ── never leak internals ─────────────────────────────────────────────


def test_message_does_not_leak_exception_text():
    exc = QueryLLMClientError(
        "query_server_error: connection to api.anthropic.com failed: socket error",
        category=ERR_SERVER_ERROR,
    )
    msg = handle_query_error(exc, user_id=USER_ID)
    assert "anthropic" not in msg.lower()
    assert "socket" not in msg.lower()


def test_message_does_not_leak_user_or_query_ids():
    exc = ToolExecutionError("anything")
    msg = handle_query_error(exc, user_id=USER_ID, query_id=QUERY_ID)
    assert str(USER_ID) not in msg
    assert str(QUERY_ID) not in msg


def test_message_does_not_leak_internal_tool_names():
    exc = ToolExecutionError("aggregate_transactions failed")
    msg = handle_query_error(exc, user_id=USER_ID)
    assert "aggregate_transactions" not in msg


# ── log includes user_id and query_id ────────────────────────────────


def test_log_includes_context(caplog):
    exc = ToolExecutionError("x")
    with caplog.at_level(logging.ERROR, logger="app.queries.delivery"):
        handle_query_error(exc, user_id=USER_ID, query_id=QUERY_ID)
    # The log line carries ctx with both UUIDs so we can grep for them
    # in production without exposing them to the user.
    combined = " ".join(r.message for r in caplog.records)
    assert str(USER_ID) in combined
    assert str(QUERY_ID) in combined
