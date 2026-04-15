import logging
import time
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import select

from app.config import get_settings
from app.models import async_session, Product, Category

logger = logging.getLogger("xml_generator")
settings = get_settings()

_cache = {"xml": None, "time": 0.0}


async def get_cached_feed() -> str:
    now = time.time()
    if _cache["xml"] and (now - _cache["time"]) < settings.xml_cache_ttl:
        return _cache["xml"]
    xml = await generate_kaspi_feed()
    _cache["xml"] = xml
    _cache["time"] = now
    return xml


def invalidate_cache() -> None:
    _cache["xml"] = None
    _cache["time"] = 0.0


async def generate_kaspi_feed() -> str:
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

    async with async_session() as session:
        cat_result = await session.execute(
            select(Category.id).where(Category.sync_enabled == True)
        )
        enabled_ids = {row[0] for row in cat_result.fetchall()}

        result = await session.execute(
            select(Product)
            .where(Product.is_active == True)
            .where(Product.price_omarket.isnot(None))
            .where(Product.price_omarket > 0)
            .order_by(Product.article)
        )
        products = result.scalars().all()

        for p in products:
            if enabled_ids and p.category_id and p.category_id not in enabled_ids:
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

            SubElement(offer, "price").text = str(int(p.price_omarket))

    xml_str = tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + xml_str


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
