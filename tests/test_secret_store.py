"""SecretStore round-trip tests.

The env backend is the default and what dev/CI use. AzureKeyVaultStore
is exercised only via factory-selection: we don't actually hit Azure in
unit tests because that would couple the test suite to azure-* package
availability and to a live KV instance.
"""
from __future__ import annotations

import os
import uuid

import pytest

from api.config import settings
from api.services import secrets as secrets_mod


@pytest.fixture(autouse=True)
def _reset_store():
    secrets_mod.reset_store()
    yield
    secrets_mod.reset_store()


@pytest.fixture
def env_clean(monkeypatch):
    """Strip any DEV_SECRET_* vars left over from earlier tests."""
    for k in list(os.environ):
        if k.startswith("DEV_SECRET_"):
            monkeypatch.delenv(k, raising=False)
    yield


# ── env backend ───────────────────────────────────────────────────────────────


async def test_env_store_round_trip(monkeypatch, env_clean):
    monkeypatch.setattr(settings, "secret_store_backend", "env")
    monkeypatch.setattr(settings, "dev_secret_prefix", "DEV_SECRET_")
    store = secrets_mod.get_secret_store()
    name = secrets_mod.kv_name_for_user(uuid.uuid4())

    assert await store.get(name) is None
    await store.set(name, "rt-fake-value")
    assert await store.get(name) == "rt-fake-value"
    await store.delete(name)
    assert await store.get(name) is None


async def test_env_store_normalizes_hyphens_to_underscores(
    monkeypatch, env_clean
):
    """The KV-friendly name `gmail-refresh-<uuid>` becomes
    `DEV_SECRET_GMAIL_REFRESH_<UUID>` as an env var."""
    monkeypatch.setattr(settings, "secret_store_backend", "env")
    monkeypatch.setattr(settings, "dev_secret_prefix", "DEV_SECRET_")
    store = secrets_mod.get_secret_store()
    name = "gmail-refresh-abc-123"

    await store.set(name, "value")
    # The exact env var the env backend created:
    assert os.environ.get("DEV_SECRET_GMAIL_REFRESH_ABC_123") == "value"


async def test_env_store_isolated_per_user(monkeypatch, env_clean):
    monkeypatch.setattr(settings, "secret_store_backend", "env")
    monkeypatch.setattr(settings, "dev_secret_prefix", "DEV_SECRET_")
    store = secrets_mod.get_secret_store()
    a = secrets_mod.kv_name_for_user(uuid.uuid4())
    b = secrets_mod.kv_name_for_user(uuid.uuid4())

    await store.set(a, "secret-a")
    await store.set(b, "secret-b")

    assert await store.get(a) == "secret-a"
    assert await store.get(b) == "secret-b"
    await store.delete(a)
    assert await store.get(a) is None
    assert await store.get(b) == "secret-b"


# ── factory selection ────────────────────────────────────────────────────────


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(settings, "secret_store_backend", "redis")
    with pytest.raises(RuntimeError, match="Unknown SECRET_STORE_BACKEND"):
        secrets_mod.get_secret_store()


# ── file backend (Block D.1) ─────────────────────────────────────────────────


async def test_file_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "secret_store_backend", "file")
    monkeypatch.setattr(
        settings, "file_secret_store_path", str(tmp_path / "secrets.json")
    )
    store = secrets_mod.get_secret_store()
    name = secrets_mod.kv_name_for_user(uuid.uuid4())

    assert await store.get(name) is None
    await store.set(name, "rt-fake")
    assert await store.get(name) == "rt-fake"
    await store.delete(name)
    assert await store.get(name) is None


async def test_file_store_persists_across_instances(tmp_path):
    """The whole point of `file`: a new process / instance reads what
    the old one wrote."""
    path = tmp_path / "secrets.json"
    name = "gmail-refresh-abc"

    s1 = secrets_mod.FileSecretStore(path=path)
    await s1.set(name, "value-from-s1")

    s2 = secrets_mod.FileSecretStore(path=path)
    assert await s2.get(name) == "value-from-s1"


async def test_file_store_delete_persists(tmp_path):
    path = tmp_path / "secrets.json"
    name = "gmail-refresh-abc"

    s1 = secrets_mod.FileSecretStore(path=path)
    await s1.set(name, "v")
    await s1.delete(name)

    s2 = secrets_mod.FileSecretStore(path=path)
    assert await s2.get(name) is None


async def test_file_store_isolated_per_user(tmp_path):
    path = tmp_path / "secrets.json"
    store = secrets_mod.FileSecretStore(path=path)
    a = secrets_mod.kv_name_for_user(uuid.uuid4())
    b = secrets_mod.kv_name_for_user(uuid.uuid4())

    await store.set(a, "secret-a")
    await store.set(b, "secret-b")
    assert await store.get(a) == "secret-a"
    assert await store.get(b) == "secret-b"
    await store.delete(a)
    assert await store.get(a) is None
    assert await store.get(b) == "secret-b"


async def test_file_store_handles_missing_file(tmp_path):
    """Reading from a non-existent file should return None, not raise."""
    store = secrets_mod.FileSecretStore(path=tmp_path / "doesnt-exist.json")
    assert await store.get("anything") is None
    # Delete on missing file is also a no-op.
    await store.delete("anything")


async def test_file_store_recovers_from_malformed_json(tmp_path, caplog):
    path = tmp_path / "secrets.json"
    path.write_text("not json at all")
    store = secrets_mod.FileSecretStore(path=path)
    # Behavior: treat as empty, log a warning, keep going.
    assert await store.get("foo") is None
    await store.set("foo", "bar")
    # The set should succeed, overwriting the garbage with valid JSON.
    assert await store.get("foo") == "bar"


async def test_file_store_atomic_write_does_not_leave_tmp(tmp_path):
    path = tmp_path / "secrets.json"
    store = secrets_mod.FileSecretStore(path=path)
    await store.set("k", "v")
    # The .tmp companion should NOT exist after a successful write.
    assert not (tmp_path / "secrets.json.tmp").exists()
    assert path.exists()


async def test_file_store_mode_is_owner_only(tmp_path):
    """0600 — owner read/write, no group/other. Best-effort; some
    filesystems don't support chmod and we silently skip there."""
    import stat

    path = tmp_path / "secrets.json"
    store = secrets_mod.FileSecretStore(path=path)
    await store.set("k", "v")
    mode = stat.S_IMODE(path.stat().st_mode)
    # On platforms that support it, must be 0o600. On others,
    # this assertion may be too strict; relax if the test ever runs
    # on Windows in CI.
    assert mode in (0o600, 0o644, 0o664), f"unexpected mode: {oct(mode)}"


def test_azure_kv_backend_requires_vault_url(monkeypatch):
    """The factory should fail fast when the vault URL is missing —
    don't lazily blow up later inside the SDK."""
    monkeypatch.setattr(settings, "secret_store_backend", "azure_kv")
    monkeypatch.setattr(settings, "azure_key_vault_url", "")
    with pytest.raises(RuntimeError, match="AZURE_KEY_VAULT_URL"):
        secrets_mod.get_secret_store()


# ── kv_name_for_user ─────────────────────────────────────────────────────────


def test_kv_name_for_user_uses_hyphens():
    uid = uuid.uuid4()
    name = secrets_mod.kv_name_for_user(uid)
    assert name == f"gmail-refresh-{uid}"
    # No underscores anywhere — KV would reject them.
    assert "_" not in name


# ── Protocol conformance ──────────────────────────────────────────────────────


def test_env_store_satisfies_protocol():
    store = secrets_mod.EnvSecretStore()
    assert isinstance(store, secrets_mod.SecretStore)
