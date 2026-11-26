from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "运营商报备自动化助手"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    browser_headless: bool = False
    browser_timeout_ms: int = 20_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
