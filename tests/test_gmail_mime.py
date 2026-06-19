"""Pure-function tests for the MIME helpers in scanner.py.

These cover the bits that don't need DB or Gmail mocking: base64url
decoding, body walking, and Gmail query construction.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from api.services.gmail.scanner import (
    _b64url_decode,
    _build_gmail_query,
    _extract_body,
    _header_value,
    _strip_html,
)


# ── _b64url_decode ───────────────────────────────────────────────────────────


def test_b64url_decode_no_padding():
    """Gmail strips '=' padding. Decoder must re-pad before decoding."""
    raw = "hola mundo".encode("utf-8")
    enc = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert _b64url_decode(enc) == raw


def test_b64url_decode_with_padding():
    raw = b"abc"
    enc = base64.urlsafe_b64encode(raw).decode()
    assert _b64url_decode(enc) == raw


def test_b64url_decode_garbage_returns_empty():
    """Defensive: malformed data returns b'' instead of raising."""
    assert _b64url_decode("!!!!!!") == b""


# ── _strip_html ──────────────────────────────────────────────────────────────


def test_strip_html_basic():
    html = "<html><body><p>Compra: <b>5000</b> en Walmart</p></body></html>"
    out = _strip_html(html)
    assert "5000" in out
    assert "<" not in out


def test_strip_html_collapses_whitespace():
    html = "<p>line1</p>\n<p>line2</p>"
    out = _strip_html(html)
    assert "line1 line2" in out


def test_strip_html_drops_style_and_script_contents():
    """Bank emails ship big inline <style>/<script> blocks. Their CSS/JS text
    is not inside `< >`, so it must be removed with its contents — otherwise it
    floods the body and buries the transaction past the 4000-char trim."""
    bulk_css = ".x{font-family:Arial;color:#fff;background:#000;} " * 50
    html = (
        "<html><head><style>" + bulk_css + "</style></head><body>"
        "<!--[if mso]><table><tr><td><![endif]-->"
        "<table><tr><td>Comercio</td><td>AUTO MERCADO GUAYABOS</td></tr>"
        "<tr><td>Monto</td><td>CRC:&nbsp;11,066.00</td></tr></table>"
        "<script>track('open');</script>"
        "</body></html>"
    )
    out = _strip_html(html)
    assert "AUTO MERCADO GUAYABOS" in out
    assert "11,066.00" in out
    assert "&nbsp;" not in out  # entity unescaped, not left literal
    assert "font-family" not in out  # CSS gone
    assert "track(" not in out  # JS gone
    assert "[if mso]" not in out  # conditional comment gone


# ── _extract_body ────────────────────────────────────────────────────────────


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode().rstrip("=")


def test_extract_body_prefers_text_plain():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64url("PLAIN VERSION")},
            },
            {
                "mimeType": "text/html",
                "body": {"data": _b64url("<p>HTML VERSION</p>")},
            },
        ],
    }
    assert _extract_body(payload) == "PLAIN VERSION"


def test_extract_body_falls_back_to_html():
    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64url("<p>only html here</p>")},
    }
    out = _extract_body(payload)
    assert "only html here" in out
    assert "<" not in out


def test_extract_body_falls_back_to_html_when_plain_is_stub():
    """Daniel's Promerica bug: the text/plain part is a short stub and the
    real transaction table lives only in text/html. The extractor must read
    the HTML, not the stub — otherwise the email is skipped as low-confidence."""
    stub_plain = "Para ver este correo activá el formato HTML."  # < 120 chars
    rich_html = (
        "<html><body><table>"
        "<tr><td>Comercio</td><td>AUTO MERCADO GUAYABOS SAN JOSE CR</td></tr>"
        "<tr><td>Fecha/hora</td><td>02 jun 2026 / 16:29</td></tr>"
        "<tr><td>Numero de tarjeta</td><td>****-****-****-9097</td></tr>"
        "<tr><td>Monto</td><td>CRC: 11,066.00</td></tr>"
        "</table></body></html>"
    )
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64url(stub_plain)}},
            {"mimeType": "text/html", "body": {"data": _b64url(rich_html)}},
        ],
    }
    out = _extract_body(payload)
    assert "AUTO MERCADO GUAYABOS SAN JOSE CR" in out
    assert "11,066.00" in out
    assert "<" not in out  # HTML tags stripped


def test_extract_body_keeps_substantive_plain_over_html():
    """A real text/plain body (>= _MIN_BODY_CHARS) is kept even when the HTML
    alternative is longer — we don't want to switch to noisier HTML for emails
    that already ship a good plain part (the 59 that work today)."""
    good_plain = (
        "Compra con tarjeta Mastercard en MXM GUAYABOS OCN00PV por CRC "
        "12,560.00 el 11 may 2026. Numero de autorizacion 123456. "
        "Gracias por usar tu tarjeta Promerica."
    )  # > 120 chars
    longer_html = "<div>" + good_plain + " " + ("promo legal aviso " * 40) + "</div>"
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64url(good_plain)}},
            {"mimeType": "text/html", "body": {"data": _b64url(longer_html)}},
        ],
    }
    assert _extract_body(payload) == good_plain.strip()


def test_extract_body_walks_nested_parts():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("nested plain")},
                    },
                ],
            },
            {"mimeType": "image/png", "body": {"attachmentId": "x"}},
        ],
    }
    assert _extract_body(payload) == "nested plain"


def test_extract_body_handles_empty():
    assert _extract_body({}) == ""
    assert _extract_body({"mimeType": "text/plain"}) == ""


# ── _header_value ────────────────────────────────────────────────────────────


def test_header_value_case_insensitive():
    payload = {
        "headers": [
            {"name": "From", "value": "x@y"},
            {"name": "Subject", "value": "compra"},
        ]
    }
    assert _header_value(payload, "FROM") == "x@y"
    assert _header_value(payload, "subject") == "compra"
    assert _header_value(payload, "missing") is None


# ── _build_gmail_query ───────────────────────────────────────────────────────


def test_build_gmail_query_single_sender():
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    q = _build_gmail_query(senders=["a@b.com"], since=since, until=None)
    assert "from:a@b.com" in q
    assert "after:" in q


def test_build_gmail_query_multiple_senders_or_clause():
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    q = _build_gmail_query(
        senders=["a@b.com", "c@d.com"], since=since, until=None
    )
    assert "from:a@b.com OR from:c@d.com" in q


def test_build_gmail_query_includes_until_when_set():
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = datetime(2026, 1, 31, tzinfo=timezone.utc)
    q = _build_gmail_query(senders=["a@b.com"], since=since, until=until)
    assert "before:" in q


def test_build_gmail_query_empty_senders_returns_empty():
    """Defensive — handlers should never call with empty senders, but
    if they do we want an empty query string, not a malformed one."""
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    q = _build_gmail_query(senders=[], since=since, until=None)
    assert q == ""
