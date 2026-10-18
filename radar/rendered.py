"""Runs in GitHub Actions, using only public pages and visible DOM.

No endpoint discovery, private APIs, cookies from accounts, stealth flags,
CAPTCHA solving or proxy rotation. Stop on a source-served access challenge.
"""
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .core import now_iso, score
from .parser import SourceError, category, confirm_detail, parse_card
from .scan import PublicClient, permitted, rules_for

CARDS = 'cx-custom-product-grid-item'
NEXT = 'cx-pagination a:has(.cx-icon-telcel-arrow-right):not(.disabled)'
EXTRACT_CARDS = """els => els.map(e=>({
 url:e.querySelector('a.cx-product-name[href]')?.href || e.querySelector('a[href*="/tienda/producto/"]')?.href || '',
 name:e.querySelector('.a-product-name')?.textContent || '',
 prices:Array.from(e.querySelectorAll('custom-one-time-charge')).map(x=>x.innerText.trim()),
 old:e.querySelector('.price-list')?.textContent?.trim() || '',
 promotion:e.querySelector('.promocion')?.textContent || '',
 basis:e.querySelector('.cx-amigo')?.textContent || '',
 brand:e.querySelector('.cx-product-model')?.textContent || '',
 image:e.querySelector('.cx-product-image img')?.src || ''
}))"""
EXTRACT_DETAIL = """() => ({
 sku:document.querySelector('.sku')?.innerText || '',
 prices:Array.from(document.querySelectorAll('custom-one-time-charge')).map(x=>x.innerText.trim()),
 old:document.querySelector('.discountPrice .discount')?.innerText || '',
 buy_enabled:Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Agregar al carrito' && !e.disabled),
 out_of_stock:document.body.innerText.includes('no contamos con la opción')
})"""


def collect(config, prior):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
    client=PublicClient()
    statuses=[];products={};evidence={'at':now_iso(),'method':'public-rendered-dom','surfaces':{},'parser_verified_on_live_products':False}
    try:
        raw=client.get('https://www.telcel.com/robots.txt',bootstrap=True)
        if 'user-agent:' not in raw.lower():raise SourceError('robots.txt no verificable')
        client.rules=rules_for(raw)
        xml=client.get(config['sitemap'])
        if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():raise SourceError('Sitemap no admitido')
        urls=[e.text for e in ET.fromstring(xml).iter() if e.tag.endswith('}loc')]
    except (SourceError,ET.ParseError) as exc:
        return [],[{'id':s['id'],'name':s['name'],'url':s['url'],'status':'SOURCE_ERROR','complete':False,'detected':0,'discovered':0,'error':str(exc)} for s in config['surfaces']],{**evidence,'error':str(exc)}
    started=time.monotonic();blocked=False
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        context=browser.new_context(viewport={'width':1365,'height':960})
        page=context.new_page()
        page.set_default_timeout(15000)

        def navigate(url):
            nonlocal blocked
            if blocked:raise SourceError('Adaptador detenido por verificación de acceso')
            if not permitted(url,client.rules):raise SourceError('Ruta no permitida por robots')
            if time.monotonic()-started>config.get('render_budget_seconds',230):raise SourceError('Presupuesto de barrido agotado; cobertura parcial')
            response=page.goto(url,wait_until='domcontentloaded',timeout=25000)
            if response and response.status in (403,429):
                blocked=True
                raise SourceError(f'HTTP {response.status}; adaptador detenido sin evasión')
            text=page.locator('body').inner_text(timeout=5000).lower()
            if any(t in text for t in ['verify you are human','checking your browser','access denied','unusual traffic','captcha']):
                blocked=True
                raise SourceError('La fuente exige verificación de acceso; sin reintentos evasivos')
            return response

        for surface in config['surfaces']:
            discovered=[u for u in urls if u and surface['product_path'] in u and permitted(u,client.rules)]
            row={'id':surface['id'],'name':surface['name'],'url':surface['url'],'status':'SOURCE_ERROR','complete':False,'detected':0,'discovered':len(discovered),'pages':0,'absence_checked_ids':[],'coverage':'SKUs expuestos por el catálogo público; variantes adicionales por barridos progresivos'}
            seen_pages=set();local={};next_url=prior.get('source_cursors',{}).get(surface['id']) or surface['url']
            if urlsplit(next_url).path!=urlsplit(surface['url']).path:next_url=surface['url']
            surface_started=time.monotonic()
            try:
                while next_url:
                    if local and time.monotonic()-surface_started>config.get('surface_budget_seconds',40):
                        row.update(status='OK',resume_url=next_url,coverage='Lectura progresiva: continuará desde la siguiente página')
                        break
                    if next_url in seen_pages:raise SourceError('Paginación repetida')
                    if len(seen_pages)>=config.get('max_listing_pages',80):raise SourceError('Límite de páginas alcanzado; cobertura parcial')
                    seen_pages.add(next_url)
                    navigate(next_url)
                    page.locator(CARDS).first.wait_for(state='attached',timeout=15000)
                    # Telcel mounts pagination only after scrolling the public listing.
                    page.locator(CARDS).last.scroll_into_view_if_needed(timeout=8000)
                    page.keyboard.press('End')
                    try:
                        page.locator('cx-pagination').first.wait_for(state='attached',timeout=8000)
                        pagination_verified=True
                    except BrowserTimeout:
                        pagination_verified=False
                    cards=page.locator(CARDS).evaluate_all(EXTRACT_CARDS)
                    parsed=[]
                    for card in cards:
                        try:
                            if '/tienda/producto/' not in urlsplit(card.get('url','')).path:
                                raise SourceError('Tarjeta sin enlace de producto verificable')
                            parsed.append(parse_card(card,surface['id']))
                        except (SourceError,ValueError) as exc:
                            row['rejected_cards']=row.get('rejected_cards',0)+1
                            evidence.setdefault('card_errors',[]).append({'surface':surface['id'],'name':card.get('name',''),'url':card.get('url',''),'reason':str(exc)[:140]})
                    if not parsed:raise SourceError('Página de catálogo vacía')
                    for p in parsed:
                        if p['id'] in local and local[p['id']]['price_cents']!=p['price_cents']:raise SourceError('Un SKU presenta precios contradictorios en el mismo barrido')
                        local[p['id']]=p
                    row['pages']+=1
                    links=page.locator(NEXT)
                    if links.count():
                        next_url=links.first.get_attribute('href')
                        from urllib.parse import urljoin
                        next_url=urljoin(surface['url'],next_url)
                        if urlsplit(next_url).path!=urlsplit(surface['url']).path:raise SourceError('La paginación salió de la superficie')
                    else:
                        if not pagination_verified:
                            row.update(status='OK',resume_url=next_url,coverage='Precios observados; paginación pendiente de verificar')
                            break
                        next_url=None
                    time.sleep(1.2)
                if not next_url:row.update(status='OK',complete=not row.get('rejected_cards'),resume_url=None)
            except (SourceError,BrowserTimeout,BrowserError) as exc:
                row['error']=str(exc).splitlines()[0][:180]
            products.update(local)
            row['detected']=len(local)
            statuses.append(row)
            evidence['surfaces'][surface['id']]={'pages':row['pages'],'products':len(local),'listing_complete':row['complete']}

        # Verify strongest anomalies and a small sample of exact variants each run.
        # A card saying "Lo quiero" is NOT used as proof of inventory.
        def priority(p):
            hist=prior.get('history',{}).get(p['id'],[])
            a=score(p,hist,evidence['at'])
            return (-a['drop_pct'],prior.get('products',{}).get(p['id'],{}).get('detail_checked_at',''))
        candidates=sorted(products.values(),key=priority)[:config.get('detail_checks_per_run',12)]
        checks=0
        for p in candidates:
            try:
                navigate(p['url'])
                page.locator('.sku').wait_for(state='visible',timeout=12000)
                updated=confirm_detail(page.evaluate(EXTRACT_DETAIL),p)
                updated['detail_checked_at']=now_iso()
                products[p['id']]=updated;checks+=1
                time.sleep(1.2)
            except (SourceError,BrowserTimeout,BrowserError) as exc:
                evidence.setdefault('detail_errors',[]).append({'sku':p['sku'],'reason':str(exc).splitlines()[0][:140]})
                if blocked:break
        # Missing from a listing alone can mean a changed representative color.
        # Require a direct 404 for known variants before advancing absence state.
        missing=[p for pid,p in prior.get('products',{}).items() if pid not in products]
        for p in missing[:6]:
            try:
                response=navigate(p['url'])
                if response and response.status==404:
                    next(s for s in statuses if s['id']==p['surface'])['absence_checked_ids'].append(p['id'])
                else:
                    page.locator('.sku').wait_for(state='visible',timeout=10000)
                    products[p['id']]=confirm_detail(page.evaluate(EXTRACT_DETAIL),p)
            except (SourceError,BrowserTimeout,BrowserError):
                if blocked:break
        browser.close()
    evidence['detail_verified']=checks
    evidence['parser_verified_on_live_products']=bool(products)
    evidence['elapsed_seconds']=round(time.monotonic()-started,1)
    evidence['runner_validation']='runtime_executed'
    return list(products.values()),statuses,evidence
