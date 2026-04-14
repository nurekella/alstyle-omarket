import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, func, desc, update

from app.config import get_settings
from app.models import init_db, async_session, Product, SyncLog, Setting
from app.fetcher import run_sync
from app.xml_generator import generate_kaspi_feed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")
settings = get_settings()

scheduler = AsyncIOScheduler()


# --- Helpers for runtime settings ---

async def get_setting(key: str, default: str = "") -> str:
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        row = result.scalar_one_or_none()
        return row.value if row else default


async def set_setting(key: str, value: str):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    async with async_session() as session:
        stmt = sqlite_insert(Setting).values(key=key, value=value).on_conflict_do_update(
            index_elements=["key"], set_={"value": value}
        )
        await session.execute(stmt)
        await session.commit()


async def get_markup() -> float:
    val = await get_setting("markup_multiplier", str(settings.markup_multiplier))
    return float(val)


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Инициализация БД...")
    await init_db()

    # Init default settings if not present
    current = await get_setting("markup_multiplier", "")
    if not current:
        await set_setting("markup_multiplier", str(settings.markup_multiplier))

    scheduler.add_job(
        run_sync,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sync_alstyle",
        next_run_time=datetime.utcnow(),
    )
    scheduler.start()
    logger.info("Планировщик: синхронизация каждые %d мин", settings.sync_interval_minutes)

    yield

    scheduler.shutdown()


app = FastAPI(
    title="Al-Style → OMarket Sync",
    version="1.1.0",
    lifespan=lifespan,
)


# ───────── Feed ─────────

@app.get("/omarket-feed.xml", response_class=Response)
async def xml_feed():
    xml_content = await generate_kaspi_feed()
    return Response(
        content=xml_content,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


# ───────── API ─────────

@app.get("/health")
async def health():
    markup = await get_markup()
    async with async_session() as session:
        total = (await session.execute(
            select(func.count(Product.article))
        )).scalar() or 0
        active = (await session.execute(
            select(func.count(Product.article)).where(Product.is_active == True)
        )).scalar() or 0
        in_stock = (await session.execute(
            select(func.count(Product.article))
            .where(Product.is_active == True)
            .where(Product.quantity != "0")
        )).scalar() or 0
        last = (await session.execute(
            select(SyncLog).order_by(desc(SyncLog.id)).limit(1)
        )).scalar_one_or_none()

    return {
        "status": "ok",
        "markup": markup,
        "products_total": total,
        "products_active": active,
        "products_in_stock": in_stock,
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
    percent: float  # e.g. 20 for 20%


@app.post("/api/markup")
async def update_markup(body: MarkupUpdate):
    multiplier = 1 + body.percent / 100
    await set_setting("markup_multiplier", str(round(multiplier, 4)))

    # Recalculate all prices
    count = 0
    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.price_dealer.isnot(None))
        )
        products = result.scalars().all()
        for p in products:
            if p.price_dealer and p.price_dealer > 1:
                p.price_omarket = round(p.price_dealer * multiplier)
                count += 1
        await session.commit()

    logger.info("Наценка изменена на %.1f%%, пересчитано %d товаров", body.percent, count)
    return {"markup_percent": body.percent, "multiplier": multiplier, "recalculated": count}


@app.post("/sync/trigger")
async def trigger_sync():
    scheduler.add_job(run_sync, id="manual_sync", replace_existing=True)
    return {"message": "Синхронизация запущена"}


@app.get("/products")
async def list_products(
    search: str = "",
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    async with async_session() as session:
        query = select(Product).where(Product.is_active == True)
        if search:
            query = query.where(Product.name.ilike(f"%{search}%"))
        query = query.order_by(Product.article).limit(limit).offset(offset)
        result = await session.execute(query)
        products = result.scalars().all()

    return [
        {
            "article": p.article,
            "name": p.name,
            "brand": p.brand,
            "price_dealer": p.price_dealer,
            "price_retail": p.price_retail,
            "price_omarket": p.price_omarket,
            "quantity": p.quantity,
            "is_new": p.is_new,
        }
        for p in products
    ]


@app.get("/products/count")
async def products_count():
    async with async_session() as session:
        total = (await session.execute(select(func.count(Product.article)))).scalar() or 0
    return {"total": total}


@app.get("/sync/logs")
async def sync_logs(limit: int = Query(default=10, le=50)):
    async with async_session() as session:
        result = await session.execute(
            select(SyncLog).order_by(desc(SyncLog.id)).limit(limit)
        )
        logs = result.scalars().all()

    return [
        {
            "id": l.id,
            "status": l.status,
            "started_at": str(l.started_at),
            "finished_at": str(l.finished_at) if l.finished_at else None,
            "products_fetched": l.products_fetched,
            "products_updated": l.products_updated,
            "error": l.error_message,
        }
        for l in logs
    ]


# ───────── Dashboard ─────────

@app.get("/")
async def root():
    return {
        "service": "PressPlay.kz",
        "feed_url": f"https://{settings.feed_domain}/omarket-feed.xml",
        "dashboard": f"https://{settings.feed_domain}/admin",
    }


@app.get("/admin", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Al-Style → OMarket</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f1117;color:#e0e0e0;padding:20px;max-width:1100px;margin:0 auto}
h1{font-size:22px;font-weight:500;margin-bottom:20px;color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.card{background:#1a1d27;border:1px solid #2a2d37;border-radius:12px;padding:16px}
.card h3{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#888;margin-bottom:8px}
.card .val{font-size:28px;font-weight:600;color:#fff}
.card .sub{font-size:12px;color:#888;margin-top:4px}
.status{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500}
.status.success{background:#0f3d0f;color:#4ade80}
.status.error{background:#3d0f0f;color:#f87171}
.status.running{background:#3d3d0f;color:#facc15}
.section{background:#1a1d27;border:1px solid #2a2d37;border-radius:12px;padding:20px;margin-bottom:16px}
.section h2{font-size:16px;font-weight:500;margin-bottom:16px;color:#fff}
.row{display:flex;gap:12px;align-items:end;flex-wrap:wrap}
input[type=number],input[type=text]{background:#0f1117;border:1px solid #2a2d37;border-radius:8px;padding:10px 14px;color:#fff;font-size:16px;width:120px}
input[type=text]{width:280px}
button{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:14px;cursor:pointer;font-weight:500;transition:background .2s}
button:hover{background:#2563eb}
button:disabled{background:#333;cursor:wait}
button.danger{background:#ef4444}
button.danger:hover{background:#dc2626}
button.green{background:#22c55e}
button.green:hover{background:#16a34a}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px}
th{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2d37;color:#888;font-weight:500;font-size:11px;text-transform:uppercase}
td{padding:8px 10px;border-bottom:1px solid #1a1d27}
tr:hover td{background:#1f2230}
.price{font-variant-numeric:tabular-nums}
.text-right{text-align:right}
.text-green{color:#4ade80}
.text-red{color:#f87171}
.text-yellow{color:#facc15}
.text-muted{color:#666}
.mt{margin-top:12px}
.log-err{font-size:11px;color:#f87171;max-width:600px;word-break:break-all}
.toast{position:fixed;top:20px;right:20px;background:#22c55e;color:#fff;padding:12px 20px;border-radius:8px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:99}
.toast.show{opacity:1}
.toast.err{background:#ef4444}
</style>
</head>
<body>

<h1>Al-Style → OMarket Sync</h1>

<div class="grid" id="stats"></div>

<div class="section">
<h2>Наценка</h2>
<div class="row">
<div>
<input type="number" id="markup" min="0" max="200" step="1" value="20">
<span style="color:#888;font-size:14px">%</span>
</div>
<button onclick="setMarkup()">Применить</button>
<span id="markup-info" style="font-size:13px;color:#888"></span>
</div>
</div>

<div class="section">
<h2>Синхронизация</h2>
<div class="row">
<button class="green" onclick="triggerSync()">Запустить синхронизацию</button>
<button onclick="loadAll()">Обновить данные</button>
</div>
<table class="mt" id="logs-table">
<thead><tr><th>#</th><th>Статус</th><th>Начало</th><th>Товаров</th><th>Обновлено</th><th>Ошибка</th></tr></thead>
<tbody id="logs-body"></tbody>
</table>
</div>

<div class="section">
<h2>Товары</h2>
<div class="row">
<input type="text" id="search" placeholder="Поиск по названию..." oninput="debounceSearch()">
<span id="product-count" style="font-size:13px;color:#888"></span>
</div>
<table class="mt">
<thead><tr><th>Артикул</th><th>Название</th><th>Бренд</th><th class="text-right">Дилерская</th><th class="text-right">OMarket</th><th class="text-right">Наценка</th><th class="text-right">Остаток</th></tr></thead>
<tbody id="products-body"></tbody>
</table>
<div class="mt row">
<button onclick="loadProducts(currentOffset-50)" id="prev-btn" disabled>← Назад</button>
<span id="page-info" style="font-size:13px;color:#888"></span>
<button onclick="loadProducts(currentOffset+50)" id="next-btn">Далее →</button>
</div>
</div>

<div id="toast" class="toast"></div>

<script>
let currentOffset=0, totalProducts=0, searchTimer=null;

function toast(msg,ok=true){
  const t=document.getElementById('toast');
  t.textContent=msg;t.className='toast show'+(ok?'':' err');
  setTimeout(()=>t.className='toast',2500);
}

async function api(url,opts){
  const r=await fetch(url,opts);
  return r.json();
}

async function loadStats(){
  const d=await api('/health');
  const m=((d.markup-1)*100).toFixed(0);
  document.getElementById('markup').value=m;
  const ls=d.last_sync||{};
  document.getElementById('stats').innerHTML=`
    <div class="card"><h3>Товаров в БД</h3><div class="val">${d.products_total}</div></div>
    <div class="card"><h3>В наличии</h3><div class="val text-green">${d.products_in_stock}</div></div>
    <div class="card"><h3>Наценка</h3><div class="val">${m}%</div><div class="sub">×${d.markup}</div></div>
    <div class="card"><h3>Последняя синхр.</h3><div class="val"><span class="status ${ls.status||''}">${ls.status||'—'}</span></div><div class="sub">${ls.started_at||'—'}</div></div>
  `;
  totalProducts=d.products_total;
}

async function loadLogs(){
  const logs=await api('/sync/logs?limit=5');
  document.getElementById('logs-body').innerHTML=logs.map(l=>`
    <tr>
      <td>${l.id}</td>
      <td><span class="status ${l.status}">${l.status}</span></td>
      <td>${l.started_at||''}</td>
      <td>${l.products_fetched}</td>
      <td>${l.products_updated}</td>
      <td>${l.error?'<span class="log-err">'+l.error.slice(0,120)+'</span>':''}</td>
    </tr>`).join('');
}

async function loadProducts(offset=0){
  if(offset<0)offset=0;
  currentOffset=offset;
  const q=document.getElementById('search').value;
  const url='/products?limit=50&offset='+offset+(q?'&search='+encodeURIComponent(q):'');
  const prods=await api(url);
  document.getElementById('products-body').innerHTML=prods.map(p=>{
    const pct=p.price_dealer>0?((p.price_omarket/p.price_dealer-1)*100).toFixed(0):'—';
    const qty=p.quantity;
    const qcls=qty==='0'?'text-red':qty.startsWith('>')?'text-green':'';
    return `<tr>
      <td>${p.article}</td>
      <td>${p.name}</td>
      <td>${p.brand||''}</td>
      <td class="text-right price">${p.price_dealer?p.price_dealer.toLocaleString():''}</td>
      <td class="text-right price">${p.price_omarket?p.price_omarket.toLocaleString():''}</td>
      <td class="text-right">${pct}%</td>
      <td class="text-right ${qcls}">${qty}</td>
    </tr>`;
  }).join('');
  document.getElementById('prev-btn').disabled=offset===0;
  document.getElementById('next-btn').disabled=prods.length<50;
  document.getElementById('page-info').textContent=`${offset+1}–${offset+prods.length} из ${totalProducts}`;
}

async function setMarkup(){
  const pct=parseFloat(document.getElementById('markup').value);
  if(isNaN(pct)||pct<0){toast('Введите число',false);return;}
  const d=await api('/api/markup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({percent:pct})});
  toast('Наценка '+pct+'% — пересчитано '+d.recalculated+' товаров');
  loadAll();
}

async function triggerSync(){
  await api('/sync/trigger',{method:'POST'});
  toast('Синхронизация запущена');
  setTimeout(loadAll,3000);
}

function debounceSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadProducts(0),400);}

function loadAll(){loadStats();loadLogs();loadProducts(currentOffset);}

loadAll();
setInterval(()=>{loadStats();loadLogs();},30000);
</script>
</body>
</html>"""
