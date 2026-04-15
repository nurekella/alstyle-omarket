from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.exporters import invalidate_cache
from app.models import Category, Product, SyncLog, async_session
from app.scheduler import scheduler
from app.security import require_auth
from app.settings_store import get_markup, set_setting
from app.suppliers import run_sync

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    markup = await get_markup()
    async with async_session() as session:
        total = (await session.execute(select(func.count(Product.article)))).scalar() or 0
        in_stock = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.quantity != "0")
        )).scalar() or 0
        cats_total = (await session.execute(select(func.count(Category.id)))).scalar() or 0
        cats_enabled = (await session.execute(
            select(func.count(Category.id)).where(Category.sync_enabled == True)
        )).scalar() or 0
        last = (await session.execute(
            select(SyncLog).order_by(desc(SyncLog.id)).limit(1)
        )).scalar_one_or_none()
    return {
        "status": "ok",
        "markup": markup,
        "products_total": total,
        "products_in_stock": in_stock,
        "categories_total": cats_total,
        "categories_enabled": cats_enabled,
        "last_sync": {
            "id": last.id,
            "status": last.status,
            "started_at": str(last.started_at),
            "finished_at": str(last.finished_at) if last.finished_at else None,
            "products_fetched": last.products_fetched,
            "products_updated": last.products_updated,
            "error": last.error_message,
        } if last else None,
    }


class MarkupUpdate(BaseModel):
    percent: float


@router.post("/markup")
async def update_markup(body: MarkupUpdate, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    if body.percent < 0 or body.percent > 500:
        return JSONResponse({"error": "percent must be 0..500"}, status_code=400)
    multiplier = 1 + body.percent / 100
    await set_setting("markup_multiplier", str(round(multiplier, 4)))
    count = 0
    async with async_session() as session:
        for p in (await session.execute(
            select(Product).where(Product.price_dealer.isnot(None))
        )).scalars().all():
            if p.price_dealer and p.price_dealer > 1:
                p.price_omarket = round(p.price_dealer * multiplier)
                count += 1
        await session.commit()
    invalidate_cache()
    return {"markup_percent": body.percent, "recalculated": count}


@router.post("/sync/trigger")
async def trigger_sync(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    scheduler.add_job(run_sync, id="manual_sync", replace_existing=True)
    return {"message": "Синхронизация запущена"}


@router.get("/categories")
async def list_categories(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        cats = (await session.execute(
            select(Category).order_by(Category.left_key)
        )).scalars().all()
        counts_q = await session.execute(
            select(Product.category_id, func.count(Product.article))
            .where(Product.is_active == True)
            .group_by(Product.category_id)
        )
        counts = dict(counts_q.fetchall())
    return [
        {
            "id": c.id, "name": c.name, "parent_id": c.parent_id, "level": c.level,
            "elements": c.elements_count, "products_count": counts.get(c.id, 0),
            "sync_enabled": c.sync_enabled,
        }
        for c in cats
    ]


@router.post("/categories/{cat_id}/toggle")
async def toggle_category(cat_id: int, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        cat = (await session.execute(
            select(Category).where(Category.id == cat_id)
        )).scalar_one_or_none()
        if not cat:
            return JSONResponse({"error": "not found"}, 404)
        new_val = not cat.sync_enabled
        cat.sync_enabled = new_val
        children = (await session.execute(
            select(Category)
            .where(Category.left_key > cat.left_key)
            .where(Category.right_key < cat.right_key)
        )).scalars().all()
        for ch in children:
            ch.sync_enabled = new_val
        await session.commit()
    invalidate_cache()
    return {"id": cat_id, "sync_enabled": new_val, "children_updated": len(children)}


@router.get("/products")
async def list_products(
    request: Request,
    search: str = "",
    category: int = 0,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        query = select(Product).where(Product.is_active == True)
        if search:
            query = query.where(Product.name.ilike(f"%{search}%"))
        if category:
            query = query.where(Product.category_id == category)
        total = (await session.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        query = query.order_by(Product.article).limit(limit).offset(offset)
        products = (await session.execute(query)).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "article": p.article, "name": p.name, "brand": p.brand,
                "price_dealer": p.price_dealer, "price_retail": p.price_retail,
                "price_omarket": p.price_omarket, "quantity": p.quantity,
                "category_id": p.category_id,
            }
            for p in products
        ],
    }


@router.get("/sync/logs")
async def sync_logs(request: Request, limit: int = Query(10, le=50)):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        logs = (await session.execute(
            select(SyncLog).order_by(desc(SyncLog.id)).limit(limit)
        )).scalars().all()
    return [
        {
            "id": l.id, "status": l.status,
            "started_at": str(l.started_at),
            "finished_at": str(l.finished_at) if l.finished_at else None,
            "products_fetched": l.products_fetched,
            "products_updated": l.products_updated,
            "error": l.error_message,
        }
        for l in logs
    ]
