"""Phase 6a — bloque 8: delivery polish.

Three responsibilities, all between the LLM dispatcher's return value
and the bytes that go to Telegram:

1. `sanitize_telegram_html` — make sure the LLM's HTML matches the
   subset Telegram accepts (b, i, u, s, a, code, pre, tg-spoiler,
   span class=tg-spoiler). Anything else is stripped (content kept).
   Unbalanced tags are auto-closed. Loose `<`, `>`, `&` are escaped.

2. `split_for_telegram` — chop a long message into ≤4096-char chunks.
   Paragraph-aware with hard-break fallback. Open tags at the cut
   point are closed at the end of chunk N and reopened at the start
   of chunk N+1 so each chunk is independently valid HTML.

3. `handle_query_error` + `BudgetExceeded` — map a known exception
   to a fixed Spanish user-facing message. The catalog is in
   docs/phase-6a-decisions.md (entry 2026-04-29). Tracebacks, IDs,
   and tool names never leave this function.

Design notes (see docs/phase-6a-decisions.md):
- No BeautifulSoup / lxml / bleach. Single-pass regex tokenizer +
  open-tag stack. Input is ~500–3500 chars of LLM output, sub-millisecond.
- The splitter does NOT depend on sanitize. The contract is: caller
  passes already-sanitized HTML. Calling split first then sanitize
  per chunk also works but doubles the walk.
- Block 8 builds the layer; block 9 wires it into bot/handlers.py.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

log = logging.getLogger("app.queries.delivery")

TELEGRAM_HARD_LIMIT = 4096
TELEGRAM_OPERATIONAL_CAP = 3900  # leaves margin for entities Telegram appends

# Allowed tag names in Telegram's HTML subset. `tg-spoiler` is the only
# tag with a hyphen — case-insensitive match handles `<TG-SPOILER>` too.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "a",
        "code",
        "pre",
        "tg-spoiler",
        "span",  # only when class="tg-spoiler"
    }
)

# Tokenizer: matches one tag (open, close, or self-closing-shaped) at a
# time, with the tag name in group 'name' and the rest in 'rest'. Anything
# that isn't a tag is treated as text.
_TAG_RE = re.compile(
    r"""
    <
    (?P<close>/?)
    \s*
    (?P<name>[a-zA-Z][a-zA-Z0-9\-]*)
    (?P<rest>[^>]*)
    >
    """,
    re.VERBOSE,
)

_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_CLASS_RE = re.compile(r"""class\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
# href values we consider safe enough to keep as link targets.
_SAFE_HREF_RE = re.compile(r"^(https?://|tg://|mailto:)", re.IGNORECASE)
# Already-escaped entities we leave alone when rewriting `&` in text.
_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")


# ── exceptions used by the error catalog ─────────────────────────────


class BudgetExceeded(RuntimeError):
    """Raised when a user has exhausted their daily LLM token budget.

    The actual enforcement (where/how the check runs) lives elsewhere
    — sub-block 8.5 was paused pending a design decision (Redis vs.
    DB-backed counter). The exception itself is uniform regardless of
    the source, so the error handler can map it without caring.
    """


class HTMLSanitizationFailed(RuntimeError):
    """Reserved for catastrophic sanitizer failures.

    `sanitize_telegram_html` itself is total — it can't raise on bad
    input — but if a future change introduces something that can,
    this is the type to raise so the handler hits the strip-to-plain
    fallback.
    """


class ChunkOverflow(RuntimeError):
    """A produced chunk exceeded Telegram's hard limit (4096) after split.

    Indicates a bug in the splitter. The handler logs CRITICAL and
    truncates with an ellipsis.
    """


class ToolExecutionError(RuntimeError):
    """A query tool raised in a way the LLM can't recover from.

    Distinct from `LLM tool_result is_error` — those are returned to
    the model so it can adapt. This is for failures the dispatcher
    itself surfaces (DB unreachable, etc.).
    """


# ── HTML sanitization ────────────────────────────────────────────────


def _escape_text_chunk(s: str) -> str:
    """Escape stray `<`, `>`, and unencoded `&` in text content.

    Existing entity references (`&amp;`, `&lt;`, `&#42;`, etc.) are
    preserved verbatim — re-escaping `&amp;` would produce `&amp;amp;`.
    """
    if not s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "&":
            m = _ENTITY_RE.match(s, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
            out.append("&amp;")
            i += 1
            continue
        if ch == "<":
            out.append("&lt;")
            i += 1
            continue
        if ch == ">":
            out.append("&gt;")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize_open_tag(name: str, rest: str) -> Optional[str]:
    """Decide what to emit for an opening tag in the allowed set.

    Returns the tag string to emit (e.g. `<b>` or `<a href="...">`),
    or None if the tag should be dropped (e.g. `<span>` without the
    spoiler class, `<a>` without a safe href).
    """
    name_l = name.lower()
    if name_l == "a":
        m = _HREF_RE.search(rest)
        if not m:
            return None
        href = (m.group(1) or m.group(2) or "").strip()
        if not _SAFE_HREF_RE.match(href):
            return None
        # Re-escape the href as a precaution. Telegram is strict on quotes.
        href_safe = href.replace('"', "&quot;")
        return f'<a href="{href_safe}">'
    if name_l == "span":
        m = _CLASS_RE.search(rest)
        cls = (m.group(1) or m.group(2) or "").strip() if m else ""
        if cls != "tg-spoiler":
            return None
        return '<span class="tg-spoiler">'
    if name_l == "code":
        m = _CLASS_RE.search(rest)
        cls = (m.group(1) or m.group(2) or "").strip() if m else ""
        if cls.startswith("language-") and re.fullmatch(r"language-[a-zA-Z0-9_+\-]+", cls):
            return f'<code class="{cls}">'
        return "<code>"
    return f"<{name_l}>"


def sanitize_telegram_html(text: str) -> str:
    """Return a Telegram-ParseMode.HTML-safe rendering of `text`.

    - Allowed tags are kept (with attrs whitelisted: href on <a>,
      class="tg-spoiler" on <span>, class="language-X" on <code>).
    - Disallowed tags are dropped, content kept.
    - Unbalanced opens are auto-closed at end of input.
    - Orphan closes (close without matching open) are dropped.
    - `<`, `>`, and stray `&` in text are escaped.
    """
    if not text:
        return text
    open_stack: list[str] = []
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        m = _TAG_RE.search(text, i)
        if m is None:
            out.append(_escape_text_chunk(text[i:]))
            break
        if m.start() > i:
            out.append(_escape_text_chunk(text[i : m.start()]))
        name = m.group("name").lower()
        is_close = bool(m.group("close"))
        rest = m.group("rest") or ""
        if name not in _ALLOWED_TAGS:
            # Drop the tag, keep nothing for it. Content between
            # disallowed open/close pairs stays in the output via the
            # text branch above.
            i = m.end()
            continue
        if is_close:
            # Pop the most recent matching open. Orphan closes are
            # dropped silently.
            if name in open_stack:
                while open_stack and open_stack[-1] != name:
                    # Close inner-nested tags to keep the document
                    # well-formed when the LLM emits crossed tags.
                    closed = open_stack.pop()
                    out.append(f"</{closed}>")
                if open_stack and open_stack[-1] == name:
                    open_stack.pop()
                    out.append(f"</{name}>")
        else:
            tag_str = _normalize_open_tag(name, rest)
            if tag_str is None:
                # Dropped (e.g. unsafe href) — content between this
                # and its matching close stays as text.
                i = m.end()
                continue
            out.append(tag_str)
            open_stack.append(name)
        i = m.end()
    # Auto-close anything still open.
    while open_stack:
        out.append(f"</{open_stack.pop()}>")
    return "".join(out)


_STRIP_HTML_RE = re.compile(r"<[^>]+>")


def strip_html_to_plain(text: str) -> str:
    """Last-resort fallback: remove every tag, unescape entities.

    Used when sanitize_telegram_html fails or when send-time validation
    rejects the sanitized output anyway.
    """
    if not text:
        return text
    s = _STRIP_HTML_RE.sub("", text)
    s = (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    return s


# ── splitter ─────────────────────────────────────────────────────────


def _walk_tags_state(text: str) -> list[str]:
    """Return the open-tag stack at the END of `text`.

    Mirrors the bookkeeping in sanitize_telegram_html but only tracks
    the stack — no rewriting. Used by the splitter to decide which
    tags need closing when a chunk ends mid-tag.
    """
    stack: list[str] = []
    for m in _TAG_RE.finditer(text):
        name = m.group("name").lower()
        if name not in _ALLOWED_TAGS:
            continue
        if m.group("close"):
            if name in stack:
                while stack and stack[-1] != name:
                    stack.pop()
                if stack and stack[-1] == name:
                    stack.pop()
        else:
            stack.append(name)
    return stack


def _open_tags_at(prefix: str, text: str) -> list[str]:
    """Open tags active at the boundary `len(prefix)` in `text`.

    Scans only the prefix — equivalent to `_walk_tags_state(prefix)`.
    """
    return _walk_tags_state(prefix)


def _safe_break_position(text: str, start: int, hard_max: int) -> int:
    """Find the best break inside `text[start:start+hard_max]`.

    Tries (in order): \\n\\n, \\n, ' '. Falls back to `hard_max`.
    Returns the absolute offset (from text[0]).
    """
    end = min(start + hard_max, len(text))
    window = text[start:end]
    for sep in ("\n\n", "\n", " "):
        idx = window.rfind(sep)
        if idx > 0:
            return start + idx + len(sep)
    return end


def _emit_chunk(buffer: str, carry_open: list[str]) -> tuple[str, list[str]]:
    """Close open tags at end-of-chunk, emit, return new chunk + tags
    that should be reopened at the start of the next chunk."""
    open_at_end = _walk_tags_state(buffer)
    if not open_at_end:
        return buffer, []
    closes = "".join(f"</{tag}>" for tag in reversed(open_at_end))
    return buffer + closes, list(open_at_end)


def _reopen_prefix(carry: list[str]) -> str:
    return "".join(f"<{tag}>" for tag in carry)


def split_for_telegram(text: str, cap: int = TELEGRAM_OPERATIONAL_CAP) -> list[str]:
    """Split `text` into chunks each ≤ `cap` chars.

    Policy is Option C:
    - Single chunk if the whole thing fits.
    - Otherwise split on `\\n\\n`. Accumulate paragraphs; flush when
      adding the next one would overflow.
    - If a single paragraph alone exceeds `cap`, hard-break it
      (backscan for `\\n`, then ` `, else exact cut).
    - Each chunk is HTML-balanced: open tags at the cut are closed
      at the end of chunk N and reopened at the start of chunk N+1.
    """
    if not text:
        return [""]
    if len(text) <= cap:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    carry: list[str] = []  # tags to reopen at start of next chunk

    def _flush():
        nonlocal current, carry
        if not current:
            return
        chunk, new_carry = _emit_chunk(current, carry)
        chunks.append(chunk)
        carry = new_carry
        current = _reopen_prefix(carry)

    for idx, para in enumerate(paragraphs):
        sep = "\n\n" if current and not current.endswith("\n\n") else ""
        prospective = current + sep + para if current else para
        if len(prospective) <= cap:
            current = prospective
            continue
        # Adding this paragraph overflows. Flush what we have first.
        if current and current != _reopen_prefix(carry):
            _flush()
        # Now try to fit `para` alone (after carry).
        if len(current + para) <= cap:
            current = current + para
            continue
        # Single paragraph too big — hard-break it.
        i = 0
        while i < len(para):
            remaining_cap = cap - len(current)
            if remaining_cap <= len(_reopen_prefix(carry)):
                # Carry alone already eats most of the cap; flush.
                _flush()
                remaining_cap = cap - len(current)
            cut = _safe_break_position(para, i, remaining_cap)
            if cut <= i:
                cut = min(i + remaining_cap, len(para))
            current = current + para[i:cut]
            i = cut
            if i < len(para):
                _flush()
        # End paragraph loop; if more paragraphs follow, the next
        # iteration will add `\n\n` separator.

    if current:
        # Last chunk doesn't need closing-then-reopening — just close.
        chunk, _ = _emit_chunk(current, carry)
        chunks.append(chunk)

    # Last-resort safety: any chunk over the hard limit is a bug.
    safe_chunks: list[str] = []
    for c in chunks:
        if len(c) > TELEGRAM_HARD_LIMIT:
            log.critical(
                "split_chunk_overflow chunk_len=%d hard_limit=%d",
                len(c),
                TELEGRAM_HARD_LIMIT,
            )
            ellipsis = "…"
            safe_chunks.append(c[: TELEGRAM_HARD_LIMIT - len(ellipsis)] + ellipsis)
        else:
            safe_chunks.append(c)
    return safe_chunks


# ── error handling ───────────────────────────────────────────────────

# User-facing strings live here, NOT in bot/messages_es.py — these
# are queries-layer concerns and the channel-agnostic dispatcher
# returns text directly. Block 9 may move them to messages_es when
# wiring up the Telegram handler.
_MSG_ITERATION_CAP = (
    "No pude completar tu consulta en el tiempo esperado. Probá reformulando."
)
_MSG_TRANSIENT = "Hubo un problema temporal. Probá de nuevo en un minuto."
_MSG_ADMIN = "El servicio está temporalmente fuera de línea. Avisale al admin."
_MSG_TOOL_FAILURE = "Algo se rompió consultando tus datos. Avisale al admin."
_MSG_BUDGET = "Llegaste al límite diario de consultas. Se renueva mañana."
_MSG_GENERIC = "Algo se rompió consultando tus datos. Avisale al admin."


def handle_query_error(
    exc: BaseException,
    *,
    user_id: Optional[uuid.UUID | str] = None,
    query_id: Optional[uuid.UUID | str] = None,
) -> str:
    """Map a known exception class to a user-facing Spanish message.

    Logs structured info (user_id, query_id, exception class, category)
    at the level dictated by the catalog. Never leaks stack traces or
    internal IDs to the returned string.

    Imports of `IterationCapExceeded` / `QueryLLMClientError` are local
    so this module stays importable without the SDK loaded.
    """
    from .llm_client import IterationCapExceeded, QueryLLMClientError

    ctx = {"user_id": str(user_id) if user_id else None, "query_id": str(query_id) if query_id else None}

    if isinstance(exc, BudgetExceeded):
        log.info("budget_exceeded ctx=%s", ctx)
        return _MSG_BUDGET

    if isinstance(exc, IterationCapExceeded):
        log.warning("iteration_cap_exceeded ctx=%s", ctx)
        return _MSG_ITERATION_CAP

    if isinstance(exc, QueryLLMClientError):
        category = getattr(exc, "category", "unknown")
        if category == "timeout":
            log.warning("llm_timeout ctx=%s", ctx)
            return _MSG_ITERATION_CAP
        if category == "rate_limit":
            log.warning("llm_rate_limit ctx=%s", ctx)
            return _MSG_TRANSIENT
        if category == "server_error":
            log.error("llm_server_error ctx=%s", ctx)
            return _MSG_TRANSIENT
        if category == "auth_error":
            log.critical("llm_auth_error ctx=%s", ctx)
            return _MSG_ADMIN
        if category == "client_error":
            log.error("llm_client_error ctx=%s", ctx)
            return _MSG_GENERIC
        log.error("llm_unknown_category ctx=%s exc=%s", ctx, type(exc).__name__)
        return _MSG_GENERIC

    if isinstance(exc, ToolExecutionError):
        log.error("tool_execution_error ctx=%s exc=%s", ctx, exc)
        return _MSG_TOOL_FAILURE

    if isinstance(exc, HTMLSanitizationFailed):
        log.error("html_sanitization_failed ctx=%s", ctx)
        # Caller decides what to send (likely strip_html_to_plain).
        return _MSG_GENERIC

    if isinstance(exc, ChunkOverflow):
        log.critical("chunk_overflow ctx=%s", ctx)
        return _MSG_GENERIC

    log.error("unhandled_query_exception ctx=%s exc_type=%s", ctx, type(exc).__name__)
    return _MSG_GENERIC
