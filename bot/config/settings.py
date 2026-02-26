from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    mongo_uri: str = Field(..., alias="MONGO_URI")
    mongo_db_name: str = Field("elitex_protector", alias="MONGO_DB_NAME")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    owner_user_id: int = Field(8088623806, alias="OWNER_USER_ID")
    owner_username: str = Field("EliteSid", alias="OWNER_USERNAME")
    admin_review_chat_id: int = Field(-1003761739308, alias="ADMIN_REVIEW_CHAT_ID")
    payment_qr_image_url: str = Field(
        "https://files.catbox.moe/0svb7x.jpg",
        alias="PAYMENT_QR_IMAGE_URL",
    )

    bot_message_delete_delay_seconds: int = Field(
        35,
        alias="BOT_MESSAGE_DELETE_DELAY_SECONDS",
        ge=1,
    )
    protected_group_cache_refresh_seconds: int = Field(
        20,
        alias="PROTECTED_GROUP_CACHE_REFRESH_SECONDS",
        ge=5,
    )
    auto_delete_tick_interval_ms: int = Field(
        200,
        alias="AUTO_DELETE_TICK_INTERVAL_MS",
        ge=50,
        le=2000,
    )
    auto_delete_chunk_size: int = Field(
        100,
        alias="AUTO_DELETE_CHUNK_SIZE",
        ge=1,
        le=100,
    )
    auto_delete_retry_attempts: int = Field(
        5,
        alias="AUTO_DELETE_RETRY_ATTEMPTS",
        ge=0,
        le=20,
    )
    auto_delete_retry_base_seconds: float = Field(
        1.5,
        alias="AUTO_DELETE_RETRY_BASE_SECONDS",
        ge=0.1,
    )
    auto_delete_retry_max_seconds: float = Field(
        35.0,
        alias="AUTO_DELETE_RETRY_MAX_SECONDS",
        ge=1.0,
    )
    auto_delete_worker_concurrency: int = Field(
        12,
        alias="AUTO_DELETE_WORKER_CONCURRENCY",
        ge=1,
        le=50,
    )
    auto_delete_metrics_log_interval_seconds: int = Field(
        60,
        alias="AUTO_DELETE_METRICS_LOG_INTERVAL_SECONDS",
        ge=10,
    )
    auto_delete_persistence_enabled: bool = Field(
        False,
        alias="AUTO_DELETE_PERSISTENCE_ENABLED",
    )
    auto_delete_persistence_ttl_hours: int = Field(
        24,
        alias="AUTO_DELETE_PERSISTENCE_TTL_HOURS",
        ge=1,
        le=168,
    )
    auto_delete_restore_limit: int = Field(
        20000,
        alias="AUTO_DELETE_RESTORE_LIMIT",
        ge=0,
        le=200000,
    )

    observer_enabled: bool = Field(
        False,
        alias="OBSERVER_ENABLED",
    )
    observer_api_id: int | None = Field(
        None,
        alias="OBSERVER_API_ID",
        ge=1,
    )
    observer_api_hash: SecretStr | None = Field(
        None,
        alias="OBSERVER_API_HASH",
    )
    observer_session_string: SecretStr | None = Field(
        None,
        alias="OBSERVER_SESSION_STRING",
    )
    observer_sync_interval_seconds: int = Field(
        30,
        alias="OBSERVER_SYNC_INTERVAL_SECONDS",
        ge=10,
        le=3600,
    )

    bot_run_mode: Literal["polling", "webhook"] = Field("polling", alias="BOT_RUN_MODE")
    webhook_mode: bool = Field(False, alias="WEBHOOK_MODE")
    webhook_base_url: str = Field("", alias="WEBHOOK_BASE_URL")
    webhook_path: str = Field("/webhook/telegram", alias="WEBHOOK_PATH")
    webhook_secret_token: SecretStr | None = Field(None, alias="WEBHOOK_SECRET_TOKEN")
    web_server_host: str = Field("0.0.0.0", alias="WEB_SERVER_HOST")
    web_server_port: int = Field(8000, alias="PORT", ge=1, le=65535)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def resolved_run_mode(self) -> Literal["polling", "webhook"]:
        if self.webhook_mode:
            return "webhook"
        return self.bot_run_mode

    @property
    def normalized_webhook_path(self) -> str:
        path = self.webhook_path.strip()
        if not path:
            return "/webhook/telegram"
        if not path.startswith("/"):
            return f"/{path}"
        return path

    @property
    def webhook_url(self) -> str | None:
        base_url = self.webhook_base_url.strip().rstrip("/")
        if not base_url:
            return None
        return f"{base_url}{self.normalized_webhook_path}"

    @property
    def webhook_secret_token_value(self) -> str | None:
        if self.webhook_secret_token is None:
            return None

        raw_token = self.webhook_secret_token.get_secret_value().strip()
        if not raw_token:
            return None
        return raw_token

    @property
    def observer_api_hash_value(self) -> str | None:
        if self.observer_api_hash is None:
            return None

        raw_hash = self.observer_api_hash.get_secret_value().strip()
        if not raw_hash:
            return None
        return raw_hash

    @property
    def observer_session_string_value(self) -> str | None:
        if self.observer_session_string is None:
            return None

        raw_session = self.observer_session_string.get_secret_value().strip()
        if not raw_session:
            return None
        return raw_session


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

