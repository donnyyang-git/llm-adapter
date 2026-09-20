from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_ADAPTER_")

    host: str = "127.0.0.1"
    port: int = 8000
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222
    chrome_executable: Path | None = None
    chrome_startup_timeout_seconds: float = 15.0
    first_response_timeout_seconds: float = 30.0
    total_response_timeout_seconds: float = 600.0
    response_stable_seconds: float = 2.0
    response_poll_interval_seconds: float = 0.25
    data_dir: Path = PROJECT_ROOT / "data"

    @property
    def chrome_profile_dir(self) -> Path:
        return self.data_dir / "chrome-profile"

    @property
    def conversations_dir(self) -> Path:
        return self.data_dir / "conversations"


settings = Settings()
