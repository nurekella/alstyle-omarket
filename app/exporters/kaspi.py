import logging
import time
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import select

from app.config import get_settings
from app.models import async_session, Product, Category, Blacklist
from app.settings_store import get_setting

logger = logging.getLogger("xml_generator")
settings = get_settings()

_cache = {"xml": None, "time": 0.0, "offers_count": 0}


async def get_cached_feed() -> str:
    now = time.time()
    if _cache["xml"] and (now - _cache["time"]) < settings.xml_cache_ttl:
        return _cache["xml"]
    xml, count = await generate_kaspi_feed_with_count()
    _cache["xml"] = xml
    _cache["time"] = now
    _cache["offers_count"] = count
    return xml


def invalidate_cache() -> None:
    _cache["xml"] = None
    _cache["time"] = 0.0
    _cache["offers_count"] = 0


def cache_info() -> dict:
    return {
        "cached": _cache["xml"] is not None,
        "age_seconds": int(time.time() - _cache["time"]) if _cache["xml"] else None,
        "size_bytes": len(_cache["xml"].encode("utf-8")) if _cache["xml"] else 0,
        "offers_count": _cache["offers_count"],
        "ttl_seconds": settings.xml_cache_ttl,
    }


async def _get_min_price() -> float:
    raw = await get_setting("min_price", "0")
    try:
        return float(raw)
    except ValueError:
        return 0.0


async def _get_commission() -> float:
    """OMarket commission in %, 0..50. Raised price = base / (1 - c/100)."""
    raw = await get_setting("commission_omarket", "0")
    try:
        val = float(raw)
    except ValueError:
        return 0.0
    return max(0.0, min(50.0, val))


def _apply_commission(price: float, commission_pct: float) -> int:
    if commission_pct <= 0:
        return int(round(price))
    return int(round(price / (1 - commission_pct / 100)))


async def generate_kaspi_feed() -> str:
    xml, _ = await generate_kaspi_feed_with_count()
    return xml


async def generate_kaspi_feed_with_count() -> tuple[str, int]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    root = Element("kaspi_catalog", {
        "date": now,
        "xmlns": "kaspiShopping",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "kaspiShopping http://kaspi.kz/kaspishopping.xsd",
    })

    SubElement(root, "company").text = settings.company_name
    SubElement(root, "merchantid").text = settings.merchant_id

    offers_el = SubElement(root, "offers")
    offers_count = 0

    min_price = await _get_min_price()
    commission = await _get_commission()

    async with async_session() as session:
        enabled_ids = {
            row[0] for row in (await session.execute(
                select(Category.id).where(Category.sync_enabled == True)
            )).fetchall()
        }
        blacklisted = {
            row[0] for row in (await session.execute(select(Blacklist.article))).fetchall()
        }

        products = (await session.execute(
            select(Product)
            .where(Product.is_active == True)
            .where(Product.price_omarket.isnot(None))
            .where(Product.price_omarket > 0)
            .order_by(Product.article)
        )).scalars().all()

        for p in products:
            if enabled_ids and p.category_id and p.category_id not in enabled_ids:
                continue
            if p.article in blacklisted:
                continue
            if min_price and (p.price_omarket or 0) < min_price:
                continue

            sku = str(p.article)[:20]
            offer = SubElement(offers_el, "offer", sku=sku)
            SubElement(offer, "model").text = p.name
            if p.brand:
                SubElement(offer, "brand").text = p.brand

            availabilities = SubElement(offer, "availabilities")
            qty = _parse_quantity(p.quantity)
            for store_id in settings.store_ids:
                SubElement(availabilities, "availability", {
                    "available": "yes" if qty > 0 else "no",
                    "storeId": store_id,
                    "stockCount": str(qty),
                })

            final_price = _apply_commission(p.price_omarket, commission)
            SubElement(offer, "price").text = str(final_price)

            if p.barcode:
                SubElement(offer, "barcode").text = str(p.barcode).strip()

            offers_count += 1

    xml_str = tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_str, offers_count


def _parse_quantity(quantity) -> int:
    if quantity is None:
        return 0
    q = str(quantity).strip()
    if q.startswith(">"):
        try:
            return int(q[1:])
        except ValueError:
            return 50
    try:
        return max(0, int(q))
    except ValueError:
        return 0
