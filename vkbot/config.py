from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    api_base_url: str
    hmac_secret: str
    bot_token: str
    redis_storage_url: str = "redis://localhost:6379/1"
    storage_state_ttl: int = Field(default=3600, gt=0)
    auth_nonce_ttl: int = Field(default=300, gt=0)

    bot_mode: str = "polling"
    vk_callback_secret: str = ""
    vk_confirmation_code: str = ""
    callback_host: str = "127.0.0.1"
    callback_port: int = Field(default=8080, ge=1, le=65535)
    callback_path: str = "/vk/callback"
    platform: str = "vk"
    schedule_timezone: str = "Europe/Moscow"
    show_entity_links: bool = True

    base_link: str = Field(alias="base_scraping_url")

    groups_cache_file_path: str = str(BASE_DIR / "cache" / "groups.json")
    teachers_cache_file_path: str = str(BASE_DIR / "cache" / "teachers.json")
    update_keyboards_rule: dict = {
        "trigger": "cron",
        "hour": 3,
        "minute": 0,
        "timezone": "Europe/Moscow",
    }

    log_level: str = "INFO"
    project_name: str = "VKBot"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR.parent / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",  # Игнорировать лишние поля в .env
    )


settings = Settings()
