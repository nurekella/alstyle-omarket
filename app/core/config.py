import logging
from functools import lru_cache

from pydantic_settings import BaseSettings

logger = logging.getLogger("config")

_INSECURE_SECRETS = {
    "change-this-secret-key-too",
    "change-me-openssl-rand-hex-32",
    "random-secret-key-here",
    "",
}
_INSECURE_PASSWORDS = {"changeme", "change-me-to-strong-password", ""}


class Settings(BaseSettings):
    alstyle_api_url: str = "https://api.al-style.kz/api"
    alstyle_access_token: str = ""
    markup_multiplier: float = 1.20
    sync_interval_minutes: int = 120
    feed_domain: str = "pressplay.kz"

    company_name: str = "MyCompany"
    merchant_id: str = "your-merchant-id"
    store_ids: list[str] = ["main-store"]

    db_path: str = "/data/omarket.db"

    admin_password: str = ""
    secret_key: str = ""

    xml_cache_ttl: int = 600

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.secret_key in _INSECURE_SECRETS:
        logger.warning(
            "SECRET_KEY is empty or default — sessions are predictable. "
            "Set a strong one: `openssl rand -hex 32`."
        )
    if s.admin_password in _INSECURE_PASSWORDS:
        logger.warning("ADMIN_PASSWORD is empty or default — login is effectively open.")
    return s
