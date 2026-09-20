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
    # [修改] 2026-09-20 10:25 原因: 長篇 HTML / 程式碼回覆有時超過 30 秒才出現第一個可擷取內容。 說明: 將首則回覆等待時間放寬到 60 秒，降低把正常長回覆誤判成失敗的機率。
    first_response_timeout_seconds: float = 60.0
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

    @property
    def conversation_artifacts_dir(self) -> Path:
        # [修改] 2026-09-20 18:05 原因: 回覆截圖不應混在 session JSON/Markdown 同層公開。 說明: 將前端要顯示的圖片集中到獨立 artifacts 目錄，便於掛載與管理。
        return self.conversations_dir / "_artifacts"


settings = Settings()
