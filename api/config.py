from decimal import Decimal

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

    # Secret store (Phase 6b)
    secret_store_backend: str = "env"  # env | file | azure_kv
    azure_key_vault_url: str = ""
    dev_secret_prefix: str = "DEV_SECRET_"
    file_secret_store_path: str = ""  # default .dev_secrets.json in cwd

    # Magic-link auth (Phase 6d B3)
    spa_base_url: str = "http://localhost:5173"
    spa_cors_origins: str = ""  # comma-separated; defaults to spa_base_url
    magic_link_session_secret: str = "change-me-in-prod-magic-link-session"
    magic_link_ttl_s: int = 1800  # 30 min
    session_cookie_name: str = "fa_session"
    session_cookie_ttl_s: int = 14400  # 4h
    session_cookie_domain: str = ""  # empty = host-only (dev)
    session_cookie_secure: bool = False  # dev=false, prod=true
    bcrypt_rounds: int = 12

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
