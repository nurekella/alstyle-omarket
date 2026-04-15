import json
import asyncio
import hashlib
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log,
)

from app.config import get_settings
from app.models import async_session, Product, Category, SyncLog, Setting, PriceAlert
from app.pricing import build_category_markup_map
from app.settings_store import get_setting

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


async def fetch_products_page(client: httpx.AsyncClient, offset: int = 0, modified_since: int | None = None):
    params = {
        "access-token": settings.alstyle_access_token,
        "limit": BATCH_SIZE,
        "offset": offset,
        "exclude_missing": 0,
        "additional_fields": ADDITIONAL_FIELDS,
    }
    if modified_since:
        # Try known parameter names; Al-Style silently ignores unknown params
        params["modified_since"] = modified_since
    return await _get(client, f"{settings.alstyle_api_url}/elements-pagination", params)


def _source_hash(p: dict) -> str:
    """Stable hash of fields we actually care about for change detection."""
    key = {
        "name": p.get("name"),
        "price1": p.get("price1"),
        "price2": p.get("price2"),
        "quantity": p.get("quantity"),
        "category": p.get("category"),
        "brand": p.get("brand"),
        "barcode": p.get("barcode"),
        "images": p.get("images"),
        "description": p.get("description"),
    }
    raw = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        "source_hash": _source_hash(p),
    }


async def upsert_products(products: list[dict], markup: float, anomaly_pct: float, min_dealer_price: float) -> dict:
    if not products:
        return {"written": 0, "skipped_unchanged": 0, "alerts": 0, "frozen": 0}

    articles = [p["article"] for p in products]
    alerts: list[dict] = []
    frozen_count = 0

    async with async_session() as session:
        existing = {
            row[0]: (row[1], row[2], row[3]) for row in (
                await session.execute(
                    select(Product.article, Product.source_hash, Product.price_dealer, Product.price_frozen)
                    .where(Product.article.in_(articles))
                )
            ).fetchall()
        }
        markup_map = await build_category_markup_map(session)

    rows = []
    skipped = 0
    for p in products:
        article = p["article"]
        new_hash = _source_hash(p)
        prev = existing.get(article)
        if prev is not None and prev[0] == new_hash:
            skipped += 1
            continue

        row = _product_row(p, markup, markup_map)

        # Anomaly detection: compare new dealer price to previous
        new_price = row["price_dealer"]
        prev_price = prev[1] if prev else None
        if prev_price and new_price and prev_price > 0:
            pct = (new_price - prev_price) / prev_price * 100
            if abs(pct) >= anomaly_pct:
                alerts.append({
                    "article": article,
                    "old_price": prev_price,
                    "new_price": new_price,
                    "pct_change": round(pct, 2),
                })
                # Freeze: keep previous price_omarket, do not update price
                row["price_frozen"] = True
                row["price_dealer"] = prev_price
                row["price_omarket"] = None  # recompute from frozen prev below if needed
                # Recompute price_omarket from previous price_dealer so we don't break fid
                effective_markup = markup
                cat = row.get("category_id")
                if cat and cat in markup_map:
                    effective_markup = markup_map[cat]
                row["price_omarket"] = round(prev_price * effective_markup)
                frozen_count += 1
            else:
                # Carry over existing frozen flag only if user explicitly frozen
                if prev[2]:
                    row["price_frozen"] = True

        # Min dealer price filter — товар остаётся в БД, но без цены для фида
        if min_dealer_price and row["price_dealer"] and row["price_dealer"] < min_dealer_price:
            row["price_omarket"] = None

        rows.append(row)

    async with async_session() as session:
        for i in range(0, len(rows), DB_CHUNK):
            chunk = rows[i : i + DB_CHUNK]
            stmt = sqlite_insert(Product).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["article"],
                set_={c.name: stmt.excluded[c.name] for c in Product.__table__.columns if c.name != "article"},
            )
            await session.execute(stmt)
        if alerts:
            for a in alerts:
                session.add(PriceAlert(**a))
        await session.commit()

    return {
        "written": len(rows),
        "skipped_unchanged": skipped,
        "alerts": len(alerts),
        "frozen": frozen_count,
    }


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

        anomaly_pct = float(await get_setting("price_anomaly_pct", "50"))
        min_dealer_price = float(await get_setting("min_dealer_price", "0"))

        # Incremental: only pull items modified since last successful sync
        modified_since = None
        last_ok = None
        async with async_session() as session:
            last_ok = (await session.execute(
                select(SyncLog.finished_at)
                .where(SyncLog.status == "success")
                .order_by(SyncLog.id.desc())
                .limit(1)
            )).scalar_one_or_none()
        if last_ok:
            # Send as unix timestamp; Al-Style ignores if unsupported
            modified_since = int(last_ok.timestamp())
            logger.info("Incremental since %s (ts=%d)", last_ok, modified_since)

        total_fetched = 0
        total_written = 0
        total_skipped = 0
        total_alerts = 0
        total_frozen = 0
        offset = 0

        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                logger.info("Загрузка offset=%d ...", offset)
                data = await fetch_products_page(client, offset, modified_since)

                elements = data.get("elements", [])
                pagination = data.get("pagination", {})

                if not elements:
                    break

                stats = await upsert_products(elements, markup, anomaly_pct, min_dealer_price)
                total_fetched += len(elements)
                total_written += stats["written"]
                total_skipped += stats["skipped_unchanged"]
                total_alerts += stats["alerts"]
                total_frozen += stats["frozen"]

                total_pages = pagination.get("totalPages", 1)
                current_page = pagination.get("currentPage", 1)

                logger.info(
                    "  Страница %d/%d — получено %d, обновлено %d, без изменений %d, аномалий %d",
                    current_page, total_pages, len(elements),
                    stats["written"], stats["skipped_unchanged"], stats["alerts"],
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
                    products_updated=total_written,
                )
            )
            await session.commit()

        logger.info(
            "=== Синхронизация завершена: получено %d, записано %d, без изменений %d, аномалий %d, заморожено %d ===",
            total_fetched, total_written, total_skipped, total_alerts, total_frozen,
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
