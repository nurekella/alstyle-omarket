async function go(){
  var r=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('pw').value})});
  if(r.ok){location.href='/admin';return}
  var err=document.getElementById('err');
  err.textContent=r.status===429?'Слишком много попыток. Подождите.':'Неверный пароль';
  err.style.display='block';
  document.getElementById('pw').value='';
  document.getElementById('pw').focus();
}
document.getElementById('pw').addEventListener('keydown',function(e){if(e.key==='Enter')go()});
document.getElementById('submit').addEventListener('click',go);
