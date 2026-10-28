from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App config, read from env vars (see ../.env.example)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str
    data_dir: str = "data"
    models_dir: str = "models"


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
