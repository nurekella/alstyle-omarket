import json
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log,
)

from app.config import get_settings
from app.models import async_session, Product, Category, SyncLog, Setting
from app.pricing import build_category_markup_map

logger = logging.getLogger("fetcher")
settings = get_settings()

BATCH_SIZE = 250
ADDITIONAL_FIELDS = "description,brand,images,barcode,warranty,weight"
DB_CHUNK = 500


_retry_http = retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TransportError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


@_retry_http
async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    resp = await client.get(url, params=params)
    if resp.status_code in (429, 502, 503, 504):
        raise httpx.HTTPError(f"retryable status {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


async def fetch_categories():
    async with httpx.AsyncClient(timeout=30) as client:
        categories = await _get(
            client,
            f"{settings.alstyle_api_url}/categories",
            {"access-token": settings.alstyle_access_token},
        )

    categories.sort(key=lambda c: c.get("left", 0))

    stack = []
    parent_map = {}
    for cat in categories:
        left = cat.get("left", 0)
        right = cat.get("right", 0)
        while stack and stack[-1][1] < left:
            stack.pop()
        parent_map[cat["id"]] = stack[-1][0] if stack else None
        stack.append((cat["id"], right))

    rows = [
        {
            "id": c["id"],
            "name": c["name"],
            "level": c.get("level", 1),
            "left_key": c.get("left", 0),
            "right_key": c.get("right", 0),
            "elements_count": c.get("elements", 0),
            "parent_id": parent_map.get(c["id"]),
        }
        for c in categories
    ]

    async with async_session() as session:
        for i in range(0, len(rows), DB_CHUNK):
            chunk = rows[i : i + DB_CHUNK]
            stmt = sqlite_insert(Category).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "level": stmt.excluded.level,
                    "left_key": stmt.excluded.left_key,
                    "right_key": stmt.excluded.right_key,
                    "elements_count": stmt.excluded.elements_count,
                    "parent_id": stmt.excluded.parent_id,
                },
            )
            await session.execute(stmt)
        await session.commit()

    logger.info("Синхронизировано %d категорий", len(categories))
    return len(categories)


async def fetch_products_page(client: httpx.AsyncClient, offset: int = 0):
    return await _get(
        client,
        f"{settings.alstyle_api_url}/elements-pagination",
        {
            "access-token": settings.alstyle_access_token,
            "limit": BATCH_SIZE,
            "offset": offset,
            "exclude_missing": 0,
            "additional_fields": ADDITIONAL_FIELDS,
        },
    )


def _product_row(p: dict, markup: float, markup_map: dict[int, float] | None = None) -> dict:
    price_dealer = p.get("price1")
    category_id = p.get("category")
    effective_markup = markup
    if markup_map and category_id in markup_map:
        effective_markup = markup_map[category_id]
    price_omarket = round(price_dealer * effective_markup) if price_dealer and price_dealer > 1 else None

    images_json = None
    if p.get("images"):
        images_json = (
            json.dumps(p["images"]) if isinstance(p["images"], list) else str(p["images"])
        )

    return {
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


async def upsert_products(products: list[dict], markup: float) -> int:
    if not products:
        return 0

    async with async_session() as session:
        markup_map = await build_category_markup_map(session)

    rows = [_product_row(p, markup, markup_map) for p in products]

    async with async_session() as session:
        for i in range(0, len(rows), DB_CHUNK):
            chunk = rows[i : i + DB_CHUNK]
            stmt = sqlite_insert(Product).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["article"],
                set_={c.name: stmt.excluded[c.name] for c in Product.__table__.columns if c.name != "article"},
            )
            await session.execute(stmt)
        await session.commit()

    return len(rows)


async def run_sync():
    logger.info("=== Начало синхронизации ===")

    async with async_session() as session:
        log = SyncLog(status="running")
        session.add(log)
        await session.commit()
        log_id = log.id

    try:
        await fetch_categories()
        await asyncio.sleep(6)

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
                    finished_at=datetime.now(timezone.utc),
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
                    finished_at=datetime.now(timezone.utc),
                    error_message=str(e),
                )
            )
            await session.commit()
        raise
