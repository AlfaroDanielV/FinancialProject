"""Production secret-store guard (api/config.Settings._enforce_prod_secret_store).

Production must store Gmail OAuth refresh tokens in Azure Key Vault — never the
dev-only `env`/`file` backends (plaintext / process env). The validator turns a
misconfigured prod deploy into a loud boot failure. Init kwargs override any
`.env` / OS env (pydantic-settings precedence), so these are hermetic.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import Settings


@pytest.mark.parametrize("backend", ["env", "file"])
def test_prod_rejects_insecure_backends(backend):
    with pytest.raises(ValidationError):
        Settings(environment="production", secret_store_backend=backend)


def test_prod_azure_kv_requires_vault_url():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            secret_store_backend="azure_kv",
            azure_key_vault_url="",
        )


def test_prod_azure_kv_with_url_ok():
    s = Settings(
        environment="production",
        secret_store_backend="azure_kv",
        azure_key_vault_url="https://vault.vault.azure.net/",
    )
    assert s.secret_store_backend == "azure_kv"
    assert s.is_dev is False


@pytest.mark.parametrize("backend", ["env", "file", "azure_kv"])
def test_dev_allows_any_backend(backend):
    # Development is unconstrained (azure_kv won't construct a client here —
    # the validator only checks config shape, not connectivity).
    s = Settings(environment="development", secret_store_backend=backend)
    assert s.is_dev is True
