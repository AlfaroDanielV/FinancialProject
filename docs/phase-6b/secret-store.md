# Phase 6b — Secret store backends

The `SecretStore` interface (`api/services/secrets.py`) is how the
Gmail flow persists OAuth refresh tokens. Three backends, picked by
`SECRET_STORE_BACKEND`:

| Backend     | When                              | Persists across restart?    | Notes |
|-------------|-----------------------------------|-----------------------------|-------|
| `env`       | Tests, CI, brief smoke runs       | **No** — process-local      | Default. Reads/writes `os.environ[DEV_SECRET_GMAIL_REFRESH_<UUID>]`. |
| `file`      | Local dev with iteration          | **Yes** — `.dev_secrets.json` (gitignored) | Plaintext on disk. Owner-only file mode (0600). |
| `azure_kv`  | Production                        | **Yes** — Key Vault         | Requires `uv sync --extra azure`. Auth via `DefaultAzureCredential` (managed identity in Container Apps, `az login` locally). |

## When to use which

- **Default to `env`** for first-time setup, throwaway tests, and CI.
  No state to clean up.
- **Switch to `file`** the moment you find yourself running
  `/conectar_gmail` more than once after restarts. The Block B
  diagnostic flagged this trap: a real refresh token written to
  `os.environ` evaporates when uvicorn dies, leaving the user looking
  at "invalid_grant" errors with no obvious cause. The boot log
  warns when `SECRET_STORE_BACKEND=env` so the dev sees this before
  hitting it.
- **`azure_kv` is prod-only.** Don't use it in dev unless you're
  specifically testing the Azure auth path — it's an extra
  ~10MB of dependencies and 3+ HTTP calls per scan.

## Switching backend

`.env`:

```ini
# pick one
SECRET_STORE_BACKEND=env
SECRET_STORE_BACKEND=file
SECRET_STORE_BACKEND=azure_kv
```

The factory caches the chosen backend on first call. Tests and dev
reload via `secrets.reset_store()`; production picks up the new value
on the next process restart.

## File backend specifics

- Default path: `.dev_secrets.json` in the cwd uvicorn was launched
  from. Override via `FILE_SECRET_STORE_PATH=/some/path.json`.
- Format: a single flat JSON object `{name: value, ...}`. Names are
  whatever the caller passed (`gmail-refresh-<uuid>` for the Gmail
  flow). Values are arbitrary strings.
- Atomicity: writes go to `<path>.tmp` then `os.replace` to the final
  path. Won't leave a partial file if the process is killed mid-write.
- File mode: 0600 on Unix (owner read/write only). Skipped silently on
  platforms that don't support `os.chmod`.
- Concurrency: an `asyncio.Lock` serializes within a single process.
  Cross-process writes (e.g. API + worker running concurrently) use
  last-writer-wins. Acceptable for the personal MVP.

## Azure Key Vault backend specifics

- Secret names follow the same convention (hyphens, no underscores —
  KV rejects underscores).
- Requires the env `AZURE_KEY_VAULT_URL=https://<kv>.vault.azure.net/`.
- The factory raises `RuntimeError` on import if `azure-identity` /
  `azure-keyvault-secrets` aren't installed (e.g. you forgot
  `uv sync --extra azure` after deploying).
- Soft-delete is the default in Key Vault; `delete()` waits for the
  poller to complete deletion (not purge). Re-creating a previously
  deleted secret with the same name works after the soft-delete window
  expires (default 90 days).
