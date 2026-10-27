from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    evolution_api_url: str = "http://localhost:8080"
    evolution_api_key: str = "maestro_dev_evolution_key_2026"
    evolution_default_instance: str = "magic-tour"
    tenant_default_id: str = "magic-tour"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_fallback_models: str = (
        "gemini-3.1-flash-lite-preview,gemini-3.5-flash-lite,gemini-3-flash-preview,gemini-3.5-flash,gemini-3.6-flash"
    )
    gemini_reasoning_effort: str = "low"
    debounce_seconds: float = 4.0
    db_path: str = "maestro.db"
    report_api_token: str = "maestro_dev_report_token_2026"


settings = Settings()
