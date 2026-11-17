from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MetaCrux AI — Adaptive AI Sales Assistant"
    environment: str = "development"
    database_url: str = "sqlite:///./data/aisa.db"
    llm_provider: str = "local"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
