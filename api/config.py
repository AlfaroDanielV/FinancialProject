from decimal import Decimal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://finance:finance@localhost:5432/finance"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    llm_extraction_model: str = "claude-haiku-4-5"
    llm_query_model: str = "claude-sonnet-4-5"
    llm_query_iteration_cap: int = 4
    llm_daily_token_budget_per_user: int = 100_000
    insights_extractor_enabled: bool = False
    # Phase 6c B11: gate on whether the Sonnet query dispatcher exposes
    # `get_user_context` as a tool (and includes the memory section + the
    # Example 6/7 few-shots in the system prompt). Off during shadow
    # validation; flipped to True after the 7-day review per B12.
    insights_dispatcher_enabled: bool = False
    llm_insight_input_usd_per_mtok: Decimal = Decimal("1.00")
    llm_insight_output_usd_per_mtok: Decimal = Decimal("5.00")
    llm_insight_cache_read_usd_per_mtok: Decimal = Decimal("0.10")
    llm_insight_cache_write_usd_per_mtok: Decimal = Decimal("1.25")

    # Telegram (Phase 5b)
    telegram_bot_token: str = ""
    telegram_mode: str = "disabled"  # disabled | polling | webhook
    telegram_webhook_secret: str = ""
    telegram_webhook_url: str = ""

    # Nudge scheduler — drives the Phase 5d proactive-messaging pipeline
    # (evaluate → deliver) on an interval so nudges actually fire without an
    # external cron. The in-process loop lives in the API lifespan; the same
    # fan-out also runs standalone via `python -m workers.nudges_daily`
    # (prod ACA Job / manual). Anti-saturation (rate limit, silence, quiet
    # hours, dedup) makes a frequent interval safe. Don't enable BOTH the loop
    # and an external cron against the worker — idempotent, just wasteful.
    nudge_scheduler_enabled: bool = True
    nudge_scheduler_interval_s: int = 21600  # 6h
    nudge_scheduler_initial_delay_s: int = 60  # let boot settle before tick 1

    # App
    environment: str = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"

    # Gmail / OAuth (Phase 6b)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_redirect_uri: str = (
        "http://localhost:8000/api/v1/gmail/oauth/callback"
    )
    gmail_oauth_state_secret: str = ""
    gmail_oauth_state_ttl_s: int = 600
    gmail_batch_threshold: int = 5
    gmail_discovery_cooldown_s: int = 600
    gmail_discovery_max_messages: int = 200

    # Secret store (Phase 6b). env | file | azure_kv. The `env`/`file` backends
    # are DEV-ONLY (os.environ / plaintext .dev_secrets.json) — production MUST
    # use azure_kv, enforced by `_enforce_prod_secret_store` below. The literal
    # default stays `env` so local/CI runs work without Azure libs; the prod
    # guard is the real protection.
    secret_store_backend: str = "env"
    azure_key_vault_url: str = ""
    dev_secret_prefix: str = "DEV_SECRET_"
    file_secret_store_path: str = ""  # default .dev_secrets.json in cwd

    # Magic-link auth (Phase 6d B3). Phase 6f B16 removed spa_base_url +
    # spa_cors_origins with the SPA.
    magic_link_session_secret: str = "change-me-in-prod-magic-link-session"
    magic_link_ttl_s: int = 1800  # 30 min
    # Session JWT lifetime (shared by magic-link + device-code exchange). The
    # SPA `fa_session` cookie was removed at Phase 6f B16; the bearer JWT keeps
    # this TTL.
    session_ttl_s: int = 14400  # 4h
    bcrypt_rounds: int = 12

    # Native app deep links (Phase 6f B15). Custom URL scheme registered in
    # mobile/app.json. A `<scheme>://exchange?token=...` link opens the app and
    # the silent `useMagicLinkListener` exchanges the token for a session JWT.
    native_app_scheme: str = "ledgercr"

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"

    @model_validator(mode="after")
    def _enforce_prod_secret_store(self) -> "Settings":
        """Fail fast if production would store Gmail OAuth refresh tokens
        insecurely.

        `env` keeps them in `os.environ` (visible in `ps eww` / crash dumps and
        lost on every container restart → silent disconnects); `file` writes
        plaintext to the container's ephemeral disk (leaks via snapshots /
        backups / `az containerapp exec`). Either one in production is an
        OAuth-token exposure, so production must use Azure Key Vault. Raising
        here means a misconfigured deploy fails loudly at boot instead of
        silently persisting secrets to disk.
        """
        if self.environment == "production":
            if self.secret_store_backend.lower() != "azure_kv":
                raise ValueError(
                    "SECRET_STORE_BACKEND must be 'azure_kv' in production "
                    f"(got {self.secret_store_backend!r}); env/file store OAuth "
                    "refresh tokens insecurely."
                )
            if not self.azure_key_vault_url:
                raise ValueError(
                    "AZURE_KEY_VAULT_URL is required in production "
                    "(SECRET_STORE_BACKEND=azure_kv)."
                )
        return self


settings = Settings()
