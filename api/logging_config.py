"""Stdout-only logging for container deployments.

Container Apps / any modern orchestrator captures stdout+stderr — never
write to log files from inside the container. `setup_logging` is called
once from the FastAPI lifespan; importing this module has no side effects.
"""
from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        # Idempotent: a second call (tests, reloads) shouldn't stack handlers.
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
