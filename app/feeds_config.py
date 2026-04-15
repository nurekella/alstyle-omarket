"""
Per-feed configuration stored in the Setting key-value table.

Keys look like `feed.<id>.<attr>`. Example: `feed.kaspi.merchant_id`.
OMarket falls back to env (.env) for backwards compatibility; for Kaspi
and future feeds everything is DB-driven and editable via UI.
"""
import json
import logging

from app.config import get_settings
from app.settings_store import get_setting, set_setting

logger = logging.getLogger("feeds_config")
_env = get_settings()


FIELDS = ("merchant_id", "company_name", "store_ids", "commission_pct", "min_price")


def _key(feed_id: str, attr: str) -> str:
    return f"feed.{feed_id}.{attr}"


async def get_feed_config(feed_id: str) -> dict:
    """Return effective feed config — DB values with env fallback for omarket."""
    cfg: dict = {
        "merchant_id": "",
        "company_name": "",
        "store_ids": [],
        "commission_pct": 0.0,
        "min_price": 0.0,
    }

    for attr in FIELDS:
        raw = await get_setting(_key(feed_id, attr), "")
        if raw:
            if attr == "store_ids":
                try:
                    cfg["store_ids"] = json.loads(raw)
                    if not isinstance(cfg["store_ids"], list):
                        cfg["store_ids"] = []
                except Exception:
                    cfg["store_ids"] = [s.strip() for s in raw.split(",") if s.strip()]
            elif attr in ("commission_pct", "min_price"):
                try:
                    cfg[attr] = float(raw)
                except ValueError:
                    pass
            else:
                cfg[attr] = raw

    # Env fallback — only for omarket, only when DB is empty.
    if feed_id == "omarket":
        if not cfg["merchant_id"]:
            cfg["merchant_id"] = _env.merchant_id
        if not cfg["company_name"]:
            cfg["company_name"] = _env.company_name
        if not cfg["store_ids"]:
            cfg["store_ids"] = list(_env.store_ids)

    return cfg


async def set_feed_config(feed_id: str, data: dict) -> dict:
    """Persist a partial update. Returns the full effective config after."""
    if "merchant_id" in data:
        await set_setting(_key(feed_id, "merchant_id"), str(data["merchant_id"] or "").strip())
    if "company_name" in data:
        await set_setting(_key(feed_id, "company_name"), str(data["company_name"] or "").strip())
    if "store_ids" in data:
        ids = data["store_ids"] or []
        if isinstance(ids, str):
            ids = [s.strip() for s in ids.split(",") if s.strip()]
        await set_setting(_key(feed_id, "store_ids"), json.dumps(list(ids), ensure_ascii=False))
    if "commission_pct" in data:
        v = float(data["commission_pct"] or 0)
        v = max(0.0, min(50.0, v))
        await set_setting(_key(feed_id, "commission_pct"), str(v))
    if "min_price" in data:
        v = float(data["min_price"] or 0)
        v = max(0.0, v)
        await set_setting(_key(feed_id, "min_price"), str(v))
    return await get_feed_config(feed_id)


def is_feed_configured(cfg: dict) -> bool:
    return bool(cfg.get("merchant_id")) and bool(cfg.get("store_ids"))
