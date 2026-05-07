"""Async secret store abstraction.

Three backends, selected by `SECRET_STORE_BACKEND`:

    env       — read/write via os.environ. Prefix `DEV_SECRET_`. For
                CI / tests / brief smokes. Mutations apply only to the
                running process; restart loses them.
    file      — JSON file `.dev_secrets.json` in cwd (gitignored).
                Persistent across uvicorn restarts. Recommended for
                local dev when iterating against real Gmail.
    azure_kv  — Azure Key Vault, accessed via DefaultAzureCredential.
                Used in production. The azure-* libs are an optional
                install (see pyproject.toml `[project.optional-dependencies]
                azure`). Importing them lazily means dev envs don't need
                them.

Naming convention used by the Gmail flow: `gmail-refresh-{user_id}`.
KV doesn't allow underscores in secret names, only hyphens — that's why
we use hyphens. The env backend translates hyphens to underscores
internally; the file backend stores the names verbatim.

Why not just os.environ directly: explicit boundary. Code that touches a
SecretStore is auditable; code that touches os.environ is not.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


_log = logging.getLogger("api.services.secrets")


# We deliberately re-import `settings` inside get_secret_store() rather
# than at module level. Tests monkeypatch `settings.secret_store_backend`
# at runtime; importing the singleton lazily keeps the selector honest.


@runtime_checkable
class SecretStore(Protocol):
    """Async interface every backend must satisfy.

    `get` returns None when the secret does not exist. Other failures
    (auth errors, network, etc.) raise. Callers handle "missing" as a
    real state but treat raises as bugs / outages.
    """

    async def get(self, name: str) -> Optional[str]: ...
    async def set(self, name: str, value: str) -> None: ...
    async def delete(self, name: str) -> None: ...


# ── env backend (dev / tests) ─────────────────────────────────────────────────


_ENV_NAME_RE = re.compile(r"[^A-Z0-9_]")


class EnvSecretStore:
    """In-process backend backed by os.environ.

    A secret named `gmail-refresh-<uuid>` becomes the env var
    `DEV_SECRET_GMAIL_REFRESH_<UUID>` (uppercased, hyphens → underscores).
    Set/delete mutate `os.environ` for the running process only — handy
    for tests and short-lived local dev. Do NOT use in prod: secrets in
    env are visible in `ps eww` and crash dumps.
    """

    def __init__(self, prefix: str = "DEV_SECRET_") -> None:
        if not prefix.endswith("_"):
            prefix += "_"
        self.prefix = prefix

    def _key(self, name: str) -> str:
        normalized = _ENV_NAME_RE.sub(
            "_", name.upper().replace("-", "_")
        )
        return f"{self.prefix}{normalized}"

    async def get(self, name: str) -> Optional[str]:
        return os.environ.get(self._key(name))

    async def set(self, name: str, value: str) -> None:
        os.environ[self._key(name)] = value

    async def delete(self, name: str) -> None:
        os.environ.pop(self._key(name), None)


# ── file backend (dev local, persistent across restarts) ─────────────────────


_DEFAULT_FILE_PATH = Path(".dev_secrets.json")


class FileSecretStore:
    """Single-file JSON-backed store for dev iteration.

    Writes to `.dev_secrets.json` in cwd by default. The file is
    gitignored. Plaintext on disk — acceptable for an individual dev
    box, NOT for shared / production environments.

    Concurrency: an asyncio lock serializes reads/writes within a
    single process. We don't fcntl across processes; if you run the
    API and the daily worker concurrently both writing tokens, the
    last writer wins. Same caveat applies to EnvSecretStore.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else _DEFAULT_FILE_PATH
        self._lock = asyncio.Lock()

    async def _read_all(self) -> dict[str, str]:
        try:
            text = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            _log.warning(
                "FileSecretStore: %s is malformed; treating as empty",
                self.path,
            )
            return {}
        if not isinstance(data, dict):
            _log.warning(
                "FileSecretStore: %s top-level is not a dict; treating as empty",
                self.path,
            )
            return {}
        # Coerce values to strings; reject anything else.
        return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}

    async def _write_all(self, data: dict[str, str]) -> None:
        # Write to a tmp file then rename for atomicity. Avoids a
        # partial write leaving the file unreadable mid-update.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(data, sort_keys=True, indent=2)

        def _write() -> None:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
            # Restrict permissions: file contains plaintext refresh
            # tokens. 0600 = owner read/write only.
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass  # best-effort on platforms that don't support chmod

        await asyncio.to_thread(_write)

    async def get(self, name: str) -> Optional[str]:
        async with self._lock:
            return (await self._read_all()).get(name)

    async def set(self, name: str, value: str) -> None:
        async with self._lock:
            data = await self._read_all()
            data[name] = value
            await self._write_all(data)

    async def delete(self, name: str) -> None:
        async with self._lock:
            data = await self._read_all()
            if name in data:
                del data[name]
                await self._write_all(data)


# ── Azure Key Vault backend (prod) ────────────────────────────────────────────


class AzureKeyVaultStore:
    """Async wrapper around azure-keyvault-secrets.

    Required env: AZURE_KEY_VAULT_URL. Auth: DefaultAzureCredential, which
    in Container Apps resolves to the managed identity assigned to the
    container. Locally, `az login` is enough.

    Imports azure libs lazily so non-Azure envs don't need them installed.
    """

    def __init__(self, vault_url: str) -> None:
        if not vault_url:
            raise RuntimeError(
                "AZURE_KEY_VAULT_URL is not set; cannot use azure_kv backend."
            )
        try:
            from azure.identity.aio import DefaultAzureCredential  # type: ignore
            from azure.keyvault.secrets.aio import SecretClient  # type: ignore
            from azure.core.exceptions import (  # type: ignore
                ResourceNotFoundError,
            )
        except ImportError as e:  # pragma: no cover — install-time issue
            raise RuntimeError(
                "azure_kv backend requires `uv sync --extra azure` "
                "(azure-identity + azure-keyvault-secrets)."
            ) from e

        self._credential = DefaultAzureCredential()
        self._client = SecretClient(
            vault_url=vault_url, credential=self._credential
        )
        self._not_found = ResourceNotFoundError

    async def get(self, name: str) -> Optional[str]:
        try:
            secret = await self._client.get_secret(name)
        except self._not_found:
            return None
        return secret.value

    async def set(self, name: str, value: str) -> None:
        await self._client.set_secret(name, value)

    async def delete(self, name: str) -> None:
        # KV's `begin_delete_secret` returns a poller. We start the delete
        # and don't block on the purge — soft-delete is the default and
        # the Gmail flow doesn't need to wait for purge.
        try:
            poller = await self._client.begin_delete_secret(name)
            await poller.wait()
        except self._not_found:
            pass


# ── module-level selector ─────────────────────────────────────────────────────


_store: Optional[SecretStore] = None


def reset_store() -> None:
    """Test helper: drop the cached store so the next call re-evaluates
    `settings.secret_store_backend`."""
    global _store
    _store = None


def get_secret_store() -> SecretStore:
    """Return the configured backend. Cached after first call."""
    global _store
    if _store is None:
        # Re-import to pick up any test-time monkeypatching on the
        # singleton settings object.
        from ..config import settings as _settings

        backend = _settings.secret_store_backend.lower()
        if backend == "env":
            _store = EnvSecretStore(prefix=_settings.dev_secret_prefix)
        elif backend == "file":
            path = (
                Path(_settings.file_secret_store_path)
                if _settings.file_secret_store_path
                else _DEFAULT_FILE_PATH
            )
            _store = FileSecretStore(path=path)
        elif backend == "azure_kv":
            _store = AzureKeyVaultStore(
                vault_url=_settings.azure_key_vault_url
            )
        else:
            raise RuntimeError(
                f"Unknown SECRET_STORE_BACKEND={backend!r}. "
                f"Expected 'env', 'file', or 'azure_kv'."
            )
        _log.info("secret store backend: %s", backend)
    return _store


# ── helper used by Gmail flow ─────────────────────────────────────────────────


def kv_name_for_user(user_id) -> str:
    """Canonical KV/secret name for a user's Gmail refresh token.

    Hyphens (not underscores) so the same name is valid in Azure Key Vault
    (which forbids underscores in secret names). The env backend
    translates internally.
    """
    return f"gmail-refresh-{user_id}"
