from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    alstyle_api_url: str = "https://api.al-style.kz/api"
    alstyle_access_token: str = ""
    markup_multiplier: float = 1.20
    sync_interval_minutes: int = 120
    feed_domain: str = "pressplay.kz"

    # OMarket / Kaspi XML
    company_name: str = "MyCompany"
    merchant_id: str = "your-merchant-id"
    store_ids: list[str] = ["main-store"]

    # SQLite — файл внутри /data (volume)
    db_path: str = "/data/omarket.db"

    # Admin auth
    admin_password: str = "changeme"
    secret_key: str = "change-this-secret-key-too"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
