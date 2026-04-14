import hmac
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query, Request
from fastapi.responses import Response, HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, func, desc

from app.config import get_settings
from app.models import init_db, async_session, Product, SyncLog, Setting, Category
from app.fetcher import run_sync
from app.xml_generator import get_cached_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")
settings = get_settings()
scheduler = AsyncIOScheduler()
TOKEN_NAME = "pp_session"


def make_token() -> str:
    return hmac.new(settings.secret_key.encode(), settings.admin_password.encode(), hashlib.sha256).hexdigest()

def check_auth(request: Request) -> bool:
    return request.cookies.get(TOKEN_NAME) == make_token()

def require_auth(request: Request):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)


async def get_setting(key, default=""):
    async with async_session() as session:
        r = await session.execute(select(Setting).where(Setting.key == key))
        row = r.scalar_one_or_none()
        return row.value if row else default

async def set_setting(key, value):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    async with async_session() as session:
        await session.execute(sqlite_insert(Setting).values(key=key, value=value).on_conflict_do_update(index_elements=["key"], set_={"value": value}))
        await session.commit()

async def get_markup():
    return float(await get_setting("markup_multiplier", str(settings.markup_multiplier)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if not await get_setting("markup_multiplier", ""):
        await set_setting("markup_multiplier", str(settings.markup_multiplier))
    scheduler.add_job(run_sync, "interval", minutes=settings.sync_interval_minutes, id="sync_alstyle", next_run_time=datetime.now())
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="PressPlay.kz", version="2.1.0", lifespan=lifespan)


# ───── Public ─────

@app.get("/")
async def root():
    return {"service": "PressPlay.kz", "feed": f"https://{settings.feed_domain}/omarket-feed.xml"}

@app.get("/omarket-feed.xml", response_class=Response)
async def xml_feed():
    return Response(content=await get_cached_feed(), media_type="application/xml; charset=utf-8", headers={"Cache-Control": "public, max-age=600"})


# ───── Auth ─────

class LoginForm(BaseModel):
    password: str

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_auth(request): return RedirectResponse("/admin", 302)
    return LOGIN_HTML

@app.post("/admin/login")
async def login(body: LoginForm):
    if body.password == settings.admin_password:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(TOKEN_NAME, make_token(), httponly=True, secure=True, samesite="strict", max_age=86400*7)
        return resp
    return JSONResponse({"ok": False}, status_code=401)

@app.post("/admin/logout")
async def logout():
    resp = RedirectResponse("/admin/login", 302)
    resp.delete_cookie(TOKEN_NAME)
    return resp

@app.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not check_auth(request): return RedirectResponse("/admin/login", 302)
    return DASHBOARD_HTML


# ───── API ─────

@app.get("/api/health")
async def health(request: Request):
    denied = require_auth(request)
    if denied: return denied
    markup = await get_markup()
    async with async_session() as session:
        total = (await session.execute(select(func.count(Product.article)))).scalar() or 0
        in_stock = (await session.execute(select(func.count(Product.article)).where(Product.is_active==True).where(Product.quantity!="0"))).scalar() or 0
        cats_total = (await session.execute(select(func.count(Category.id)))).scalar() or 0
        cats_enabled = (await session.execute(select(func.count(Category.id)).where(Category.sync_enabled==True))).scalar() or 0
        last = (await session.execute(select(SyncLog).order_by(desc(SyncLog.id)).limit(1))).scalar_one_or_none()
    return {"status":"ok","markup":markup,"products_total":total,"products_in_stock":in_stock,
            "categories_total":cats_total,"categories_enabled":cats_enabled,
            "last_sync":{"id":last.id,"status":last.status,"started_at":str(last.started_at),"finished_at":str(last.finished_at) if last.finished_at else None,"products_fetched":last.products_fetched,"products_updated":last.products_updated,"error":last.error_message} if last else None}


class MarkupUpdate(BaseModel):
    percent: float

@app.post("/api/markup")
async def update_markup(body: MarkupUpdate, request: Request):
    denied = require_auth(request)
    if denied: return denied
    multiplier = 1 + body.percent / 100
    await set_setting("markup_multiplier", str(round(multiplier, 4)))
    count = 0
    async with async_session() as session:
        for p in (await session.execute(select(Product).where(Product.price_dealer.isnot(None)))).scalars().all():
            if p.price_dealer and p.price_dealer > 1:
                p.price_omarket = round(p.price_dealer * multiplier)
                count += 1
        await session.commit()
    return {"markup_percent": body.percent, "recalculated": count}


@app.post("/api/sync/trigger")
async def trigger_sync(request: Request):
    denied = require_auth(request)
    if denied: return denied
    scheduler.add_job(run_sync, id="manual_sync", replace_existing=True)
    return {"message": "Синхронизация запущена"}


@app.get("/api/categories")
async def list_categories(request: Request):
    denied = require_auth(request)
    if denied: return denied
    async with async_session() as session:
        cats = (await session.execute(select(Category).order_by(Category.left_key))).scalars().all()
        # Считаем товары на категорию
        counts_q = await session.execute(
            select(Product.category_id, func.count(Product.article))
            .where(Product.is_active == True)
            .group_by(Product.category_id)
        )
        counts = dict(counts_q.fetchall())
    return [{"id":c.id,"name":c.name,"parent_id":c.parent_id,"level":c.level,
             "elements":c.elements_count,"products_count":counts.get(c.id,0),
             "sync_enabled":c.sync_enabled} for c in cats]


@app.post("/api/categories/{cat_id}/toggle")
async def toggle_category(cat_id: int, request: Request):
    denied = require_auth(request)
    if denied: return denied
    async with async_session() as session:
        cat = (await session.execute(select(Category).where(Category.id==cat_id))).scalar_one_or_none()
        if not cat: return JSONResponse({"error":"not found"}, 404)
        new_val = not cat.sync_enabled
        cat.sync_enabled = new_val
        # Также переключить все дочерние (по nested sets)
        children = (await session.execute(
            select(Category).where(Category.left_key > cat.left_key).where(Category.right_key < cat.right_key)
        )).scalars().all()
        for ch in children:
            ch.sync_enabled = new_val
        await session.commit()
    return {"id": cat_id, "sync_enabled": new_val, "children_updated": len(children)}


@app.get("/api/products")
async def list_products(request: Request, search:str="", category:int=0, limit:int=Query(50,le=200), offset:int=Query(0,ge=0)):
    denied = require_auth(request)
    if denied: return denied
    async with async_session() as session:
        query = select(Product).where(Product.is_active==True)
        if search: query = query.where(Product.name.ilike(f"%{search}%"))
        if category: query = query.where(Product.category_id==category)
        total_q = await session.execute(select(func.count()).select_from(query.subquery()))
        total = total_q.scalar() or 0
        query = query.order_by(Product.article).limit(limit).offset(offset)
        products = (await session.execute(query)).scalars().all()
    return {"total":total,"items":[{"article":p.article,"name":p.name,"brand":p.brand,"price_dealer":p.price_dealer,"price_retail":p.price_retail,"price_omarket":p.price_omarket,"quantity":p.quantity,"category_id":p.category_id} for p in products]}


@app.get("/api/sync/logs")
async def sync_logs(request: Request, limit:int=Query(10,le=50)):
    denied = require_auth(request)
    if denied: return denied
    async with async_session() as session:
        logs = (await session.execute(select(SyncLog).order_by(desc(SyncLog.id)).limit(limit))).scalars().all()
    return [{"id":l.id,"status":l.status,"started_at":str(l.started_at),"finished_at":str(l.finished_at) if l.finished_at else None,"products_fetched":l.products_fetched,"products_updated":l.products_updated,"error":l.error_message} for l in logs]


# ───── HTML ─────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход — PressPlay</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f1117;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#1a1d27;border:1px solid #2a2d37;border-radius:16px;padding:40px;width:360px}
h1{font-size:20px;font-weight:500;margin-bottom:8px;color:#fff}
.sub{font-size:13px;color:#666;margin-bottom:24px}
label{font-size:13px;color:#888;display:block;margin-bottom:6px}
input{width:100%;background:#0f1117;border:1px solid #2a2d37;border-radius:8px;padding:12px 14px;color:#fff;font-size:15px;margin-bottom:16px}
input:focus{outline:none;border-color:#3b82f6}
button{width:100%;background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:12px;font-size:15px;cursor:pointer;font-weight:500}
button:hover{background:#2563eb}
.err{color:#f87171;font-size:13px;margin-bottom:12px;display:none}
</style></head><body>
<div class="box">
<h1>PressPlay.kz</h1>
<p class="sub">Вход в панель управления</p>
<div class="err" id="err">Неверный пароль</div>
<label>Пароль</label>
<input type="password" id="pw" autofocus onkeydown="if(event.key==='Enter')go()">
<button onclick="go()">Войти</button>
</div>
<script>
async function go(){
  var r=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('pw').value})});
  if(r.ok)location.href='/admin';
  else{document.getElementById('err').style.display='block';document.getElementById('pw').value='';document.getElementById('pw').focus()}
}
</script></body></html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PressPlay — Панель</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0f1117;color:#e0e0e0}
.wrap{display:flex;min-height:100vh}
.sidebar{width:300px;background:#151820;border-right:1px solid #2a2d37;padding:16px;overflow-y:auto;flex-shrink:0}
.main{flex:1;padding:20px;overflow-y:auto;max-width:900px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}
h1{font-size:20px;font-weight:500;color:#fff}
.logout{color:#888;font-size:13px;cursor:pointer;text-decoration:underline}
.logout:hover{color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:20px}
.card{background:#1a1d27;border:1px solid #2a2d37;border-radius:10px;padding:14px}
.card h3{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#888;margin-bottom:6px}
.card .val{font-size:24px;font-weight:600;color:#fff}
.card .sub{font-size:11px;color:#888;margin-top:3px}
.section{background:#1a1d27;border:1px solid #2a2d37;border-radius:10px;padding:16px;margin-bottom:14px}
.section h2{font-size:15px;font-weight:500;margin-bottom:12px;color:#fff}
.row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}
input[type=number],input[type=text]{background:#0f1117;border:1px solid #2a2d37;border-radius:6px;padding:8px 12px;color:#fff;font-size:14px;width:100px}
input[type=text]{width:100%}
button{background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:13px;cursor:pointer;font-weight:500}
button:hover{background:#2563eb}
button.green{background:#22c55e}
button.green:hover{background:#16a34a}
button.sm{padding:4px 10px;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}
th{text-align:left;padding:6px 8px;border-bottom:1px solid #2a2d37;color:#888;font-weight:500;font-size:10px;text-transform:uppercase}
td{padding:6px 8px;border-bottom:1px solid #1a1d27}
tr:hover td{background:#1f2230}
.text-right{text-align:right}
.text-green{color:#4ade80}
.text-red{color:#f87171}
.status{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.status.success{background:#0f3d0f;color:#4ade80}
.status.error{background:#3d0f0f;color:#f87171}
.status.running{background:#3d3d0f;color:#facc15}
.mt{margin-top:10px}
.toast{position:fixed;top:20px;right:20px;background:#22c55e;color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .3s;z-index:99}
.toast.show{opacity:1}
.toast.err{background:#ef4444}
.log-err{font-size:10px;color:#f87171;max-width:400px;word-break:break-all}
.sb-title{font-size:13px;font-weight:500;color:#fff;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.cat-tree{font-size:12px}
.cat-item{padding:3px 0;display:flex;align-items:center;gap:6px;cursor:pointer;border-radius:4px}
.cat-item:hover{background:#1f2230}
.cat-item.active{background:#1f2230}
.cat-item input[type=checkbox]{accent-color:#3b82f6;cursor:pointer;flex-shrink:0}
.cat-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cat-count{color:#666;font-size:10px;flex-shrink:0}
.cat-item.disabled .cat-name{color:#555;text-decoration:line-through}
@media(max-width:768px){.wrap{flex-direction:column}.sidebar{width:100%;max-height:300px;border-right:none;border-bottom:1px solid #2a2d37}}
</style></head><body>
<div class="wrap">

<div class="sidebar">
<div class="sb-title">Категории <button class="sm" onclick="toggleAllCats()">Вкл/Выкл все</button></div>
<div id="search-cat-wrap"><input type="text" id="cat-search" placeholder="Поиск категорий..." oninput="filterCats()" style="width:100%;margin-bottom:8px;font-size:12px;padding:6px 10px"></div>
<div class="cat-tree" id="cat-tree"></div>
</div>

<div class="main">
<div class="header">
<h1>PressPlay.kz</h1>
<span class="logout" onclick="logout()">Выйти</span>
</div>

<div class="grid" id="stats"></div>

<div class="section">
<h2>Наценка</h2>
<div class="row">
<div><input type="number" id="markup" min="0" max="200" step="1" value="20"> <span style="color:#888;font-size:13px">%</span></div>
<button onclick="setMarkup()">Применить</button>
</div>
</div>

<div class="section">
<h2>Синхронизация</h2>
<div class="row">
<button class="green" onclick="triggerSync()">Запустить</button>
<button onclick="loadAll()">Обновить</button>
</div>
<table class="mt"><thead><tr><th>#</th><th>Статус</th><th>Начало</th><th>Товаров</th><th>Ошибка</th></tr></thead>
<tbody id="logs-body"></tbody></table>
</div>

<div class="section">
<h2>Товары <span id="product-count" style="font-size:12px;color:#888"></span></h2>
<input type="text" id="search" placeholder="Поиск по названию..." oninput="debounceSearch()">
<table class="mt"><thead><tr><th>Артикул</th><th>Название</th><th>Бренд</th><th class="text-right">Дилер</th><th class="text-right">OMarket</th><th class="text-right">Остаток</th></tr></thead>
<tbody id="products-body"></tbody></table>
<div class="mt row">
<button onclick="loadProducts(currentOffset-50)" id="prev-btn" disabled>←</button>
<span id="page-info" style="font-size:12px;color:#888"></span>
<button onclick="loadProducts(currentOffset+50)" id="next-btn">→</button>
</div>
</div>

</div></div>

<div id="toast" class="toast"></div>

<script>
var currentOffset=0, totalProducts=0, searchTimer=null, allCats=[], selectedCatId=0, allCatsEnabled=true;

function toast(msg,ok){var t=document.getElementById('toast');t.textContent=msg;t.className='toast show'+(ok===false?' err':'');setTimeout(function(){t.className='toast'},2500)}

async function api(url,opts){var r=await fetch(url,opts);if(r.status===401){location.href='/admin/login';return null}return r.json()}

async function loadStats(){
  var d=await api('/api/health');if(!d)return;
  var m=((d.markup-1)*100).toFixed(0);
  document.getElementById('markup').value=m;
  var ls=d.last_sync||{};
  document.getElementById('stats').innerHTML=
    '<div class="card"><h3>Товаров</h3><div class="val">'+d.products_total+'</div></div>'+
    '<div class="card"><h3>В наличии</h3><div class="val text-green">'+d.products_in_stock+'</div></div>'+
    '<div class="card"><h3>Наценка</h3><div class="val">'+m+'%</div></div>'+
    '<div class="card"><h3>Категории</h3><div class="val">'+d.categories_enabled+'</div><div class="sub">из '+d.categories_total+'</div></div>'+
    '<div class="card"><h3>Синхр.</h3><div class="val"><span class="status '+(ls.status||'')+'">'+(ls.status||'—')+'</span></div><div class="sub">'+(ls.started_at||'')+'</div></div>';
}

async function loadCats(){
  allCats=await api('/api/categories');if(!allCats)return;
  renderCats();
}

function renderCats(){
  var search=document.getElementById('cat-search').value.toLowerCase();
  var html='';
  for(var i=0;i<allCats.length;i++){
    var c=allCats[i];
    if(search && c.name.toLowerCase().indexOf(search)===-1) continue;
    var indent=(c.level-1)*16;
    var active=c.id===selectedCatId?' active':'';
    var dis=c.sync_enabled?'':' disabled';
    html+='<div class="cat-item'+active+dis+'" style="padding-left:'+indent+'px" onclick="selectCat('+c.id+')">'+
      '<input type="checkbox" '+(c.sync_enabled?'checked':'')+' onclick="event.stopPropagation();toggleCat('+c.id+')" title="Включить/выключить для фида">'+
      '<span class="cat-name" title="'+c.name+'">'+c.name+'</span>'+
      '<span class="cat-count">'+(c.products_count||'')+'</span></div>';
  }
  document.getElementById('cat-tree').innerHTML=html;
}

function filterCats(){renderCats()}

function selectCat(id){
  selectedCatId=selectedCatId===id?0:id;
  renderCats();
  loadProducts(0);
}

async function toggleCat(id){
  var d=await api('/api/categories/'+id+'/toggle',{method:'POST'});
  if(!d)return;
  toast(d.sync_enabled?'Категория включена':'Категория выключена');
  loadCats();loadStats();
}

async function toggleAllCats(){
  allCatsEnabled=!allCatsEnabled;
  for(var i=0;i<allCats.length;i++){
    if(allCats[i].level===1){
      var c=allCats[i];
      if(c.sync_enabled!==allCatsEnabled){
        await api('/api/categories/'+c.id+'/toggle',{method:'POST'});
      }
    }
  }
  loadCats();loadStats();
  toast(allCatsEnabled?'Все включены':'Все выключены');
}

async function loadLogs(){
  var logs=await api('/api/sync/logs?limit=5');if(!logs)return;
  document.getElementById('logs-body').innerHTML=logs.map(function(l){
    return '<tr><td>'+l.id+'</td><td><span class="status '+l.status+'">'+l.status+'</span></td><td>'+(l.started_at||'')+'</td><td>'+l.products_fetched+'</td><td>'+(l.error?'<span class="log-err">'+l.error.slice(0,100)+'</span>':'')+'</td></tr>';
  }).join('');
}

async function loadProducts(offset){
  if(offset<0)offset=0;
  currentOffset=offset;
  var q=document.getElementById('search').value;
  var url='/api/products?limit=50&offset='+offset;
  if(q) url+='&search='+encodeURIComponent(q);
  if(selectedCatId) url+='&category='+selectedCatId;
  var data=await api(url);if(!data)return;
  totalProducts=data.total;
  document.getElementById('product-count').textContent='('+data.total+')';
  document.getElementById('products-body').innerHTML=data.items.map(function(p){
    var qty=p.quantity;
    var qcls=qty==='0'?'text-red':(qty.indexOf&&qty.indexOf('>')===0?'text-green':'');
    return '<tr><td>'+p.article+'</td><td>'+p.name+'</td><td>'+(p.brand||'')+'</td><td class="text-right">'+(p.price_dealer?p.price_dealer.toLocaleString():'')+'</td><td class="text-right">'+(p.price_omarket?p.price_omarket.toLocaleString():'')+'</td><td class="text-right '+qcls+'">'+qty+'</td></tr>';
  }).join('');
  document.getElementById('prev-btn').disabled=offset===0;
  document.getElementById('next-btn').disabled=data.items.length<50;
  document.getElementById('page-info').textContent=(offset+1)+'-'+(offset+data.items.length)+' / '+totalProducts;
}

async function setMarkup(){
  var pct=parseFloat(document.getElementById('markup').value);
  if(isNaN(pct)||pct<0){toast('Введите число',false);return}
  var d=await api('/api/markup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({percent:pct})});
  if(d) toast('Наценка '+pct+'%, пересчитано '+d.recalculated);
  loadAll();
}

async function triggerSync(){
  await api('/api/sync/trigger',{method:'POST'});
  toast('Синхронизация запущена');
  setTimeout(loadAll,3000);
}

function debounceSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(function(){loadProducts(0)},400)}

async function logout(){await fetch('/admin/logout',{method:'POST'});location.href='/admin/login'}

function loadAll(){loadStats();loadCats();loadLogs();loadProducts(currentOffset)}
loadAll();
setInterval(function(){loadStats();loadLogs()},30000);
</script></body></html>"""
