from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8080, alias="API_PORT")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_alert_chat_id: str = Field(default="", alias="TELEGRAM_ALERT_CHAT_ID")
    telegram_state_path: str = Field(default="data/telegram/state.json", alias="TELEGRAM_STATE_PATH")
    credential_store_path: str = Field(default="data/credentials/exchanges.json", alias="CREDENTIAL_STORE_PATH")
    telegram_admin_chat_ids: str = Field(default="", alias="TELEGRAM_ADMIN_CHAT_IDS")
    telegram_daily_report_hour_utc: int = Field(default=7, alias="TELEGRAM_DAILY_REPORT_HOUR_UTC")

    use_mock_data: bool = Field(default=False, alias="USE_MOCK_DATA")
    request_timeout_sec: float = Field(default=10.0, alias="REQUEST_TIMEOUT_SEC")
    status_cache_ttl_sec: float = Field(default=300.0, alias="STATUS_CACHE_TTL_SEC")
    alert_poll_interval_sec: int = Field(default=300, alias="ALERT_POLL_INTERVAL_SEC")
    alert_cooldown_sec: int = Field(default=300, alias="ALERT_COOLDOWN_SEC")
    heartbeat_interval_sec: int = Field(default=3600, alias="HEARTBEAT_INTERVAL_SEC")

    enabled_exchanges: str = Field(
        default="bitget,bingx,mexc,hyperliquid,extended,okx,kucoin,aden,vest,lighter", alias="ENABLED_EXCHANGES"
    )

    bitget_api_base: str = Field(default="https://api.bitget.com", alias="BITGET_API_BASE")
    bitget_api_key: str = Field(default="", alias="BITGET_API_KEY")
    bitget_api_secret: str = Field(default="", alias="BITGET_API_SECRET")
    bitget_api_passphrase: str = Field(default="", alias="BITGET_API_PASSPHRASE")
    bitget_product_type: str = Field(default="USDT-FUTURES", alias="BITGET_PRODUCT_TYPE")
    bitget_margin_coin: str = Field(default="USDT", alias="BITGET_MARGIN_COIN")

    bingx_api_base: str = Field(default="https://open-api.bingx.com", alias="BINGX_API_BASE")
    bingx_api_key: str = Field(default="", alias="BINGX_API_KEY")
    bingx_api_secret: str = Field(default="", alias="BINGX_API_SECRET")

    okx_api_base: str = Field(default="https://www.okx.com", alias="OKX_API_BASE")
    okx_api_key: str = Field(default="", alias="OKX_API_KEY")
    okx_api_secret: str = Field(default="", alias="OKX_API_SECRET")
    okx_api_passphrase: str = Field(default="", alias="OKX_API_PASSPHRASE")

    hyperliquid_api_base: str = Field(default="https://api.hyperliquid.xyz", alias="HYPERLIQUID_API_BASE")
    hyperliquid_user_address: str = Field(default="", alias="HYPERLIQUID_USER_ADDRESS")
    hyperliquid_dex: str = Field(default="", alias="HYPERLIQUID_DEX")
    hyperliquid_private_key: str = Field(default="", alias="HYPERLIQUID_PRIVATE_KEY")
    hyperliquid_read_only: bool = Field(default=True, alias="HYPERLIQUID_READ_ONLY")

    extended_api_base: str = Field(default="https://api.starknet.extended.exchange", alias="EXTENDED_API_BASE")
    extended_api_key: str = Field(default="", alias="EXTENDED_API_KEY")
    extended_stark_key_public: str = Field(default="", alias="EXTENDED_STARK_KEY_PUBLIC")
    extended_stark_key_private: str = Field(default="", alias="EXTENDED_STARK_KEY_PRIVATE")
    extended_vault_number: str = Field(default="", alias="EXTENDED_VAULT_NUMBER")
    extended_client_id: str = Field(default="", alias="EXTENDED_CLIENT_ID")
    extended_read_only: bool = Field(default=True, alias="EXTENDED_READ_ONLY")

    kucoin_api_base: str = Field(default="https://api-futures.kucoin.com", alias="KUCOIN_API_BASE")
    kucoin_api_key: str = Field(default="", alias="KUCOIN_API_KEY")
    kucoin_api_secret: str = Field(default="", alias="KUCOIN_API_SECRET")
    kucoin_api_passphrase: str = Field(default="", alias="KUCOIN_API_PASSPHRASE")

    aden_api_base: str = Field(default="https://api.aden.io", alias="ADEN_API_BASE")
    aden_api_prefix: str = Field(default="/api/v1", alias="ADEN_API_PREFIX")
    aden_api_key: str = Field(default="", alias="ADEN_API_KEY")
    aden_api_secret: str = Field(default="", alias="ADEN_API_SECRET")

    vest_api_base: str = Field(default="https://server-prod.hz.vestmarkets.com/v2", alias="VEST_API_BASE")
    vest_api_key: str = Field(default="", alias="VEST_API_KEY")
    vest_account_group: str = Field(default="", alias="VEST_ACCOUNT_GROUP")

    lighter_api_base: str = Field(default="https://mainnet.zklighter.elliot.ai", alias="LIGHTER_API_BASE")
    lighter_account_index: str = Field(default="", alias="LIGHTER_ACCOUNT_INDEX")
    lighter_l1_address: str = Field(default="", alias="LIGHTER_L1_ADDRESS")

    max_margin_ratio: float = Field(default=0.75, alias="MAX_MARGIN_RATIO")
    min_liq_distance_pct: float = Field(default=12.0, alias="MIN_LIQ_DISTANCE_PCT")
    max_abs_net_delta_usd: float = Field(default=500.0, alias="MAX_ABS_NET_DELTA_USD")
    history_storage_dir: str = Field(default="data/portfolio-history", alias="HISTORY_STORAGE_DIR")
    history_snapshot_interval_hours: int = Field(default=4, alias="HISTORY_SNAPSHOT_INTERVAL_HOURS")
    history_retention_days: int = Field(default=30, alias="HISTORY_RETENTION_DAYS")

    @property
    def exchanges(self) -> list[str]:
        return [x.strip().lower() for x in self.enabled_exchanges.split(",") if x.strip()]

    @property
    def telegram_admin_chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.telegram_admin_chat_ids.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
