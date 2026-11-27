import os

from pydantic_settings import BaseSettings, SettingsConfigDict


_default_profile = str(
    __import__("pathlib").Path(
        os.environ.get("LOCALAPPDATA", __import__("pathlib").Path.home())
    ) / "ChromeAutomationTest"
)


class Settings(BaseSettings):
    app_name: str = "运营商报备自动化助手"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    browser_timeout_ms: int = 20_000
    browser_cdp_url: str = "http://127.0.0.1:9222"
    browser_user_data_dir: str = _default_profile

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
