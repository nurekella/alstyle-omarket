import json
import asyncio
import logging
from datetime import datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings
from app.models import async_session, Product, Category, SyncLog, Setting

logger = logging.getLogger("fetcher")
settings = get_settings()

BATCH_SIZE = 250
ADDITIONAL_FIELDS = "description,brand,images,barcode,warranty,weight"


async def fetch_categories():
    """Загрузить категории из Al-Style."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.alstyle_api_url}/categories",
            params={"access-token": settings.alstyle_access_token},
        )
        resp.raise_for_status()
        categories = resp.json()

    async with async_session() as session:
        for cat in categories:
            stmt = sqlite_insert(Category).values(
                id=cat["id"],
                name=cat["name"],
                level=cat.get("level", 1),
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={"name": cat["name"], "level": cat.get("level", 1)},
            )
            await session.execute(stmt)
        await session.commit()

    logger.info("Синхронизировано %d категорий", len(categories))
    return len(categories)


async def fetch_products_page(client: httpx.AsyncClient, offset: int = 0):
    """Получить одну страницу товаров."""
    resp = await client.get(
        f"{settings.alstyle_api_url}/elements-pagination",
        params={
            "access-token": settings.alstyle_access_token,
            "limit": BATCH_SIZE,
            "offset": offset,
            "exclude_missing": 0,
            "additional_fields": ADDITIONAL_FIELDS,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def upsert_products(products: list[dict], markup: float) -> int:
    """Вставить/обновить товары в БД с наценкой."""
    count = 0

    async with async_session() as session:
        for p in products:
            price_dealer = p.get("price1")
            price_omarket = None
            if price_dealer and price_dealer > 1:
                price_omarket = round(price_dealer * markup)

            images_json = None
            if "images" in p and p["images"]:
                images_json = (
                    json.dumps(p["images"])
                    if isinstance(p["images"], list)
                    else str(p["images"])
                )

            values = {
                "article": p["article"],
                "article_pn": p.get("article_pn"),
                "name": p["name"],
                "full_name": p.get("full_name"),
                "description": p.get("description"),
                "category_id": p.get("category"),
                "brand": p.get("brand"),
                "price_dealer": price_dealer,
                "price_retail": p.get("price2"),
                "price_omarket": price_omarket,
                "quantity": str(p.get("quantity", 0)),
                "is_new": bool(p.get("isnew")),
                "barcode": p.get("barcode"),
                "warranty": p.get("warranty"),
                "weight": p.get("weight"),
                "images": images_json,
                "quantity_markdown": p.get("quantityMarkdown", 0),
                "price_markdown": p.get("priceMarkdown"),
                "is_active": True,
            }

            stmt = sqlite_insert(Product).values(**values).on_conflict_do_update(
                index_elements=["article"],
                set_={k: v for k, v in values.items() if k != "article"},
            )
            await session.execute(stmt)
            count += 1

        await session.commit()

    return count


async def run_sync():
    """Полный цикл синхронизации."""
    logger.info("=== Начало синхронизации ===")

    async with async_session() as session:
        log = SyncLog(status="running")
        session.add(log)
        await session.commit()
        log_id = log.id

    try:
        await fetch_categories()
        await asyncio.sleep(6)

        # Read markup from DB settings
        async with async_session() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == "markup_multiplier")
            )
            row = result.scalar_one_or_none()
            markup = float(row.value) if row else settings.markup_multiplier

        total_fetched = 0
        total_updated = 0
        offset = 0

        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                logger.info("Загрузка offset=%d ...", offset)
                data = await fetch_products_page(client, offset)

                elements = data.get("elements", [])
                pagination = data.get("pagination", {})

                if not elements:
                    break

                updated = await upsert_products(elements, markup)
                total_fetched += len(elements)
                total_updated += updated

                total_pages = pagination.get("totalPages", 1)
                current_page = pagination.get("currentPage", 1)

                logger.info(
                    "  Страница %d/%d — получено %d, обновлено %d",
                    current_page, total_pages, len(elements), updated,
                )

                if current_page >= total_pages:
                    break

                await asyncio.sleep(6)
                offset += BATCH_SIZE

        async with async_session() as session:
            await session.execute(
                update(SyncLog)
                .where(SyncLog.id == log_id)
                .values(
                    status="success",
                    finished_at=datetime.utcnow(),
                    products_fetched=total_fetched,
                    products_updated=total_updated,
                )
            )
            await session.commit()

        logger.info(
            "=== Синхронизация завершена: %d получено, %d обновлено ===",
            total_fetched, total_updated,
        )

    except Exception as e:
        logger.exception("Ошибка синхронизации: %s", e)
        async with async_session() as session:
            await session.execute(
                update(SyncLog)
                .where(SyncLog.id == log_id)
                .values(
                    status="error",
                    finished_at=datetime.utcnow(),
                    error_message=str(e),
                )
            )
            await session.commit()
        raise
