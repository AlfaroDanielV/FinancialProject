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

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
