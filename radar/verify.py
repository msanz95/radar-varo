"""Static release checks. Does not claim browser or production validation."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from .core import validate


class Markup(HTMLParser):
    def __init__(self, raw):
        super().__init__();self.ids=[];self.references=[];self.feed(raw)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if a.get('id'):self.ids.append(a['id'])
        for key in ('href','src'):
            if a.get(key):self.references.append(a[key])


def main():
    root=Path(__file__).resolve().parents[1]/'web'
    doc=Markup((root/'index.html').read_text())
    assert len(doc.ids)==len(set(doc.ids)), 'Duplicate HTML IDs'
    for ref in doc.references:
        if ref.startswith('#'):assert ref[1:] in doc.ids,ref
        elif ref.startswith('./'):assert (root/ref[2:]).exists(),ref
    data=json.loads((root/'data/dashboard.json').read_text())
    assert data['schema_version']==1
    assert data['status']==json.loads((root/'data/status.json').read_text())
    assert data['products']==json.loads((root/'data/latest.json').read_text())
    assert data['alerts']==json.loads((root/'data/alerts.json').read_text())
    products={p['id']:p for p in data['products']}
    for p in products.values():validate(p)
    for alert in data['alerts']:assert alert['product_id'] in products
    evidence=json.loads((root/'data/evidence.json').read_text())
    assert not products or evidence.get('parser_verified_on_live_products') or all(p.get('parser') and p['last_seen']<=data['generated_at'] for p in products.values()), 'No unverified initial products'
    manifest=json.loads((root/'manifest.webmanifest').read_text())
    assert manifest['scope']=='./' and manifest['start_url']=='./'
    for icon in manifest['icons']:assert (root/icon['src']).exists()
    sw=(root/'sw.js').read_text()
    assert "url.pathname.includes('/data/')" in sw and "cache:'no-store'" in sw
    assert not re.search(r'ntfy\.sh/[A-Za-z0-9_-]{32,}', '\n'.join(p.read_text(errors='ignore') for p in root.rglob('*.json')))
    print(json.dumps({'checks':'PASS','products':len(products),'alerts':len(data['alerts']),'live_scan_complete':data['status']['successful_scans']>0,'browser_qa':'NOT_RUN','production_qa':'NOT_DEPLOYED'}))


if __name__=='__main__':main()
