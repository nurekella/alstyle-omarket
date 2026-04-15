// ─── core helpers ────────────────────────────────────────────
var state = {
  suppliers: [],
  feeds: [],
  health: null,
  cats: [],
  selectedCat: 0,
  productsOffset: 0,
  productsSearch: '',
  productsTotal: 0,
  markupSaveTimer: null,
  searchTimer: null,
};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function fmt(n){return (n==null?'—':Number(n).toLocaleString('ru-RU'))}
function bytes(n){if(!n)return'0 B';var u=['B','KB','MB','GB'];var i=Math.floor(Math.log(n)/Math.log(1024));return (n/Math.pow(1024,i)).toFixed(1)+' '+u[i]}

function toast(msg, ok){
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (ok===false?' err':'');
  clearTimeout(toast._t);
  toast._t = setTimeout(function(){t.className='toast'}, 2500);
}

async function api(url, opts){
  var r = await fetch(url, opts);
  if (r.status === 401){ location.href = '/admin/login'; return null }
  if (r.status >= 400){
    var msg = 'Ошибка ' + r.status;
    try { var j = await r.json(); if(j.error) msg = j.error } catch(_){}
    toast(msg, false);
    return null;
  }
  var ct = r.headers.get('content-type') || '';
  if (ct.indexOf('application/json') > -1) return r.json();
  return r.text();
}

function modal(html){
  var m = document.getElementById('modal');
  document.getElementById('modal-body').innerHTML = html;
  m.classList.add('show');
  m.onclick = function(e){ if(e.target===m) closeModal() };
}
function closeModal(){ document.getElementById('modal').classList.remove('show') }

async function logout(){ await fetch('/admin/logout',{method:'POST'}); location.href='/admin/login' }

// ─── router ──────────────────────────────────────────────────
var routes = {};
function route(name, handler){ routes[name] = handler }

function navigate(hash){
  location.hash = hash;
}

async function handleRoute(){
  var hash = location.hash.replace(/^#\/?/, '') || 'home';
  var parts = hash.split('/');
  var name = parts[0];
  var args = parts.slice(1);

  document.querySelectorAll('.nav-item').forEach(function(el){
    el.classList.toggle('active', el.dataset.route === hash);
  });

  var view = document.getElementById('view');
  view.innerHTML = '<div class="muted">Загрузка...</div>';
  var handler = routes[name] || routes.home;
  try { await handler(args, view); } catch(e){ view.innerHTML = '<div class="text-red">Ошибка: '+esc(e.message)+'</div>'; }
}

window.addEventListener('hashchange', handleRoute);

// ─── sidebar ─────────────────────────────────────────────────
async function refreshSidebar(){
  var [suppliers, feeds] = await Promise.all([
    api('/api/suppliers'),
    api('/api/feeds'),
  ]);
  if (suppliers) state.suppliers = suppliers;
  if (feeds) state.feeds = feeds;

  var sup = document.getElementById('nav-suppliers');
  sup.innerHTML = state.suppliers.map(function(s){
    var dot = s.last_sync_status === 'success' ? 'ok' : (s.last_sync_status === 'error' ? 'err' : (s.last_sync_status === 'running' ? 'warn' : 'off'));
    return '<a class="nav-item" data-route="supplier/'+s.id+'">'+
      '<span class="nav-ico">📦</span>'+esc(s.name)+
      '<span class="dot '+dot+'"></span></a>';
  }).join('');

  var fd = document.getElementById('nav-feeds');
  fd.innerHTML = state.feeds.map(function(f){
    return '<a class="nav-item" data-route="feed/'+f.id+'">'+
      '<span class="nav-ico">📤</span>'+esc(f.name)+
      '<span class="count">'+fmt(f.offers_count||0)+'</span></a>';
  }).join('');

  document.querySelectorAll('.nav-item[data-route]').forEach(function(el){
    el.onclick = function(e){ e.preventDefault(); navigate('#/'+el.dataset.route) };
    if (el.dataset.route === (location.hash.replace(/^#\/?/, '') || 'home')) el.classList.add('active');
  });
}

// ─── crumbs ──────────────────────────────────────────────────
function setCrumbs(parts){
  var html = parts.map(function(p,i){
    var last = i === parts.length-1;
    return (last ? '<span>' + esc(p.text) + '</span>' : '<a href="#/'+p.href+'">'+esc(p.text)+'</a>');
  }).join('<span class="sep">›</span>');
  document.getElementById('crumbs').innerHTML = html;
}

// ─── HOME ────────────────────────────────────────────────────
route('home', async function(args, view){
  setCrumbs([{text:'Главная'}]);
  var [health, suppliers, feeds] = await Promise.all([
    api('/api/health'), api('/api/suppliers'), api('/api/feeds')
  ]);
  if (!health) return;
  state.health = health;
  var ls = health.last_sync || {};
  var feedOffers = (feeds||[]).reduce(function(a,f){return a+(f.offers_count||0)},0);

  view.innerHTML =
    '<div class="h1">Обзор</div>'+
    '<div class="grid">'+
      card('Товаров в БД', fmt(health.products_total))+
      card('В наличии', fmt(health.products_in_stock), null, 'text-green')+
      card('В XML-фидах', fmt(feedOffers), 'после фильтров')+
      card('Категорий', fmt(health.categories_enabled)+' / '+fmt(health.categories_total))+
      card('В ЧС', fmt(health.blacklist_count))+
      card('Посл. sync', '<span class="status '+(ls.status||'')+'">'+(ls.status||'—')+'</span>', ls.started_at||'')+
    '</div>'+

    '<div class="section"><div class="h2">Поставщики</div>'+
      (suppliers||[]).map(function(s){
        var dot = s.last_sync_status==='success'?'ok':(s.last_sync_status==='error'?'err':'off');
        return '<div class="supplier-card" onclick="navigate(\'#/supplier/'+s.id+'\')">'+
          '<div class="supplier-icon">'+s.name.substring(0,2).toUpperCase()+'</div>'+
          '<div class="supplier-info">'+
            '<div class="supplier-name">'+esc(s.name)+' <span class="dot '+dot+'"></span></div>'+
            '<div class="supplier-stats">'+fmt(s.products_total)+' товаров · '+fmt(s.products_in_stock)+' в наличии · sync каждые '+s.sync_interval_minutes+' мин</div>'+
          '</div>'+
          '<button class="ghost sm">Открыть →</button>'+
        '</div>';
      }).join('')+
    '</div>'+

    '<div class="section"><div class="h2">XML-фиды</div>'+
      (feeds||[]).map(feedMini).join('')+
    '</div>';
});

function card(h, v, sub, cls){
  return '<div class="card"><h3>'+esc(h)+'</h3><div class="val '+(cls||'')+'">'+v+'</div>'+(sub?'<div class="sub">'+esc(sub)+'</div>':'')+'</div>';
}

function feedMini(f){
  return '<div class="feed-card">'+
    '<div class="feed-head">'+
      '<div class="feed-title">📤 '+esc(f.name)+'</div>'+
      '<div>'+
        '<button class="ghost sm" onclick="navigate(\'#/feed/'+f.id+'\')">Настройки</button> '+
        '<a href="'+f.url_path+'" target="_blank"><button class="sm">↗ Открыть</button></a>'+
      '</div>'+
    '</div>'+
    '<div class="url-box">'+esc(f.absolute_url)+'</div>'+
    '<div class="help">'+fmt(f.offers_count)+' товаров · '+bytes(f.size_bytes)+(f.age_seconds!=null?' · обновлён '+f.age_seconds+' сек назад':'')+'</div>'+
  '</div>';
}

// ─── SUPPLIER DETAIL ─────────────────────────────────────────
route('supplier', async function(args, view){
  var id = args[0] || 'alstyle';
  var suppliers = state.suppliers.length ? state.suppliers : await api('/api/suppliers');
  var s = (suppliers||[]).find(function(x){return x.id===id});
  if (!s){ view.innerHTML = '<div class="text-red">Поставщик не найден</div>'; return }

  setCrumbs([{text:'Поставщики', href:'home'}, {text:s.name}]);
  view.innerHTML =
    '<div class="h1">'+esc(s.name)+' <span class="badge">Поставщик</span></div>'+

    '<div class="grid" id="sup-stats"></div>'+

    '<div class="section"><div class="h2">Синхронизация</div>'+
      '<div class="row"><button class="green" onclick="triggerSync()">▶ Запустить sync</button>'+
      '<button class="ghost" onclick="handleRoute()">⟳ Обновить</button></div>'+
      '<table><thead><tr><th>#</th><th>Статус</th><th>Начало</th><th>Товаров</th><th>Ошибка</th></tr></thead>'+
      '<tbody id="sync-logs-body"></tbody></table>'+
    '</div>'+

    '<div class="two-col">'+
      '<div class="section">'+
        '<div class="h2">Категории <button class="sm ghost" onclick="toggleAllCats()">Все вкл/выкл</button></div>'+
        '<div class="hint">Галочка — выгружать в XML. Поле % — своя наценка категории (пусто = наследуется).</div>'+
        '<input type="text" id="cat-search" placeholder="Поиск категорий..." oninput="renderCats()" style="margin-bottom:8px">'+
        '<div class="cat-tree" id="cat-tree"></div>'+
      '</div>'+

      '<div class="section">'+
        '<div class="h2">Товары <span class="muted" id="product-count"></span></div>'+
        '<input type="text" id="search" placeholder="Поиск по названию..." oninput="debounceSearch()">'+
        '<table><thead><tr><th>Артикул</th><th>Название</th><th>Бренд</th><th class="text-right">Дилер</th><th class="text-right">OMarket</th><th class="text-right">Остаток</th><th></th></tr></thead>'+
        '<tbody id="products-body"></tbody></table>'+
        '<div class="row" style="margin-top:10px">'+
          '<button class="ghost sm" onclick="loadProducts(state.productsOffset-50)" id="prev-btn" disabled>←</button>'+
          '<span id="page-info" class="muted" style="font-size:12px"></span>'+
          '<button class="ghost sm" onclick="loadProducts(state.productsOffset+50)" id="next-btn">→</button>'+
          '<div style="flex:1"></div>'+
          '<a href="/api/export/xlsx"><button class="ghost sm">📊 Скачать Excel</button></a>'+
        '</div>'+
      '</div>'+
    '</div>';

  // Load stats, cats, products, logs in parallel
  loadSupStats();
  loadCats();
  loadProducts(0);
  loadSyncLogs();
});

async function loadSupStats(){
  var h = await api('/api/health');
  if (!h) return;
  state.health = h;
  var ls = h.last_sync || {};
  var pct = ((h.markup-1)*100).toFixed(0);
  document.getElementById('sup-stats').innerHTML =
    card('Товаров', fmt(h.products_total))+
    card('В наличии', fmt(h.products_in_stock), null, 'text-green')+
    card('Категории', fmt(h.categories_enabled)+' / '+fmt(h.categories_total))+
    card('Наценка по ум.', pct+'%')+
    card('Статус sync', '<span class="status '+(ls.status||'')+'">'+(ls.status||'—')+'</span>', ls.started_at||'');
}

async function loadCats(){
  var cats = await api('/api/categories');
  if (!cats) return;
  state.cats = cats;
  renderCats();
}

function renderCats(){
  var q = (document.getElementById('cat-search')||{}).value || '';
  q = q.toLowerCase();
  var html = '';
  for (var i=0;i<state.cats.length;i++){
    var c = state.cats[i];
    if (q && c.name.toLowerCase().indexOf(q) === -1) continue;
    var indent = (c.level-1)*14;
    var dis = c.sync_enabled ? '' : ' disabled';
    var has = c.markup_percent!=null ? ' has-markup' : '';
    html += '<div class="cat-item'+dis+has+'" style="padding-left:'+indent+'px">'+
      '<input type="checkbox" '+(c.sync_enabled?'checked':'')+' onclick="toggleCat('+c.id+')" title="Выгружать в XML">'+
      '<span class="cat-name" title="'+esc(c.name)+'" onclick="selectCat('+c.id+')">'+esc(c.name)+'</span>'+
      '<input class="cat-markup" type="number" min="0" max="500" step="1" placeholder="%" value="'+(c.markup_percent!=null?c.markup_percent:'')+'" onchange="saveCatMarkup('+c.id+',this.value)" title="Наценка категории (пусто = наследуется)">'+
      '<span class="cat-count">'+(c.products_count||'')+'</span>'+
    '</div>';
  }
  document.getElementById('cat-tree').innerHTML = html || '<div class="muted" style="padding:10px">Ничего не найдено</div>';
}

function selectCat(id){
  state.selectedCat = state.selectedCat === id ? 0 : id;
  loadProducts(0);
  // highlight
  document.querySelectorAll('.cat-item').forEach(function(el){el.style.background=''});
}

async function toggleCat(id){
  var d = await api('/api/categories/'+id+'/toggle', {method:'POST'});
  if (!d) return;
  toast(d.sync_enabled ? 'Категория включена' : 'Категория выключена');
  loadCats();
  loadSupStats();
  refreshSidebar();
}

async function toggleAllCats(){
  if (!confirm('Переключить все корневые категории?')) return;
  var anyEnabled = state.cats.some(function(c){return c.level===1 && c.sync_enabled});
  var target = !anyEnabled;
  for (var i=0;i<state.cats.length;i++){
    var c = state.cats[i];
    if (c.level===1 && c.sync_enabled !== target){
      await api('/api/categories/'+c.id+'/toggle', {method:'POST'});
    }
  }
  loadCats();
  loadSupStats();
  refreshSidebar();
  toast(target ? 'Все включены' : 'Все выключены');
}

function saveCatMarkup(id, value){
  clearTimeout(state.markupSaveTimer);
  state.markupSaveTimer = setTimeout(async function(){
    var pct = value === '' ? null : parseFloat(value);
    var r = await api('/api/categories/'+id+'/markup', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({percent: pct})
    });
    if (r) { toast('Наценка категории обновлена, пересчитано '+r.recalculated); loadCats() }
  }, 400);
}

async function loadSyncLogs(){
  var logs = await api('/api/sync/logs?limit=5');
  if (!logs) return;
  document.getElementById('sync-logs-body').innerHTML = logs.map(function(l){
    return '<tr><td>'+l.id+'</td><td><span class="status '+l.status+'">'+l.status+'</span></td><td>'+(l.started_at||'')+'</td><td>'+fmt(l.products_fetched)+'</td><td>'+(l.error?'<span class="log-err">'+esc(l.error.slice(0,100))+'</span>':'')+'</td></tr>';
  }).join('') || '<tr><td colspan="5" class="muted">Нет записей</td></tr>';
}

async function loadProducts(offset){
  if (offset<0) offset=0;
  state.productsOffset = offset;
  var q = (document.getElementById('search')||{}).value || '';
  var url = '/api/products?limit=50&offset='+offset;
  if (q) url += '&search=' + encodeURIComponent(q);
  if (state.selectedCat) url += '&category=' + state.selectedCat;
  var d = await api(url);
  if (!d) return;
  state.productsTotal = d.total;
  document.getElementById('product-count').textContent = '('+d.total+')';
  document.getElementById('products-body').innerHTML = d.items.map(function(p){
    var qty = p.quantity || '0';
    var qcls = qty === '0' ? 'text-red' : (qty.indexOf && qty.indexOf('>')===0 ? 'text-green' : '');
    var bl = p.blacklisted ? '<span class="status error">ЧС</span>' : '<button class="sm ghost" title="В чёрный список" onclick="blacklistAdd('+p.article+',\''+esc(p.name).replace(/'/g,"\\'")+'\')">×</button>';
    return '<tr><td>'+p.article+'</td><td>'+esc(p.name)+'</td><td>'+esc(p.brand||'')+'</td>'+
      '<td class="text-right">'+(p.price_dealer?fmt(p.price_dealer):'')+'</td>'+
      '<td class="text-right">'+(p.price_omarket?fmt(p.price_omarket):'')+'</td>'+
      '<td class="text-right '+qcls+'">'+esc(qty)+'</td>'+
      '<td>'+bl+'</td></tr>';
  }).join('') || '<tr><td colspan="7" class="muted">Нет товаров</td></tr>';
  document.getElementById('prev-btn').disabled = offset===0;
  document.getElementById('next-btn').disabled = d.items.length < 50;
  document.getElementById('page-info').textContent = (offset+1)+'-'+(offset+d.items.length)+' из '+d.total;
}

function debounceSearch(){
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(function(){loadProducts(0)}, 400);
}

async function triggerSync(){
  await api('/api/sync/trigger', {method:'POST'});
  toast('Синхронизация запущена');
  setTimeout(function(){ loadSyncLogs(); loadSupStats(); refreshSidebar() }, 2500);
}

async function blacklistAdd(article, name){
  var reason = prompt('Причина исключения "'+name+'":', '');
  if (reason === null) return;
  var r = await api('/api/blacklist', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({article: article, reason: reason || null})
  });
  if (r) { toast('Добавлено в чёрный список'); loadProducts(state.productsOffset) }
}

// ─── FEED DETAIL ─────────────────────────────────────────────
route('feed', async function(args, view){
  var id = args[0] || 'omarket';
  var feeds = state.feeds.length ? state.feeds : await api('/api/feeds');
  var f = (feeds||[]).find(function(x){return x.id===id});
  if (!f){ view.innerHTML = '<div class="text-red">Фид не найден</div>'; return }
  setCrumbs([{text:'XML-фиды', href:'home'}, {text:f.name}]);

  view.innerHTML =
    '<div class="h1">'+esc(f.name)+' <span class="badge">XML-фид</span></div>'+

    '<div class="section">'+
      '<div class="h2">URL фида</div>'+
      '<div class="url-box" id="feed-url">'+esc(f.absolute_url)+'</div>'+
      '<div class="row" style="margin-top:12px">'+
        '<a href="'+f.url_path+'" target="_blank"><button>↗ Открыть в новой вкладке</button></a>'+
        '<button class="ghost" onclick="copyUrl(\''+f.absolute_url+'\')">📋 Скопировать URL</button>'+
        '<button class="ghost" onclick="feedPreview(\''+f.id+'\')">👁 Preview</button>'+
        '<button class="ghost" onclick="feedRefresh(\''+f.id+'\')">⟳ Пересобрать</button>'+
      '</div>'+
    '</div>'+

    '<div class="grid">'+
      card('Товаров в фиде', fmt(f.offers_count))+
      card('Размер', bytes(f.size_bytes), 'gzip на лету')+
      card('Магазинов', (f.store_ids||[]).length)+
      card('Формат', f.format || '—')+
      card('TTL кэша', (f.ttl_seconds||0)+' сек', f.age_seconds!=null?'возраст: '+f.age_seconds+' сек':'кэш пуст')+
    '</div>'+

    '<div class="section">'+
      '<div class="h2">Магазины</div>'+
      '<div class="help">ID точек продаж, передаются в каждый &lt;availability&gt; в XML.</div>'+
      '<div style="margin-top:8px">'+(f.store_ids||[]).map(function(s){return '<span class="url-box" style="display:inline-block;margin:4px 4px 0 0;padding:4px 8px">'+esc(s)+'</span>'}).join('')+'</div>'+
    '</div>';
});

async function copyUrl(url){
  try { await navigator.clipboard.writeText(url); toast('Ссылка скопирована') }
  catch(e){ toast('Не удалось скопировать', false) }
}

async function feedRefresh(id){
  var r = await api('/api/feeds/'+id+'/refresh', {method:'POST'});
  if (r){ toast('Пересобран: '+r.offers+' товаров'); handleRoute(); refreshSidebar() }
}

async function feedPreview(id){
  var d = await api('/api/feeds/'+id+'/preview?limit=30');
  if (!d) return;
  modal('<h3>Preview ('+d.shown_offers+' из '+d.total_offers+' offers) <button class="ghost sm" onclick="closeModal()">✕</button></h3>'+
    '<pre>'+esc(d.preview)+'</pre>');
}

// ─── SETTINGS ────────────────────────────────────────────────
route('settings', async function(args, view){
  setCrumbs([{text:'Настройки'}]);
  var h = await api('/api/health');
  var mp = await api('/api/settings/min_price');
  if (!h || !mp) return;
  var pct = ((h.markup-1)*100).toFixed(0);

  view.innerHTML =
    '<div class="h1">Настройки</div>'+

    '<div class="section">'+
      '<div class="h2">Глобальная наценка</div>'+
      '<div class="hint">Применяется к товарам, если у их категории не задана своя наценка.</div>'+
      '<div class="row end">'+
        '<div><input type="number" id="markup" min="0" max="500" step="1" value="'+pct+'"><span class="muted" style="margin-left:6px">%</span></div>'+
        '<button onclick="setMarkup()">Применить</button>'+
      '</div>'+
    '</div>'+

    '<div class="section">'+
      '<div class="h2">Минимальная цена в XML</div>'+
      '<div class="hint">Товары с OMarket-ценой ниже этого порога не попадают в фид. Пусто/0 = без фильтра.</div>'+
      '<div class="row end">'+
        '<div><input type="number" id="min-price" min="0" step="1" value="'+(mp.value||0)+'"><span class="muted" style="margin-left:6px">₸</span></div>'+
        '<button onclick="setMinPrice()">Сохранить</button>'+
      '</div>'+
    '</div>'+

    '<div class="section">'+
      '<div class="h2">Экспорт</div>'+
      '<div class="hint">Excel со всеми товарами, ценами (дилер/OMarket), рассчитанной наценкой и статусом активности.</div>'+
      '<div class="row" style="margin-top:10px">'+
        '<a href="/api/export/xlsx"><button>📊 Скачать прайс (XLSX)</button></a>'+
      '</div>'+
    '</div>';
});

async function setMarkup(){
  var pct = parseFloat(document.getElementById('markup').value);
  if (isNaN(pct) || pct<0){ toast('Введите число', false); return }
  var r = await api('/api/markup', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({percent: pct})});
  if (r) { toast('Наценка '+pct+'%, пересчитано '+r.recalculated); refreshSidebar() }
}

async function setMinPrice(){
  var v = parseFloat(document.getElementById('min-price').value || 0);
  if (isNaN(v) || v<0){ toast('Введите число', false); return }
  var r = await api('/api/settings/min_price', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({value: v})});
  if (r) { toast('Мин. цена: '+v+' ₸'); refreshSidebar() }
}

// ─── BLACKLIST ───────────────────────────────────────────────
route('blacklist', async function(args, view){
  setCrumbs([{text:'Чёрный список'}]);
  var list = await api('/api/blacklist');
  if (!list) return;

  view.innerHTML =
    '<div class="h1">Чёрный список <span class="badge">'+list.length+'</span></div>'+

    '<div class="section">'+
      '<div class="h2">Добавить артикул</div>'+
      '<div class="hint">Товар будет исключён из всех XML-фидов сразу после сохранения.</div>'+
      '<div class="row end" style="margin-top:8px">'+
        '<input type="number" id="bl-article" placeholder="Артикул" style="width:140px">'+
        '<input type="text" id="bl-reason" placeholder="Причина (опционально)" style="flex:1">'+
        '<button onclick="blacklistSubmit()">Добавить</button>'+
      '</div>'+
    '</div>'+

    '<div class="section">'+
      '<div class="h2">Список</div>'+
      '<table><thead><tr><th>Артикул</th><th>Название</th><th>Причина</th><th>Добавлен</th><th></th></tr></thead>'+
      '<tbody>'+
        (list.length ? list.map(function(b){
          return '<tr><td>'+b.article+'</td><td>'+esc(b.name||'—')+'</td><td>'+esc(b.reason||'')+'</td><td class="muted">'+esc(b.added_at.slice(0,19))+'</td>'+
            '<td><button class="danger sm" onclick="blacklistRemove('+b.article+')">Убрать</button></td></tr>';
        }).join('') : '<tr><td colspan="5" class="muted">Список пуст</td></tr>')+
      '</tbody></table>'+
    '</div>';
});

async function blacklistSubmit(){
  var a = parseInt(document.getElementById('bl-article').value);
  var r = document.getElementById('bl-reason').value;
  if (!a || isNaN(a)){ toast('Введите артикул', false); return }
  var res = await api('/api/blacklist', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({article: a, reason: r || null})});
  if (res) { toast('Добавлено'); handleRoute() }
}

async function blacklistRemove(article){
  if (!confirm('Убрать артикул '+article+' из ЧС?')) return;
  var res = await api('/api/blacklist/'+article, {method:'DELETE'});
  if (res) { toast('Удалено'); handleRoute() }
}

// ─── boot ────────────────────────────────────────────────────
(async function init(){
  await refreshSidebar();
  await handleRoute();
  setInterval(refreshSidebar, 30000);
})();
