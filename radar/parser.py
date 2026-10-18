"""Strict public product parser. No reverse-engineered APIs or invented prices."""
import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .core import validate


class SourceError(Exception):
    pass


def money(value):
    value = str(value).strip().replace("$", "").replace("MXN", "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
        raise SourceError("Importe ambiguo")
    try:
        result = int(Decimal(value) * 100)
    except InvalidOperation as e:
        raise SourceError("Importe inválido") from e
    if result <= 0:
        raise SourceError("Importe no positivo")
    return result


def category(name, path):
    n = name.casefold()
    if "telefonos-y-smartphones" in path:
        return "Celulares"
    for pattern, label in [(r"airpods|buds|aud[ií]fono|headset|headphone", "Audífonos"), (r"watch|reloj|smartband|band \d", "Relojes"), (r"tablet|ipad|matepad", "Tablets"), (r"laptop|macbook|notebook", "Laptops"), (r"gamepad|control|dualsense", "Controles"), (r"xbox|playstation|switch", "Consolas"), (r"televisi[oó]n|smart tv", "TVs")]:
        if re.search(pattern, n):
            return label
    return "Tablets" if "/tablets/" in path else "Otros"


class Document(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.jsonld, self.text, self.links = [], [], []
        self.script = None
        self.skip = 0
        self.feed(raw)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = ""
        elif tag in ("script", "style"):
            self.skip += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            try:
                self.jsonld.append(json.loads(self.script))
            except json.JSONDecodeError as e:
                raise SourceError("JSON-LD inválido") from e
            self.script = None
        elif tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.script += data
        elif not self.skip and data.strip():
            self.text.append(data.strip())


def flatten(node):
    if isinstance(node, list):
        for x in node:
            yield from flatten(x)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from flatten(node[key])


def parse_product(raw, url, surface):
    doc = Document(raw)
    for root in doc.jsonld:
        for obj in flatten(root):
            if obj.get("@type") != "Product":
                continue
            offers = obj.get("offers", {})
            if isinstance(offers, list):
                if len(offers) != 1:
                    raise SourceError("Varias ofertas sin variante inequívoca")
                offers = offers[0]
            if offers.get("@type") == "AggregateOffer" or offers.get("priceCurrency") != "MXN":
                raise SourceError("No hay precio exacto MXN para una variante")
            sku = str(obj.get("sku", ""))
            if sku != urlsplit(url).path.rstrip("/").split("/")[-1]:
                raise SourceError("SKU de ficha no coincide con URL")
            available = str(offers.get("availability", ""))
            image = obj.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if not isinstance(image, str) or not image.startswith("https://www.telcel.com/"):
                image = None
            brand = obj.get("brand", {})
            return validate({"sku": sku, "name": obj["name"], "brand": brand.get("name", "") if isinstance(brand, dict) else str(brand), "url": url, "surface": surface, "category": category(obj["name"], url), "price_cents": money(offers.get("price", "")), "listed_old_price_cents": None, "promotion": None, "availability": "in_stock" if available.endswith("/InStock") else "out_of_stock" if available.endswith(("/OutOfStock", "/Discontinued")) else "unknown", "image": image, "variant": obj.get("color", ""), "parser": "public-jsonld-v1"})
    raise SourceError("HTML sin SKU/precio verificable; requiere validar lectura del DOM público renderizado")


def parse_rendered(snapshot, url, surface):
    """Consumes visible DOM only. Fail closed on multiple full-price candidates."""
    text = snapshot.get("text", "")
    match = re.search(r"\bSKU:\s*(\d+)\b", text)
    if not match or match[1] != urlsplit(url).path.rstrip("/").split("/")[-1]:
        raise SourceError("SKU visible ausente o distinto")
    name = snapshot.get("name", "").strip()
    if not name:
        raise SourceError("Nombre visible ausente")
    section = text[match.end():]
    section = re.split(r"\bo en hasta\b|\bColor:\b|\bCapacidad:", section, maxsplit=1, flags=re.I)[0]
    prices = re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", section)
    if len(prices) != 1:
        raise SourceError("Precio visible ambiguo; necesita revisión de parser")
    stock = "out_of_stock" if "no contamos con la opción" in text else "in_stock" if snapshot.get("buy_enabled") else "unknown"
    return validate({"sku": match[1], "name": name, "brand": "", "url": url, "surface": surface, "category": category(name, url), "price_cents": money(prices[0]), "listed_old_price_cents": None, "promotion": None, "availability": stock, "image": None, "variant": urlsplit(url).path.split("/")[-2], "parser": "public-visible-dom-candidate-v1"})


def parse_card(card, surface):
    """Selectors verified in the public Telcel DOM on 2026-10-12."""
    url=card.get('url','')
    prices=card.get('prices',[])
    if len(prices)!=1 or not card.get('name'):
        raise SourceError('Tarjeta sin precio único o nombre verificable')
    sku=urlsplit(url).path.rstrip('/').split('/')[-1]
    price=money(prices[0])
    old=money(card['old']) if card.get('old') else None
    image=card.get('image')
    if not image or urlsplit(image).hostname!='www.telcel.com':image=None
    return validate({'sku':sku,'name':card['name'].strip(),'brand':card.get('brand','').split('|')[0].strip(),'url':url,'surface':surface,'category':category(card['name'],url),'price_cents':price,'listed_old_price_cents':old,'promotion':card.get('promotion','').strip(),'availability':'unknown','image':image,'variant':urlsplit(url).path.split('/')[-2], 'price_basis':'advertised_from' if 'desde' in card.get('basis','').lower() else 'listing', 'price_caption':card.get('basis','').strip(),'parser':'telcel-visible-card-v1'})


def confirm_detail(snapshot, p):
    """Verify exact SKU and full one-time price. Never interpret installments as price."""
    match=re.fullmatch(r'\s*SKU:\s*(\d+)\s*',snapshot.get('sku',''))
    prices=list(dict.fromkeys(snapshot.get('prices',[])))
    if not match or match[1]!=p['sku'] or len(prices)!=1:
        raise SourceError('La ficha no confirma SKU y precio único')
    result=dict(p)
    result.update(price_cents=money(prices[0]),price_basis='exact',price_caption='Precio de la variante verificado en ficha',availability='out_of_stock' if snapshot.get('out_of_stock') else 'in_stock' if snapshot.get('buy_enabled') else 'unknown')
    result['listed_old_price_cents']=money(snapshot['old']) if snapshot.get('old') else None
    return validate(result)
