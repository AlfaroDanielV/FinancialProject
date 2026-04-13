from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://finance:finance@localhost:5432/finance"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""

    # App
    environment: str = "development"
    secret_key: str = "change-me"

    # Single-user MVP
    default_user_id: str = ""

    # iPhone Shortcut auth
    shortcut_token: str = "change-me"

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
