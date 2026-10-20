'use strict';
const $ = s => document.querySelector(s);
const levels = {'WATCH':0,'B':1,'A':2,'S+':3,'S++':4};
const labels = {NEW_PRODUCT:'Nuevo producto',PRICE_DROP:'Bajó de precio',PRICE_INCREASE:'Subió de precio',PROMO_ON:'Inicia promoción',PROMO_OFF:'Termina promoción',LISTED_OLD_PRICE_CHANGE:'Cambió el precio anterior publicado',DISAPPEARED:'Dejó de aparecer',REAPPEARED:'Volvió a aparecer',SOURCE_ERROR:'Lectura pendiente'};
const currency = new Intl.NumberFormat('es-MX',{style:'currency',currency:'MXN',maximumFractionDigits:2});
const datetime = new Intl.DateTimeFormat('es-MX',{timeZone:'America/Mexico_City',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false});
const timeonly = new Intl.DateTimeFormat('es-MX',{timeZone:'America/Mexico_City',hour:'2-digit',minute:'2-digit',hour12:false});
let data = null, tab = 'alerts', category = 'Todas', minimum = 'B', fetchFailed = false;
let notificationTopic = '', openNotificationSetup = false;
try {
  notificationTopic = localStorage.getItem('radar-notification-topic') || '';
  const incoming = new URLSearchParams(location.hash.slice(1)).get('notify');
  if (incoming && /^[A-Za-z0-9_-]{32,160}$/.test(incoming)) {
    notificationTopic = incoming;
    openNotificationSetup = true;
    localStorage.setItem('radar-notification-topic', incoming);
    history.replaceState(null, '', location.pathname + location.search);
  }
} catch {}
try { minimum = localStorage.getItem('radar-minimum') || 'B'; } catch {}
if (!(minimum in levels) || minimum === 'WATCH') minimum = 'B';
const escape = value => String(value ?? '').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
const money = cents => currency.format(cents / 100);
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const categoryIcon = value => ({Celulares:'phone',Tablets:'phone',Relojes:'watch','Audífonos':'headphones'})[value] || 'box';
function productURL(value) { try { const u = new URL(value); return u.protocol === 'https:' && u.hostname === 'www.telcel.com' && !u.username && u.pathname.startsWith('/tienda/producto/') ? u.href : ''; } catch { return ''; } }
function stale(p) { return !p.last_seen || Date.now()-Date.parse(p.last_seen)>30*60*1000; }
function personalSignal(p) { return p.signal?.reasons?.some(r=>['PRICE_TARGET','PUBLISHED_PROMOTION'].includes(r.code)); }
function visibleAlert(p) { return p && p.present && !stale(p) && (personalSignal(p) || levels[p.anomaly.level]>=levels[minimum]); }
function notificationLinks() {
  const valid = /^[A-Za-z0-9_-]{32,160}$/.test(notificationTopic);
  $('#subscribe-phone').hidden = !valid;
  $('#subscribe-web').hidden = !valid;
  if (valid) {
    $('#subscribe-phone').href = `ntfy://ntfy.sh/${notificationTopic}`;
    $('#subscribe-web').href = `https://ntfy.sh/${notificationTopic}`;
  }
}
function empty(title,message,link=false) { return `<div class="empty">${icon('radar')}<h3>${escape(title)}</h3><p>${escape(message)}</p>${link ? '<a href="https://www.telcel.com/tienda/" target="_blank" rel="noopener noreferrer">Abrir tienda Telcel ↗</a>' : ''}</div>`; }

function renderStatus() {
  const status = data.status, all = data.products, current = all.filter(p=>p.present && !stale(p));
  const alerts = data.alerts.map(a=>all.find(p=>p.id===a.product_id)).filter(p=>p && !stale(p) && p.present);
  const outdated = !status.last_attempt || Date.now()-Date.parse(status.last_attempt)>30*60*1000;
  const operational = status.automation === 'github_actions' && ['OK','PARTIAL'].includes(status.state) && !outdated && !fetchFailed;
  document.body.classList.toggle('is-live',operational);
  $('#count-products').textContent = current.length.toLocaleString('es-MX');
  $('#count-alerts').textContent = alerts.filter(p=>p.signal?.notify || levels[p.anomaly.level]>=3).length;
  $('#count-promos').textContent = current.filter(p=>p.promotion).length;
  const discovered = status.surfaces.reduce((sum,s)=>sum+(s.discovered||0),0);
  $('#catalog-discovered').textContent = `${discovered.toLocaleString('es-MX')} fichas descubiertas`;
  $('#system-state').textContent = operational ? (status.state==='PARTIAL'?'PARCIAL':'ACTIVO') : status.automation !== 'github_actions' ? 'PENDIENTE' : outdated || fetchFailed ? 'ATRASADO' : 'SIN LECTURA';
  $('#system-state').className = operational ? 'status-ok' : 'status-error';
  $('#system-detail').textContent = operational ? (status.state==='PARTIAL'?'Lectura progresiva':'Barrido real completado') : 'Sin monitoreo confirmado';
  $('#health-text').textContent = fetchFailed ? 'No se pudo actualizar la conexión. Se conserva el último estado recibido.' : status.automation !== 'github_actions' ? 'Monitoreo aún no desplegado. La consulta inicial de Telcel no obtuvo precios verificables.' : outdated ? 'La última observación está atrasada. Las ofertas antiguas no se muestran como actuales.' : status.state === 'PARTIAL' ? 'Lectura parcial: hay precios observados; las páginas restantes continuarán en los siguientes barridos.' : status.state !== 'OK' ? 'Telcel no entregó datos verificables en todas las superficies. El histórico permanece intacto.' : 'Radar activo. Precios observados y memoria actualizada automáticamente.';
  $('#last-check').textContent = status.last_attempt ? `Consulta: ${datetime.format(new Date(status.last_attempt))} CDMX` : 'Sin barridos';
  $('#last-check').dateTime = status.last_attempt || '';
  $('#source-pill').innerHTML = `<span class="dot ${operational?'':'amber'}"></span> ${operational?'FUENTE ACTIVA':'SIN LECTURA VÁLIDA'}`;
  const symbols = {smartphones:'phone',accessories:'headphones',connected:'watch',tablets:'phone'};
  $('#surfaces').innerHTML = status.surfaces.map(s=>`<article class="surface">${icon(symbols[s.id]||'box')}<strong>${escape(s.name)}</strong><p><b>${Number(s.detected)||0}</b> leídos · ${Number(s.discovered)||0} fichas</p><span>${s.status==='DEFERRED'?'Memoria conservada · prioridad a celulares':s.status==='OK'?'Lectura confirmada':'Precios pendientes de lectura'}</span><a href="${escape(s.url && s.url.startsWith('https://www.telcel.com/tienda/categoria/')?s.url:'https://www.telcel.com/tienda/')}" target="_blank" rel="noopener noreferrer">Abrir catálogo ↗</a></article>`).join('');
  const push = status.notifications;
  $('#push-status').textContent = push.configured ? `Emisor activo. ${push.pending||0} avisos pendientes; ${push.delivered||0} aceptados en esta ejecución.${push.last_accepted_at?' Último envío: '+datetime.format(new Date(push.last_accepted_at))+' CDMX.':''} La recepción en tu teléfono requiere suscribirte y permitir notificaciones.` : 'Emisor pendiente de activar en GitHub. Las alertas ya aparecen en el radar; todavía no se envían al celular.';
  $('#alert-rules').textContent = 'Celulares < $1,800 · promoción ≥ 50% y ahorro ≥ $1,000 · baja observada ≥ 30%. Ventanas intensivas: 02:00–04:59 y 14:00–17:59 CDMX.';
  notificationLinks();
}

function renderFilters() {
  const available = [...new Set(data.products.filter(p=>p.present).map(p=>p.category))];
  if (!available.includes(category)) category = 'Todas';
  $('#filters').innerHTML = ['Todas',...available].map(c=>`<button data-category="${escape(c)}" class="${c===category?'selected':''}" aria-pressed="${c===category}">${c==='Todas'?'':icon(categoryIcon(c))}${escape(c)}</button>`).join('');
}

function offer(p) {
  const a=p.anomaly, old=p.listed_old_price_cents, url=productURL(p.url);
  const image = p.image && p.image.startsWith('https://www.telcel.com/') ? `<img src="${escape(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : icon(categoryIcon(p.category));
  const signal = p.signal;
  const reason = signal ? signal.reasons.map(r=>r.detail).join(' · ') : a.reason;
  const badge = signal?.reasons?.some(r=>r.code==='PRICE_TARGET')?'<$1,800':signal?.reasons?.some(r=>r.code==='PUBLISHED_PROMOTION')&&levels[a.level]<3?'PROMO':a.level;
  return `<article class="offer ${signal?.notify?'attention':''}" data-level="${escape(a.level)}"><span class="level ${signal?.notify||levels[a.level]>=3?'high':''}">${escape(badge)}</span><div class="product-visual">${image}</div><div class="offer-name"><small>Telcel · ${escape(p.category)}</small><h3>${escape(p.name)}</h3><p>${escape(p.variant||'SKU '+p.sku)}</p></div><div class="price-block"><strong>${p.price_basis==='advertised_from'?'<small>Desde </small>':''}${money(p.price_cents)} <small>MXN</small></strong>${old?`<del>${money(old)}</del><small>Anterior publicado por Telcel</small>`:''}${a.drop_pct>0?`<span class="drop">↓ ${escape(a.drop_pct)}% observado</span>`:''}</div><div class="signal-info">${signal?`<span class="signal-state ${signal.confirmed?'confirmed':''}">${escape(signal.label)} · ${escape(signal.status)}</span>`:''}<b>${escape(reason)}</b>${p.availability==='in_stock'?'Disponible al revisar ficha':p.availability==='out_of_stock'?'Agotado al observar':'Disponibilidad sin confirmar'}${p.detail_checked_at?` · Ficha: ${datetime.format(new Date(p.detail_checked_at))} CDMX`:''}</div><button class="offer-open" data-detail="${escape(p.id)}" aria-label="Ver evidencia de ${escape(p.name)}">${icon('arrow')}</button><div class="offer-cta"><span>${stale(p)?'Dato anterior · ':''}${datetime.format(new Date(p.last_seen))} CDMX</span>${url?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">VER OFERTA ↗</a>`:''}</div></article>`;
}

function eventMarkup(e) {
  const error=e.type==='SOURCE_ERROR';
  return `<article class="event ${error?'error':''}"><span class="dot"></span><time datetime="${escape(e.at)}">${timeonly.format(new Date(e.at))}</time><div><strong>${escape(labels[e.type]||e.type)}</strong> ${escape(e.name||e.surface||'')}${error?`<small>${escape(e.reason||'La fuente no produjo precios verificables.')}</small>`:''}</div>${e.before_cents && e.after_cents?`<b>${money(e.before_cents)} → ${money(e.after_cents)}</b>`:''}</article>`;
}

function renderFeed() {
  $('#feed-title span').textContent = {alerts:'Alertas accionables',telcel:'Productos observados',events:'Movimientos del radar'}[tab];
  $('.sort').hidden = tab==='events';
  if (tab==='events') {
    const events=data.events.filter(e=>category==='Todas'||data.products.find(p=>p.id===e.product_id)?.category===category);
    $('#feed').innerHTML=events.length?events.map(eventMarkup).join(''):empty('Sin movimientos registrados','Aquí aparecerán los cambios observados, con su hora real.');
    return;
  }
  let products=tab==='alerts'?data.alerts.map(a=>data.products.find(p=>p.id===a.product_id)).filter(visibleAlert):data.products.filter(p=>p.present);
  products=products.filter(p=>category==='Todas'||p.category===category);
  const order=$('#sort').value;
  products.sort(order==='price'?(a,b)=>a.price_cents-b.price_cents:order==='recent'?(a,b)=>Date.parse(b.last_seen)-Date.parse(a.last_seen):(a,b)=>(b.signal?.priority||0)-(a.signal?.priority||0)||levels[b.anomaly.level]-levels[a.anomaly.level]||b.anomaly.drop_pct-a.anomaly.drop_pct);
  $('#feed').innerHTML=products.length?products.map(offer).join(''):empty(tab==='alerts'?'Sin alertas vigentes para estos filtros':'Aún no hay precios verificados',data.products.length===0?'El radar necesita completar su primera lectura de precios. Las fichas encontradas en el sitemap no se presentan como ofertas.':`Filtro histórico ${minimum}. Los celulares debajo de $1,800 y promociones fuertes se incluyen independientemente del nivel.`,true);
}

function render() {
  renderStatus(); renderFilters(); renderFeed();
  $('#recent-events').innerHTML=data.events.length?data.events.slice(0,3).map(eventMarkup).join(''):'<p class="event empty-event">El primer barrido válido iniciará el histórico de movimientos.</p>';
}

async function load() {
  try {
    if (!navigator.onLine) throw new Error('Offline');
    const response=await fetch('./data/dashboard.json',{cache:'no-store'});
    if(!response.ok) throw new Error('Connection');
    const next=await response.json();
    if(next.schema_version!==1||!Array.isArray(next.products)||!Array.isArray(next.alerts)||!Array.isArray(next.events)||!Array.isArray(next.status?.surfaces)) throw new Error('Schema');
    data=next; fetchFailed=false; render();
  } catch {
    fetchFailed=true;
    if(data) render();
    else {$('#system-state').textContent='SIN CONEXIÓN';$('#health-text').textContent='No se pudo cargar el estado del radar. La conexión se volverá a comprobar automáticamente.';$('#last-check').textContent='Sin lectura disponible';$('#feed').innerHTML=empty('El estado no está disponible','La app no puede confirmar precios ni alertas en este momento.');}
  }
}

function switchTab(value) {
  tab=value;
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-pressed',String(b.dataset.tab===tab));});
  if(data)renderFeed();
}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
$('#filters').addEventListener('click',e=>{const b=e.target.closest('[data-category]');if(!b||!data)return;category=b.dataset.category;renderFilters();renderFeed();});
$('#sort').addEventListener('change',()=>{if(data)renderFeed();});
$('#all-events').addEventListener('click',()=>{switchTab('events');$('#feed-title').scrollIntoView({block:'start',behavior:'smooth'});});
$('#notifications').addEventListener('click',()=>{$(`input[name=level][value="${minimum}"]`).checked=true;$('#preferences').showModal();});
$('#save-channel').addEventListener('click',()=>{
  const value=$('#notification-topic').value.trim();
  if(!/^[A-Za-z0-9_-]{32,160}$/.test(value)){$('#channel-feedback').textContent='Usa el canal personal recibido, de 32 a 160 letras, números, guiones o guiones bajos.';return;}
  notificationTopic=value;
  try{localStorage.setItem('radar-notification-topic',value);}catch{}
  $('#notification-topic').value='';
  $('#channel-feedback').textContent='Canal guardado en este dispositivo. Pulsa Suscribirme en Android y permite las notificaciones en ntfy.';
  notificationLinks();
});
$('#save-preferences').addEventListener('click',()=>{minimum=$('input[name=level]:checked').value;try{localStorage.setItem('radar-minimum',minimum);}catch{}if(data)renderFeed();});
$('#product-detail .close').addEventListener('click',()=>$('#product-detail').close());
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('#feed').addEventListener('click',async e=>{
  const target=e.target.closest('[data-detail]');if(!target||!data)return;
  const p=data.products.find(p=>p.id===target.dataset.detail);if(!p)return;
  const url=productURL(p.url), content=$('#detail-content');
  content.innerHTML=`<div class="eyebrow">EVIDENCIA · TELCEL</div><h2>${escape(p.name)}</h2><p>SKU ${escape(p.sku)} · ${escape(p.category)}</p><div class="detail-price">${money(p.price_cents)} MXN</div><span class="level ${levels[p.anomaly.level]>=3?'high':''}">${escape(p.anomaly.level)}</span><p>${escape(p.anomaly.reason)}</p><ul class="detail-list"><li>Primera observación: ${datetime.format(new Date(p.first_seen))} CDMX</li><li>Última observación: ${datetime.format(new Date(p.last_seen))} CDMX</li><li>El precio anterior publicado no determina el nivel S+ / S++.</li><li>Sin referencia externa: ROI y liquidez aún no calculados.</li></ul><h3>Histórico observado</h3><div id="detail-history"><p>Cargando observaciones…</p></div>${url?`<a class="primary" href="${escape(url)}" target="_blank" rel="noopener noreferrer">Ver oferta en Telcel ↗</a>`:''}`;
  $('#product-detail').showModal();
  if(p.signal){const notice=document.createElement('p');notice.className='detail-signal';notice.textContent=p.signal.label+' · '+p.signal.status+'. '+p.signal.reasons.map(r=>r.detail).join(' · ');content.insertBefore(notice,content.querySelector('ul'));}
  try{const r=await fetch('./data/history.json',{cache:'no-store'});if(!r.ok)throw new Error('History');const h=await r.json();if(!$('#detail-history'))return;$('#detail-history').innerHTML=(h[p.id]||[]).slice(-12).reverse().map(row=>`<div class="history-row"><span>${datetime.format(new Date(row.first))} CDMX<br><small>${row.count} observaciones iguales</small></span><strong>${money(row.price_cents)}</strong></div>`).join('')||'<p>Sin histórico previo.</p>';}catch{if($('#detail-history'))$('#detail-history').textContent='No se pudo consultar el histórico en este momento.';}
});
load();
notificationLinks();
if(openNotificationSetup){$(`input[name=level][value="${minimum}"]`).checked=true;$('#preferences').showModal();}
setInterval(()=>{if(!document.hidden)load();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
window.addEventListener('online',load);
window.addEventListener('offline',load);
if('serviceWorker' in navigator && location.protocol==='https:') navigator.serviceWorker.register('./sw.js',{scope:'./'}).then(r=>r.update()).catch(()=>{});
