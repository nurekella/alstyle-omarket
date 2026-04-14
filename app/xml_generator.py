import json
import logging
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import select

from app.config import get_settings
from app.models import async_session, Product

logger = logging.getLogger("xml_generator")
settings = get_settings()


async def generate_kaspi_feed() -> str:
    """
    XML-фид в формате Kaspi.kz (совместим с OMarket).
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

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
        result = await session.execute(
            select(Product)
            .where(Product.is_active == True)
            .where(Product.price_omarket.isnot(None))
            .where(Product.price_omarket > 0)
            .order_by(Product.article)
        )
        products = result.scalars().all()

        for p in products:
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
