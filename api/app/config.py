from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App config, read from env vars (see ../.env.example)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str
    data_dir: str = "data"
    models_dir: str = "models"
    model_path: str = "models/best.pt"
    frame_sample_fps: float = 1.0
    min_confidence: float = 0.25
    dedupe_radius_m: float = 5.0
    max_video_size_mb: int = 2048
    max_photos_per_upload: int = 100
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
