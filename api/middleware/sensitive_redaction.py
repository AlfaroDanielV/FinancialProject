"""Phase 6c B8 — strip user_insights payloads from log records.

Privacy commitment: no PII from `user_insights.content` (or audit
`previous_content` / `new_content` / `content_at_deletion`) leaks into
stdout logs, error tracebacks, or APM events.

This is a `logging.Filter` installed on the root logger from
`api/main.py` lifespan. It walks two surfaces of every `LogRecord`:

  1. `record.args` — positional format args (typically dict/tuple)
  2. `record.__dict__` — extras passed via `log.info(..., extra={...})`

Any dict it finds gets recursively scrubbed: keys named one of the
SENSITIVE_KEYS have their value replaced with the redaction marker.

The redaction rule is intentionally simple — match by key name. The
keys are specific enough that collateral damage on unrelated `content`
fields is acceptable; the privacy upside is the whole point.
"""
from __future__ import annotations

import logging
from typing import Any

REDACTION_MARKER = "[redacted]"

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "content",
        "content_at_deletion",
        "previous_content",
        "new_content",
    }
)

# Internal LogRecord attributes we MUST NOT touch — overwriting them
# breaks the formatter (or pytest's caplog).
_RECORD_RESERVED: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class SensitiveRedactionFilter(logging.Filter):
    """Logging filter that scrubs user-insight payloads in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        # 1. Walk record.args — Python's logging passes positional %-format
        # arguments here. A common shape: log.info("...", row.content) makes
        # args=(row.content,) — we redact dicts inside.
        if record.args:
            record.args = _walk(record.args)

        # 2. Walk extras attached via `extra={...}`. Logging copies them
        # onto the record's __dict__. Skip the reserved attributes.
        for key in list(record.__dict__.keys()):
            if key in _RECORD_RESERVED or key.startswith("_"):
                continue
            if key in SENSITIVE_KEYS:
                record.__dict__[key] = REDACTION_MARKER
                continue
            record.__dict__[key] = _walk(record.__dict__[key])

        return True


def _walk(value: Any) -> Any:
    """Recursively scrub sensitive keys in dicts; pass other types through."""
    if isinstance(value, dict):
        return {
            k: REDACTION_MARKER if k in SENSITIVE_KEYS else _walk(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_walk(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk(item) for item in value)
    return value


def install_on_root_logger() -> SensitiveRedactionFilter:
    """Attach a single instance of the filter to the root logger.

    Idempotent — re-installing replaces any prior instance so reloads
    and tests don't stack filters.
    """
    root = logging.getLogger()
    for existing in list(root.filters):
        if isinstance(existing, SensitiveRedactionFilter):
            root.removeFilter(existing)
    flt = SensitiveRedactionFilter()
    root.addFilter(flt)
    # Also attach to each handler so child loggers that bypass propagation
    # still get scrubbed at write time.
    for handler in list(root.handlers):
        for existing in list(handler.filters):
            if isinstance(existing, SensitiveRedactionFilter):
                handler.removeFilter(existing)
        handler.addFilter(flt)
    return flt
