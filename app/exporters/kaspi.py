import logging
import time
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import select

from app.config import get_settings
from app.exporters.registry import get_feed_meta
from app.feeds_config import get_feed_config, is_feed_configured
from app.models import async_session, Product, Category, Blacklist

logger = logging.getLogger("xml_generator")
settings = get_settings()

# Per-feed in-memory cache.
_caches: dict[str, dict] = {}


def _cache_for(feed_id: str) -> dict:
    return _caches.setdefault(feed_id, {"xml": None, "time": 0.0, "offers_count": 0})


async def get_cached_feed(feed_id: str = "omarket") -> str:
    cache = _cache_for(feed_id)
    now = time.time()
    if cache["xml"] and (now - cache["time"]) < settings.xml_cache_ttl:
        return cache["xml"]
    xml, count = await generate_feed_with_count(feed_id)
    cache["xml"] = xml
    cache["time"] = now
    cache["offers_count"] = count
    return xml


def invalidate_cache(feed_id: str | None = None) -> None:
    if feed_id is None:
        _caches.clear()
        return
    if feed_id in _caches:
        _caches[feed_id] = {"xml": None, "time": 0.0, "offers_count": 0}


def cache_info(feed_id: str = "omarket") -> dict:
    cache = _cache_for(feed_id)
    return {
        "cached": cache["xml"] is not None,
        "age_seconds": int(time.time() - cache["time"]) if cache["xml"] else None,
        "size_bytes": len(cache["xml"].encode("utf-8")) if cache["xml"] else 0,
        "offers_count": cache["offers_count"],
        "ttl_seconds": settings.xml_cache_ttl,
    }


def _apply_commission(price: float, commission_pct: float) -> int:
    if commission_pct <= 0:
        return int(round(price))
    return int(round(price / (1 - commission_pct / 100)))


async def generate_feed_with_count(feed_id: str = "omarket") -> tuple[str, int]:
    cfg = await get_feed_config(feed_id)
    meta = await get_feed_meta(feed_id) or {}
    strict = bool(meta.get("strict_xsd"))

    # If feed isn't configured, return a minimal "not configured" document
    # so an accidental external request doesn't get a crash or stale feed.
    if not is_feed_configured(cfg):
        stub = Element("kaspi_catalog", {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")})
        SubElement(stub, "company").text = cfg.get("company_name") or "PressPlay"
        SubElement(stub, "offers")
        xml = '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(stub, encoding="unicode")
        xml += "\n<!-- feed not configured: set merchant_id and store_ids -->"
        return xml, 0

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    root = Element("kaspi_catalog", {
        "date": now_str,
        "xmlns": "kaspiShopping",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "kaspiShopping http://kaspi.kz/kaspishopping.xsd",
    })

    SubElement(root, "company").text = cfg["company_name"] or settings.company_name
    SubElement(root, "merchantid").text = cfg["merchant_id"]

    offers_el = SubElement(root, "offers")
    offers_count = 0

    min_price = cfg["min_price"]
    commission = cfg["commission_pct"]
    store_ids: list[str] = cfg["store_ids"]

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

            brand = (p.brand or "").strip()
            if brand and brand.lower() not in {"no name", "noname", "no brand", "unknown", "-"}:
                SubElement(offer, "brand").text = brand

            # productCode / barcode are NOT in strict Kaspi XSD — include
            # them only for non-strict feeds (OMarket accepts them and
            # uses them for auto-matching to catalog).
            if not strict and p.article_pn:
                code = str(p.article_pn).strip()
                if code:
                    SubElement(offer, "productCode").text = code

            availabilities = SubElement(offer, "availabilities")
            qty = _parse_quantity(p.quantity)
            for store_id in store_ids:
                SubElement(availabilities, "availability", {
                    "available": "yes" if qty > 0 else "no",
                    "storeId": store_id,
                    "stockCount": str(qty),
                })

            final_price = _apply_commission(p.price_omarket, commission)
            SubElement(offer, "price").text = str(final_price)

            if not strict and p.barcode:
                SubElement(offer, "barcode").text = str(p.barcode).strip()

            offers_count += 1

    xml_str = tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_str, offers_count


# Back-compat helpers
async def generate_kaspi_feed(feed_id: str = "omarket") -> str:
    xml, _ = await generate_feed_with_count(feed_id)
    return xml


async def generate_kaspi_feed_with_count(feed_id: str = "omarket") -> tuple[str, int]:
    return await generate_feed_with_count(feed_id)


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
