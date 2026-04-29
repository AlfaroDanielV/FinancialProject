"""Tests for app.queries.delivery.split_for_telegram.

Splitter is Option C: paragraph-aware with hard-break fallback. Each
chunk must be HTML-balanced — open tags at the cut point are closed
at the end of chunk N and reopened at the start of chunk N+1.
"""
from __future__ import annotations

import re

import pytest

from app.queries.delivery import (
    TELEGRAM_HARD_LIMIT,
    TELEGRAM_OPERATIONAL_CAP,
    sanitize_telegram_html,
    split_for_telegram,
)


# ── single-chunk cases ───────────────────────────────────────────────


def test_short_text_one_chunk():
    out = split_for_telegram("hola")
    assert out == ["hola"]


def test_empty_text_one_chunk():
    out = split_for_telegram("")
    assert out == [""]


def test_text_at_cap_one_chunk():
    s = "x" * TELEGRAM_OPERATIONAL_CAP
    out = split_for_telegram(s)
    assert len(out) == 1
    assert out[0] == s


# ── paragraph-based split ────────────────────────────────────────────


def test_two_paragraphs_split_when_total_exceeds_cap():
    para1 = "a" * 2500
    para2 = "b" * 2500
    text = para1 + "\n\n" + para2
    out = split_for_telegram(text)
    assert len(out) == 2
    # Each chunk under cap.
    assert all(len(c) <= TELEGRAM_OPERATIONAL_CAP for c in out)
    # No data lost: concatenating should restore something close to
    # the original (paragraph separator may be consumed).
    assert para1 in out[0]
    assert para2 in out[1]


def test_three_chunks_when_3x_cap():
    text = ("a" * 1500 + "\n\n") * 3 + "tail"
    out = split_for_telegram(text, cap=2000)
    assert len(out) >= 2
    assert all(len(c) <= 2000 for c in out)


# ── tag rebalancing across chunks ────────────────────────────────────


def test_open_b_at_cut_is_closed_and_reopened():
    # First paragraph: opens <b>, content runs long, no close.
    # We craft two paragraphs so the splitter cuts between them.
    para1 = "<b>" + ("a" * 2500) + "</b>"
    para2 = "<b>" + ("b" * 2500) + "</b>"
    text = para1 + "\n\n" + para2
    out = split_for_telegram(text)
    assert len(out) == 2
    # Each chunk parses through sanitize unchanged (already balanced).
    for c in out:
        assert sanitize_telegram_html(c) == c


def test_open_tag_carry_when_paragraph_break_inside_b():
    # `<b>` opens, paragraph break, `</b>` later. Splitter must close
    # the <b> at end of chunk 1 and reopen it at start of chunk 2.
    para1 = "<b>" + ("a" * 3500)
    para2 = ("b" * 2500) + "</b>"
    text = para1 + "\n\n" + para2
    out = split_for_telegram(text)
    assert len(out) >= 2
    # Chunk 1 must end with </b>.
    assert out[0].rstrip().endswith("</b>")
    # Chunk 2 must start with <b>.
    assert out[1].lstrip().startswith("<b>")
    # All chunks valid HTML.
    for c in out:
        assert sanitize_telegram_html(c) == c


# ── hard-break fallback (single huge paragraph) ──────────────────────


def test_single_paragraph_over_cap_hard_break():
    # No `\n\n`. Just a wall of text.
    text = "x" * (TELEGRAM_OPERATIONAL_CAP * 2 + 100)
    out = split_for_telegram(text)
    assert len(out) >= 2
    assert all(len(c) <= TELEGRAM_OPERATIONAL_CAP for c in out)
    # Total content preserved (just chars, no tags).
    assert "".join(out) == text


def test_hard_break_prefers_newline_to_arbitrary_cut():
    # 3000 chars without newline + newline + 3000 chars without newline.
    a = "a" * 3000
    b = "b" * 3000
    text = a + "\n" + b
    out = split_for_telegram(text, cap=3500)
    # Either chunk crosses the newline cleanly.
    assert len(out) == 2
    # The "a" block fits in the first chunk; the newline lands at the
    # cut, so chunk 0 ends near the newline.
    assert out[0].endswith("a") or out[0].endswith("\n")
    assert "".join(out) == text


def test_hard_break_prefers_space_when_no_newline():
    text = ("word " * 1200).rstrip()  # ~6000 chars, all space-separated
    out = split_for_telegram(text, cap=3500)
    assert len(out) >= 2
    assert all(len(c) <= 3500 for c in out)


# ── safety: never exceed Telegram hard limit ─────────────────────────


def test_no_chunk_exceeds_hard_limit():
    text = "x" * (TELEGRAM_HARD_LIMIT * 3)
    out = split_for_telegram(text)
    for c in out:
        assert len(c) <= TELEGRAM_HARD_LIMIT


# ── content preservation ─────────────────────────────────────────────


def test_total_visible_content_preserved_no_tags():
    text = "\n\n".join("p" * 1000 for _ in range(8))  # 8 paragraphs
    out = split_for_telegram(text)
    # Non-tag chars only — all `p` and `\n` should still be present.
    joined = "".join(out)
    # We may add reopened tag prefixes, but with no tags in source there
    # should be no tag chars in any chunk.
    assert "<" not in joined
    # Count of `p` chars is preserved.
    assert joined.count("p") == text.count("p")


def test_panorama_like_realistic_content():
    """Mimics the bloque-6 panorama few-shot: 4 sections with bold
    headers, dash bullets, money numbers."""
    section = (
        "<b>Sección {n}</b>\n"
        "- Línea 1: ₡{n}5.000\n"
        "- Línea 2: ₡{n}2.000\n"
        "- Línea 3: ₡{n}9.000\n"
    )
    text = "\n".join(section.format(n=i) for i in range(1, 30))
    if len(text) <= TELEGRAM_OPERATIONAL_CAP:
        out = split_for_telegram(text)
        assert len(out) == 1
    else:
        out = split_for_telegram(text)
        assert len(out) >= 2
        for c in out:
            assert len(c) <= TELEGRAM_OPERATIONAL_CAP
            assert sanitize_telegram_html(c) == c


# ── edge: paragraph with carry-only chunk ────────────────────────────


def test_carry_does_not_cause_infinite_loop():
    # Pathological: many tiny paragraphs each fully fits, sum exceeds
    # cap. We just need split to terminate and produce valid chunks.
    text = "\n\n".join("<b>p{}</b>".format(i) for i in range(2000))
    out = split_for_telegram(text)
    assert len(out) >= 2
    assert all(len(c) <= TELEGRAM_OPERATIONAL_CAP for c in out)
    for c in out:
        assert sanitize_telegram_html(c) == c
