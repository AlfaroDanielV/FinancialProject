"""Statement extraction asks for output-token headroom (the parse_statement_empty fix).

A bank statement normalizes into a large nested tool-use JSON
(accounts[] -> currency_legs[] -> flows[] + closing_candidates[]). The default
512-token cap truncated that JSON mid-`accounts`, so it validated into an empty
extraction and surfaced as "No pude leer el estado" / 0 productos. The statement
path now asks for 8192 tokens; the flat debt/card/transaction paths keep 512.

DB-free: a stub session swallows the `llm_extractions` audit write so the test
runs anywhere (no Postgres).
"""
from __future__ import annotations

from api.services.llm_extractor.client import FixtureLLMClient, RecordedLLMResponse
from api.services.llm_extractor.document import extract_debt_terms, extract_statement


class _StubDB:
    """Swallows the _log() audit write — we only assert the LLM call's cap."""

    def add(self, _row) -> None:  # noqa: D401
        pass

    async def commit(self) -> None:
        pass


class _StubUser:
    id = "00000000-0000-0000-0000-000000000000"


_VALID_STATEMENT = RecordedLLMResponse(
    tool_input={
        "bank": "BAC",
        "period_end": "2026-05-19",
        "confidence": 0.95,
        "accounts": [
            {
                "account_type": "savings",
                "currency_legs": [
                    {
                        "currency": "CRC",
                        "closing_candidates": [
                            {"amount": 1000.0, "role": "closing"}
                        ],
                    }
                ],
            }
        ],
    }
)


async def test_statement_extraction_requests_8192_headroom():
    # Non-empty, high-confidence response → only the first pass runs.
    client = FixtureLLMClient(default=_VALID_STATEMENT)
    await extract_statement(
        user=_StubUser(),
        pdf_bytes=b"%PDF-1.4 stub",
        client=client,
        haiku_model="m",
        sonnet_model="m",
        db=_StubDB(),
    )
    assert client.max_tokens_calls == [8192]


async def test_debt_extraction_keeps_default_512_cap():
    # The flat debt schema is small — it must NOT inherit the statement headroom.
    client = FixtureLLMClient(
        default=RecordedLLMResponse(tool_input={"confidence": 0.9})
    )
    await extract_debt_terms(
        user=_StubUser(),
        pdf_bytes=b"%PDF-1.4 stub",
        client=client,
        haiku_model="m",
        sonnet_model="m",
        db=_StubDB(),
    )
    assert client.max_tokens_calls == [512]


async def test_empty_statement_retries_then_both_passes_use_headroom():
    # An empty `accounts` forces the second (retry) pass — both must use 8192.
    client = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={"bank": "BAC", "confidence": 0.95, "accounts": []}
        )
    )
    result = await extract_statement(
        user=_StubUser(),
        pdf_bytes=b"%PDF-1.4 stub",
        client=client,
        haiku_model="m",
        sonnet_model="m",
        db=_StubDB(),
    )
    assert result.accounts == []
    assert client.max_tokens_calls == [8192, 8192]
