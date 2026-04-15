from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.config import get_settings
from app.brands import extract_brand
from app.exporters import cache_info, invalidate_cache
from app.exporters.kaspi import generate_kaspi_feed_with_count
from app.exporters.registry import FEEDS, FEEDS_BY_ID
from app.exporters.xlsx import build_products_xlsx
from app.models import Blacklist, Category, PriceAlert, Product, SyncLog, async_session
from app.pricing import build_category_markup_map
from app.scheduler import scheduler
from app.security import require_auth
from app.settings_store import get_markup, get_setting, set_setting
from app.suppliers import run_sync
from app.suppliers.registry import SUPPLIERS, SUPPLIERS_BY_ID

router = APIRouter(prefix="/api")
settings = get_settings()


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
        blacklist_count = (await session.execute(
            select(func.count(Blacklist.article))
        )).scalar() or 0
        open_alerts = (await session.execute(
            select(func.count(PriceAlert.id)).where(PriceAlert.resolved == False)
        )).scalar() or 0
        frozen = (await session.execute(
            select(func.count(Product.article)).where(Product.price_frozen == True)
        )).scalar() or 0
        with_brand = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.brand.isnot(None))
            .where(Product.brand != "")
        )).scalar() or 0
        with_pn = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.article_pn.isnot(None))
            .where(Product.article_pn != "")
        )).scalar() or 0
        with_barcode = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.barcode.isnot(None))
            .where(Product.barcode != "")
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
        "blacklist_count": blacklist_count,
        "open_alerts": open_alerts,
        "frozen_products": frozen,
        "min_price": float(await get_setting("min_price", "0") or 0),
        "min_dealer_price": float(await get_setting("min_dealer_price", "0") or 0),
        "price_anomaly_pct": float(await get_setting("price_anomaly_pct", "50") or 50),
        "commission_omarket": float(await get_setting("commission_omarket", "0") or 0),
        "matching_coverage": {
            "with_brand": with_brand,
            "with_product_code": with_pn,
            "with_barcode": with_barcode,
            "brand_pct": round(with_brand / in_stock * 100, 1) if in_stock else 0,
            "barcode_pct": round(with_barcode / in_stock * 100, 1) if in_stock else 0,
        },
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
    count = await _recalc_all_prices()
    invalidate_cache()
    return {"markup_percent": body.percent, "recalculated": count}


class CategoryMarkupUpdate(BaseModel):
    percent: float | None = None


@router.post("/categories/{cat_id}/markup")
async def set_category_markup(cat_id: int, body: CategoryMarkupUpdate, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        cat = (await session.execute(
            select(Category).where(Category.id == cat_id)
        )).scalar_one_or_none()
        if not cat:
            return JSONResponse({"error": "not found"}, 404)
        if body.percent is None:
            cat.markup_multiplier = None
        else:
            if body.percent < 0 or body.percent > 500:
                return JSONResponse({"error": "percent must be 0..500"}, status_code=400)
            cat.markup_multiplier = 1 + body.percent / 100
        await session.commit()
    count = await _recalc_all_prices()
    invalidate_cache()
    return {"id": cat_id, "percent": body.percent, "recalculated": count}


async def _recalc_all_prices() -> int:
    async with async_session() as session:
        markup_map = await build_category_markup_map(session)
        global_markup = await get_markup()
        products = (await session.execute(
            select(Product).where(Product.price_dealer.isnot(None))
        )).scalars().all()
        count = 0
        for p in products:
            if not p.price_dealer or p.price_dealer <= 1:
                continue
            m = markup_map.get(p.category_id, global_markup) if p.category_id else global_markup
            p.price_omarket = round(p.price_dealer * m)
            count += 1
        await session.commit()
    return count


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
            "markup_percent": round((c.markup_multiplier - 1) * 100, 1) if c.markup_multiplier else None,
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
        blacklisted = {
            r[0] for r in (await session.execute(select(Blacklist.article))).fetchall()
        }
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
                "blacklisted": p.article in blacklisted,
                "frozen": bool(p.price_frozen),
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


# ───── Suppliers ─────

@router.get("/suppliers")
async def list_suppliers(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        total = (await session.execute(select(func.count(Product.article)))).scalar() or 0
        in_stock = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.quantity != "0")
        )).scalar() or 0
        last = (await session.execute(
            select(SyncLog).order_by(desc(SyncLog.id)).limit(1)
        )).scalar_one_or_none()

    out = []
    for s in SUPPLIERS:
        item = {
            "id": s["id"],
            "name": s["name"],
            "url": s["url"],
            "enabled": s["enabled"],
            "products_total": 0,
            "products_in_stock": 0,
            "last_sync_status": None,
            "last_sync_at": None,
            "sync_interval_minutes": None,
        }
        if s["id"] == "alstyle" and s["enabled"]:
            item.update({
                "products_total": total,
                "products_in_stock": in_stock,
                "last_sync_status": last.status if last else None,
                "last_sync_at": str(last.started_at) if last else None,
                "sync_interval_minutes": settings.sync_interval_minutes,
            })
        out.append(item)
    return out


@router.get("/suppliers/{supplier_id}")
async def get_supplier(supplier_id: str, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    s = SUPPLIERS_BY_ID.get(supplier_id)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return s


# ───── Feeds ─────

@router.get("/feeds")
async def list_feeds(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    info = cache_info()
    out = []
    for f in FEEDS:
        item = {
            "id": f["id"],
            "name": f["name"],
            "format": f["format"],
            "url_path": f["url_path"],
            "absolute_url": f"https://{settings.feed_domain}{f['url_path']}" if f["url_path"] else None,
            "enabled": f["enabled"],
            "target": f.get("target"),
            "site": f.get("site"),
            "store_ids": settings.store_ids if f["enabled"] else [],
            "cached": False,
            "age_seconds": None,
            "size_bytes": 0,
            "offers_count": 0,
            "ttl_seconds": settings.xml_cache_ttl,
        }
        if f["id"] == "omarket" and f["enabled"]:
            item.update(info)
        out.append(item)
    return out


@router.post("/feeds/{feed_id}/refresh")
async def refresh_feed(feed_id: str, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    if feed_id != "omarket":
        return JSONResponse({"error": "unknown feed"}, status_code=404)
    invalidate_cache()
    _, count = await generate_kaspi_feed_with_count()
    return {"ok": True, "offers": count, "refreshed_at": datetime.now(timezone.utc).isoformat()}


@router.get("/feeds/{feed_id}/preview")
async def preview_feed(feed_id: str, request: Request, limit: int = Query(30, le=200)):
    denied = require_auth(request)
    if denied:
        return denied
    if feed_id != "omarket":
        return JSONResponse({"error": "unknown feed"}, status_code=404)
    xml, total = await generate_kaspi_feed_with_count()
    lines = xml.splitlines()
    cutoff = 0
    offer_count = 0
    for i, line in enumerate(lines):
        if "</offer>" in line:
            offer_count += 1
            if offer_count >= limit:
                cutoff = i + 1
                break
    if cutoff == 0:
        cutoff = len(lines)
    preview = "\n".join(lines[:cutoff])
    if cutoff < len(lines):
        preview += f"\n  <!-- ...ещё {total - offer_count} offers... -->\n</offers>\n</kaspi_catalog>"
    return {"preview": preview, "total_offers": total, "shown_offers": offer_count}


# ───── Blacklist ─────

class BlacklistAdd(BaseModel):
    article: int
    reason: str | None = None


@router.get("/blacklist")
async def list_blacklist(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        rows = (await session.execute(
            select(Blacklist, Product.name)
            .join(Product, Product.article == Blacklist.article, isouter=True)
            .order_by(desc(Blacklist.added_at))
        )).all()
    return [
        {
            "article": b.article,
            "name": name or "",
            "reason": b.reason or "",
            "added_at": str(b.added_at),
        }
        for b, name in rows
    ]


@router.post("/blacklist")
async def add_blacklist(body: BlacklistAdd, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        existing = (await session.execute(
            select(Blacklist).where(Blacklist.article == body.article)
        )).scalar_one_or_none()
        if existing:
            existing.reason = body.reason
        else:
            session.add(Blacklist(article=body.article, reason=body.reason))
        await session.commit()
    invalidate_cache()
    return {"ok": True, "article": body.article}


@router.delete("/blacklist/{article}")
async def remove_blacklist(article: int, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        row = (await session.execute(
            select(Blacklist).where(Blacklist.article == article)
        )).scalar_one_or_none()
        if row:
            await session.delete(row)
            await session.commit()
    invalidate_cache()
    return {"ok": True, "article": article}


# ───── Settings ─────

class ValueUpdate(BaseModel):
    value: float


SETTING_BOUNDS = {
    "min_price": (0.0, 1_000_000.0),
    "min_dealer_price": (0.0, 1_000_000.0),
    "price_anomaly_pct": (0.0, 100.0),
    "commission_omarket": (0.0, 50.0),
}


@router.get("/settings/{key}")
async def get_any_setting(key: str, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    if key not in SETTING_BOUNDS:
        return JSONResponse({"error": "unknown key"}, status_code=404)
    val = await get_setting(key, "0")
    try:
        return {"key": key, "value": float(val)}
    except ValueError:
        return {"key": key, "value": 0.0}


@router.post("/settings/{key}")
async def set_any_setting(key: str, body: ValueUpdate, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    if key not in SETTING_BOUNDS:
        return JSONResponse({"error": "unknown key"}, status_code=404)
    lo, hi = SETTING_BOUNDS[key]
    if not (lo <= body.value <= hi):
        return JSONResponse({"error": f"value must be {lo}..{hi}"}, status_code=400)
    await set_setting(key, str(body.value))
    invalidate_cache()
    return {"ok": True, "key": key, "value": body.value}


# ───── Price alerts ─────

class ResolveAlert(BaseModel):
    action: str  # "unfreeze" | "ignore"


@router.get("/alerts")
async def list_alerts(request: Request, only_open: bool = True, limit: int = Query(50, le=200)):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        q = select(PriceAlert, Product.name).join(
            Product, Product.article == PriceAlert.article, isouter=True,
        ).order_by(desc(PriceAlert.id)).limit(limit)
        if only_open:
            q = q.where(PriceAlert.resolved == False)
        rows = (await session.execute(q)).all()
    return [
        {
            "id": a.id,
            "article": a.article,
            "name": name or "",
            "old_price": a.old_price,
            "new_price": a.new_price,
            "pct_change": a.pct_change,
            "detected_at": str(a.detected_at),
            "resolved": a.resolved,
        }
        for a, name in rows
    ]


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, body: ResolveAlert, request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    async with async_session() as session:
        alert = (await session.execute(
            select(PriceAlert).where(PriceAlert.id == alert_id)
        )).scalar_one_or_none()
        if not alert:
            return JSONResponse({"error": "not found"}, status_code=404)
        alert.resolved = True
        if body.action == "unfreeze":
            product = (await session.execute(
                select(Product).where(Product.article == alert.article)
            )).scalar_one_or_none()
            if product:
                product.price_frozen = False
                if alert.new_price:
                    product.price_dealer = alert.new_price
                    markup_map = await build_category_markup_map(session)
                    global_markup = await get_markup()
                    m = (markup_map.get(product.category_id, global_markup)
                         if product.category_id else global_markup)
                    product.price_omarket = round(alert.new_price * m)
        await session.commit()
    invalidate_cache()
    return {"ok": True, "action": body.action}


# ───── Tools ─────

@router.post("/tools/rebuild-brands")
async def rebuild_brands(request: Request):
    """
    Re-run brand extraction on all products that have an empty/placeholder
    brand. Does not touch Al-Style; uses name/full_name from DB.
    """
    denied = require_auth(request)
    if denied:
        return denied
    placeholders = {"", "no name", "noname", "no brand", "unknown", "-"}
    updated = 0
    scanned = 0
    async with async_session() as session:
        products = (await session.execute(select(Product))).scalars().all()
        for p in products:
            scanned += 1
            current = (p.brand or "").strip().lower()
            if current and current not in placeholders:
                continue
            candidate = extract_brand(p.name or p.full_name or "")
            if candidate:
                p.brand = candidate
                updated += 1
        await session.commit()
    invalidate_cache()
    return {"ok": True, "scanned": scanned, "updated": updated}


# ───── Excel export ─────

@router.get("/export/xlsx")
async def export_xlsx(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    data = await build_products_xlsx()
    filename = f"pressplay-prices-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
