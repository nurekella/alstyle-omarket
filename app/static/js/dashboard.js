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
