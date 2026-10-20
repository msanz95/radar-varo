"""CI-only browser QA; no production data are replaced or injected."""
import contextlib
import functools
import http.server
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

def main():
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]
    output=root/'qa-results';output.mkdir(exist_ok=True)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*_args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',8765),functools.partial(Handler,directory=str(root/'web')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            for width,height in [(360,800),(390,844),(1365,960)]:
                page=browser.new_page(viewport={'width':width,'height':height})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:8765/',wait_until='networkidle')
                page.get_by_role('heading',name='SISTEMA AUTÓNOMO').wait_for()
                assert page.locator('#system-state').inner_text() not in ('…',''), 'Status not loaded'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'Overflow at {width}'
                page.get_by_role('button',name='Telcel',exact=True).click()
                assert 'Productos observados' in page.locator('#feed-title').inner_text()
                page.get_by_role('button',name='Movimientos',exact=True).click()
                assert 'Movimientos del radar' in page.locator('#feed-title').inner_text()
                page.get_by_role('button',name='Preferencias de alertas').click()
                page.get_by_label('S++ · Sólo anomalías extremas').check()
                page.get_by_role('button',name='Guardar preferencia').click()
                page.reload(wait_until='networkidle')
                # Price-target / published-promotion alerts bypass the historical S++ filter.
                snapshot=json.loads((root/'web/data/dashboard.json').read_text())
                protected=[p for p in snapshot['products'] if p.get('signal') and any(r['code'] in ('PRICE_TARGET','PUBLISHED_PROMOTION') for r in p['signal']['reasons']) and p.get('present') and (datetime.now(timezone.utc)-datetime.fromisoformat(p['last_seen'])).total_seconds()<1800]
                for p in protected:
                    assert page.locator(f'[data-detail="{p["id"]}"]').count()==1, 'Personal alert hidden by S++ filter'
                if protected:
                    assert page.locator('.signal-state').first.inner_text(), 'Missing preliminary/confirmed evidence'
                page.get_by_role('button',name='Preferencias de alertas').click()
                assert page.locator('#alert-rules').inner_text().find('$1,800')>=0
                assert page.locator('#save-channel').is_visible()
                page.locator('#preferences').get_by_role('button',name='Cerrar').click()
                page.get_by_role('button',name='Preferencias de alertas').click()
                assert page.get_by_label('S++ · Sólo anomalías extremas').is_checked()
                page.locator('#preferences').get_by_role('button',name='Cerrar').click()
                test_topic='browser_test_topic_no_real_messages_123456'
                page.goto('http://127.0.0.1:8765/#notify='+test_topic,wait_until='networkidle')
                assert page.locator('#preferences').is_visible(), 'Phone setup did not open'
                assert page.locator('#subscribe-phone').get_attribute('href')=='ntfy://ntfy.sh/'+test_topic
                assert page.evaluate('location.hash')=='#notify='+test_topic, 'Setup URL must remain usable until the user confirms'
                page.get_by_role('button',name='Guardar preferencia').click()
                assert page.evaluate('location.hash')=='', 'Channel fragment retained after user confirmation'
                assert not errors,errors
                page.screenshot(path=str(output/f'viewport-{width}.png'),full_page=True)
                results.append({'viewport':f'{width}x{height}','overflow':False,'tabs':'PASS','preferences_persist':'PASS','javascript_errors':errors})
                page.close()
            browser.close()
    finally:
        server.shutdown()
    (output/'browser-qa.json').write_text(json.dumps(results,indent=2))
    print(json.dumps({'browser_checks':'PASS','viewports':len(results)}))

if __name__=='__main__':main()
