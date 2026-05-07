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

    # Secret store (Phase 6b)
    secret_store_backend: str = "env"  # env | file | azure_kv
    azure_key_vault_url: str = ""
    dev_secret_prefix: str = "DEV_SECRET_"
    file_secret_store_path: str = ""  # default .dev_secrets.json in cwd

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
