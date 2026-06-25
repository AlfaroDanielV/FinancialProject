"""Daily Gmail worker error-handling hardening.

Pins the contract that a *systemic* failure (import / secret-store / config)
fails the whole job loudly instead of being swallowed per-user — the trap that
let the 2026-06-25 incident report Succeeded while scanning nobody (the worker
image lacked `--extra azure`, so every user's scan raised
SecretStoreUnavailable, was caught per-user, and the job stayed green).

Pure unit tests over the classification + run-health helpers — no DB or network.
"""
from __future__ import annotations

import pytest

from api.services.secrets import SecretStoreUnavailable
from workers import gmail_daily


# ── _is_systemic: walks the cause/context chain ────────────────────────────────


def test_is_systemic_direct_secret_store():
    assert gmail_daily._is_systemic(SecretStoreUnavailable("no azure"))


def test_is_systemic_import_error():
    assert gmail_daily._is_systemic(ModuleNotFoundError("No module named 'azure'"))


def test_is_systemic_through_cause_chain():
    """The real incident: SecretStoreUnavailable raised `from` ImportError,
    then re-wrapped by the time it reaches the worker. Either link counts."""
    try:
        try:
            raise ModuleNotFoundError("No module named 'azure'")
        except ModuleNotFoundError as cause:
            raise SecretStoreUnavailable("needs --extra azure") from cause
    except SecretStoreUnavailable as exc:
        wrapped = RuntimeError("scan failed")
        wrapped.__cause__ = exc
        assert gmail_daily._is_systemic(wrapped)


def test_per_user_error_is_not_systemic():
    """A Gmail API hiccup / validation crash for one user must stay soft."""
    assert not gmail_daily._is_systemic(ValueError("bad email body"))
    assert not gmail_daily._is_systemic(RuntimeError("gmail 503"))


def test_is_systemic_handles_self_referential_chain():
    """Defensive: a cycle in __context__ must not loop forever."""
    a = RuntimeError("a")
    b = ValueError("b")
    a.__context__ = b
    b.__context__ = a
    assert gmail_daily._is_systemic(a) is False


# ── _raise_if_all_failed: finished-but-empty run is a red flag ─────────────────


def test_all_failed_raises():
    with pytest.raises(RuntimeError, match="all 3 user"):
        gmail_daily._raise_if_all_failed(ok=0, failed=3)


def test_partial_failure_does_not_raise():
    gmail_daily._raise_if_all_failed(ok=2, failed=1)  # some succeeded → fine


def test_all_ok_does_not_raise():
    gmail_daily._raise_if_all_failed(ok=5, failed=0)


def test_no_eligible_users_does_not_raise():
    """Zero users is the normal steady state before anyone connects Gmail —
    it must NOT fail the job."""
    gmail_daily._raise_if_all_failed(ok=0, failed=0)
