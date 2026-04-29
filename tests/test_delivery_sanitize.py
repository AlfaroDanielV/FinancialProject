"""Tests for app.queries.delivery.sanitize_telegram_html.

Defense-in-depth on top of the bloque 6 system prompt — the sanitizer
runs even when the LLM stays within the prompt rules, in case a tool
result smuggles in raw markup or the model drifts.
"""
from __future__ import annotations

import pytest

from app.queries.delivery import (
    sanitize_telegram_html,
    strip_html_to_plain,
)


# ── pass-through ─────────────────────────────────────────────────────


def test_clean_input_unchanged():
    s = "<b>Saldos</b>\n- BAC: ₡5.000\n- Visa: -₡1.200"
    assert sanitize_telegram_html(s) == s


def test_empty_string():
    assert sanitize_telegram_html("") == ""


def test_plain_text_unchanged():
    assert sanitize_telegram_html("hola, qué tal") == "hola, qué tal"


# ── tag stripping (content kept) ─────────────────────────────────────


def test_strips_ul_li_keeps_content():
    out = sanitize_telegram_html("<ul><li>uno</li><li>dos</li></ul>")
    assert out == "unodos"


def test_strips_div_p_h1():
    out = sanitize_telegram_html("<div><p><h1>titulo</h1>texto</p></div>")
    assert out == "titulotexto"


def test_strips_br():
    # <br> isn't allowed; LLMs sometimes emit it. Strip cleanly.
    out = sanitize_telegram_html("línea 1<br>línea 2")
    assert out == "línea 1línea 2"


def test_defends_against_script_tag():
    out = sanitize_telegram_html("<script>alert(1)</script>")
    # Script content is text between two stripped tags. We DON'T parse
    # JS — we just strip the tags and let the literal text show.
    assert out == "alert(1)"


def test_strips_unknown_self_closing_shape():
    out = sanitize_telegram_html("<img src='x'/>texto")
    assert out == "texto"


# ── auto-close on unbalanced opens ───────────────────────────────────


def test_auto_closes_unclosed_b():
    out = sanitize_telegram_html("<b>negrita sin cerrar")
    assert out == "<b>negrita sin cerrar</b>"


def test_auto_closes_nested_unclosed():
    out = sanitize_telegram_html("<b>uno <i>dos")
    assert out == "<b>uno <i>dos</i></b>"


def test_drops_orphan_close():
    out = sanitize_telegram_html("texto sin abrir</b>")
    assert out == "texto sin abrir"


def test_handles_crossed_tags():
    # `<b>uno<i>dos</b>tres</i>` — Telegram rejects crossed tags. We
    # close the inner `<i>` before closing `<b>`.
    out = sanitize_telegram_html("<b>uno<i>dos</b>tres</i>")
    # Expected: <b>uno<i>dos</i></b>tres   (the orphan </i> is dropped)
    assert out == "<b>uno<i>dos</i></b>tres"


# ── escape stray &, <, > ─────────────────────────────────────────────


def test_escapes_literal_ampersand():
    out = sanitize_telegram_html("Tom & Jerry")
    assert out == "Tom &amp; Jerry"


def test_preserves_existing_entities():
    out = sanitize_telegram_html("Tom &amp; Jerry &lt;3 &#42;")
    assert out == "Tom &amp; Jerry &lt;3 &#42;"


def test_escapes_stray_lt_gt_in_text():
    out = sanitize_telegram_html("if 5 < 10 and 11 > 9")
    assert out == "if 5 &lt; 10 and 11 &gt; 9"


def test_does_not_escape_inside_allowed_tags():
    out = sanitize_telegram_html("<b>5 < 10</b>")
    # `<` inside `<b>` text content gets escaped, but the tag itself
    # stays literal.
    assert out == "<b>5 &lt; 10</b>"


# ── <a href="..."> ───────────────────────────────────────────────────


def test_keeps_a_with_https_href():
    out = sanitize_telegram_html('<a href="https://example.com">link</a>')
    assert out == '<a href="https://example.com">link</a>'


def test_keeps_a_with_http_href():
    out = sanitize_telegram_html('<a href="http://example.com">x</a>')
    assert out == '<a href="http://example.com">x</a>'


def test_keeps_a_with_tg_link():
    out = sanitize_telegram_html('<a href="tg://user?id=42">user</a>')
    assert out == '<a href="tg://user?id=42">user</a>'


def test_drops_a_with_javascript_href():
    out = sanitize_telegram_html('<a href="javascript:alert(1)">click</a>')
    # Tag dropped, link text kept.
    assert out == "click"


def test_drops_a_without_href():
    out = sanitize_telegram_html("<a>texto</a>")
    assert out == "texto"


# ── <span class="tg-spoiler"> ────────────────────────────────────────


def test_keeps_spoiler_span():
    out = sanitize_telegram_html('<span class="tg-spoiler">oculto</span>')
    assert out == '<span class="tg-spoiler">oculto</span>'


def test_drops_span_without_spoiler_class():
    out = sanitize_telegram_html('<span class="other">texto</span>')
    assert out == "texto"


def test_drops_span_with_no_class():
    out = sanitize_telegram_html("<span>texto</span>")
    assert out == "texto"


# ── <code> with optional language class ──────────────────────────────


def test_keeps_plain_code():
    out = sanitize_telegram_html("<code>x = 1</code>")
    assert out == "<code>x = 1</code>"


def test_keeps_code_with_language_class():
    out = sanitize_telegram_html('<code class="language-python">x = 1</code>')
    assert out == '<code class="language-python">x = 1</code>'


# ── strip_html_to_plain (fallback) ───────────────────────────────────


def test_strip_html_to_plain_removes_all_tags():
    out = strip_html_to_plain("<b>Saldo</b>: ₡5.000 &amp; <i>nota</i>")
    assert out == "Saldo: ₡5.000 & nota"


def test_strip_html_to_plain_empty():
    assert strip_html_to_plain("") == ""


# ── case-insensitive tag matching ────────────────────────────────────


def test_case_insensitive_b_tag():
    out = sanitize_telegram_html("<B>negrita</B>")
    assert out == "<b>negrita</b>"


def test_case_insensitive_drop_unknown():
    out = sanitize_telegram_html("<DIV>x</DIV>")
    assert out == "x"
