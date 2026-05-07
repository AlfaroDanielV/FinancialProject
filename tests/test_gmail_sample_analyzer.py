"""Tests for the sample analyzer.

We don't hit Anthropic. The two-step shape (vision text-extract, then
text classify) means we exercise:
    1. _coerce_analysis on a raw payload — defensive type handling.
    2. analyze_text_sample / analyze_image_sample with a fake client —
       to verify the pipeline glue and bytes → text → analysis ordering.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from api.services.gmail.sample_analyzer import (
    CONFIDENCE_THRESHOLD,
    SampleAnalysis,
    SampleAnalyzerClient,
    _coerce_analysis,
    analyze_image_sample,
    analyze_text_sample,
)


# ── _coerce_analysis ─────────────────────────────────────────────────────────


def test_coerce_happy_path():
    payload = {
        "sender_email": "notificaciones@bac.cr",
        "bank_name": "BAC Credomatic",
        "format_signature": {"subject_pattern": "Compra"},
        "confidence": 0.82,
    }
    a = _coerce_analysis("RAW TEXT", payload)
    assert a.sender_email == "notificaciones@bac.cr"
    assert a.bank_name == "BAC Credomatic"
    assert a.format_signature == {"subject_pattern": "Compra"}
    assert a.confidence == 0.82
    assert a.raw_text == "RAW TEXT"


def test_coerce_clamps_confidence():
    a = _coerce_analysis("x", {"confidence": 1.5, "format_signature": {}})
    assert a.confidence == 1.0
    b = _coerce_analysis("x", {"confidence": -0.4, "format_signature": {}})
    assert b.confidence == 0.0


def test_coerce_handles_null_fields():
    a = _coerce_analysis(
        "x",
        {
            "sender_email": None,
            "bank_name": None,
            "format_signature": {},
            "confidence": 0.3,
        },
    )
    assert a.sender_email is None
    assert a.bank_name is None
    assert a.format_signature == {}


def test_coerce_recovers_from_string_signature():
    """Anthropic occasionally serializes a dict tool input as a string;
    accept it gracefully."""
    a = _coerce_analysis(
        "x",
        {"format_signature": '{"key": "value"}', "confidence": 0.5},
    )
    assert a.format_signature == {"key": "value"}


def test_coerce_drops_garbage_signature():
    a = _coerce_analysis(
        "x",
        {"format_signature": "not-json", "confidence": 0.5},
    )
    assert a.format_signature == {}


def test_coerce_strips_whitespace_in_strings():
    a = _coerce_analysis(
        "x",
        {
            "sender_email": "  user@bank.cr  ",
            "bank_name": " BAC ",
            "format_signature": {},
            "confidence": 0.9,
        },
    )
    assert a.sender_email == "user@bank.cr"
    assert a.bank_name == "BAC"


# ── analyze_*_sample with a fake client ──────────────────────────────────────


@dataclass
class _FakeClient:
    text_to_return: SampleAnalysis
    extracted_text: str = "decoded"

    async def extract_text_from_image(self, image_bytes, *, mime_type="image/jpeg"):
        self.image_seen = (image_bytes, mime_type)
        return self.extracted_text

    async def analyze_text(self, raw_text):
        self.text_seen = raw_text
        # Return a copy that surfaces what raw_text the analyzer received.
        return SampleAnalysis(
            raw_text=raw_text,
            sender_email=self.text_to_return.sender_email,
            bank_name=self.text_to_return.bank_name,
            format_signature=self.text_to_return.format_signature,
            confidence=self.text_to_return.confidence,
        )


async def test_analyze_text_sample_passes_through():
    fake = _FakeClient(
        text_to_return=SampleAnalysis(
            raw_text="",
            sender_email="x@y",
            bank_name="BAC",
            format_signature={"k": "v"},
            confidence=0.9,
        )
    )
    result = await analyze_text_sample("hola texto", client=fake)
    assert result.raw_text == "hola texto"
    assert result.bank_name == "BAC"
    assert fake.text_seen == "hola texto"


async def test_analyze_image_sample_runs_vision_then_text():
    fake = _FakeClient(
        text_to_return=SampleAnalysis(
            raw_text="",
            sender_email="x@y",
            bank_name="Promerica",
            format_signature={},
            confidence=0.85,
        ),
        extracted_text="visioned text",
    )
    result = await analyze_image_sample(b"\x00\x01jpegbytes", client=fake)
    assert fake.image_seen[0] == b"\x00\x01jpegbytes"
    assert fake.image_seen[1] == "image/jpeg"
    assert fake.text_seen == "visioned text"
    assert result.raw_text == "visioned text"
    assert result.bank_name == "Promerica"


# ── confidence threshold sanity ──────────────────────────────────────────────


def test_threshold_constants_are_sane():
    """Guard: changing these silently would break the bot's UX. If you
    are intentionally moving the threshold, update this test too."""
    assert 0.5 < CONFIDENCE_THRESHOLD <= 0.9
