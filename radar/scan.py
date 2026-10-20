"""Public HTTP collector. A JavaScript shell is a SOURCE_ERROR, never zero stock."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .core import due, hot_window, ingest, initial_state, now_iso
from .parser import Document, SourceError, parse_product

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"
UA = "RADARVARO/0.1 (public product price observer)"


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def read(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def rules_for(raw, agent=UA):
    groups, agents, rules = [], [], []
    for line in raw.splitlines() + ["User-agent: __end__"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = [s.strip() for s in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
        elif k in ("allow", "disallow") and v:
            rules.append((k, v))
    selected = [(max((len(a) for a in aa if a != "*" and a in agent.lower()), default=0), rr) for aa, rr in groups if "*" in aa or any(a in agent.lower() for a in aa)]
    specificity = max([a for a, _ in selected], default=-1)
    return [r for spec, rr in selected if spec == specificity for r in rr]


def permitted(url, rules):
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname != "www.telcel.com" or u.username or u.port not in (None, 443):
        return False
    path = u.path + ("?" + u.query if u.query else "")
    matches = []
    for kind, pattern in rules:
        expr = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\$", "$")
        if re.search(expr, path):
            matches.append((len(pattern.replace("*", "")), kind == "allow"))
    return max(matches, default=(0, True))[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


class PublicClient:
    def __init__(self):
        self.rules = []
        self.last_request = 0
        self.count = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, url, bootstrap=False):
        if not permitted(url, self.rules):
            raise SourceError("Ruta rechazada por directrices de la fuente")
        delay = 1.2 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,text/plain"})
            with self.opener.open(req, timeout=20) as r:
                content = r.read(8_000_001)
                if len(content) > 8_000_000:
                    raise SourceError("Respuesta demasiado grande")
                data = content.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise SourceError(f"HTTP {e.code}; sin reintento evasivo") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise SourceError("La fuente no respondió dentro del límite") from e
        finally:
            self.last_request = time.monotonic()
            self.count += 1
        lower = data.lower()
        if any(x in lower for x in ("verify you are human", "access denied", "captcha challenge", "checking your browser")):
            raise SourceError("La fuente exige validación de acceso; adaptador detenido")
        return data


def collect(config):
    client = PublicClient()
    statuses, products, discovered = [], [], {}
    evidence = {"at": now_iso(), "method": "public-http", "parser_verified_on_live_products": False}
    try:
        robots = client.get("https://www.telcel.com/robots.txt", bootstrap=True)
        if "user-agent:" not in robots.lower():
            raise SourceError("robots.txt no verificable")
        client.rules = rules_for(robots)
        evidence["robots_sha256"] = hashlib.sha256(robots.encode()).hexdigest()
        sitemap = client.get(config["sitemap"])
        if "<!DOCTYPE" in sitemap.upper() or "<!ENTITY" in sitemap.upper():
            raise SourceError("Sitemap no admitido")
        urls = [e.text for e in ET.fromstring(sitemap).iter() if e.tag.endswith("}loc") or e.tag == "loc"]
        for surface in config["surfaces"]:
            discovered[surface["id"]] = sorted({u for u in urls if u and surface["product_path"] in u and permitted(u, client.rules)})
        evidence["sitemap_count"] = sum(len(v) for v in discovered.values())
    except (SourceError, ET.ParseError) as e:
        return [], [{"id": x["id"], "name": x["name"], "status": "SOURCE_ERROR", "complete": False, "error": str(e), "detected": 0, "discovered": 0} for x in config["surfaces"]], {**evidence, "error": str(e)}
    for surface in config["surfaces"]:
        row = {"id": surface["id"], "name": surface["name"], "url": surface["url"], "discovered": len(discovered[surface["id"]]), "detected": 0, "complete": False, "status": "SOURCE_ERROR"}
        try:
            body = client.get(surface["url"])
            doc = Document(body)
            links = sorted({urljoin(surface["url"], x) for x in doc.links if surface["product_path"] in x})
            evidence.setdefault("surfaces", {})[surface["id"]] = {"html_bytes": len(body.encode()), "sha256": hashlib.sha256(body.encode()).hexdigest(), "public_product_links": len(links)}
            # No guessing selectors or treating a JS shell as a successful scan.
            if not links:
                raise SourceError("El catálogo carga con JavaScript; HTTP no expone productos verificables")
            if len(links) < len(discovered[surface["id"]]):
                raise SourceError("Cobertura de catálogo incompleta; paginación pública pendiente de validar")
            if len(links) > config["max_product_requests"]:
                raise SourceError("Catálogo supera presupuesto de lectura; requiere adaptador de listados validado")
            batch = []
            for url in links:
                batch.append(parse_product(client.get(url), url, surface["id"]))
            if not batch:
                raise SourceError("Barrido vacío no verificable")
            products.extend(batch)
            row.update(status="OK", detected=len(batch), complete=True)
        except SourceError as e:
            row["error"] = str(e)
        statuses.append(row)
    evidence["requests"] = client.count
    evidence["parser_verified_on_live_products"] = bool(products)
    return products, statuses, evidence


def notify(s):
    from .delivery import send
    return send(s, os.environ.get("NTFY_TOPIC", ""), rules=read(ROOT / "alert_rules.json", {}))


def publish_data(s, surfaces, evidence, automation, push, rules=None):
    at = s["last_attempt"]
    good = sum(x["status"] == "OK" for x in surfaces)
    health = "OK" if good == len(surfaces) and surfaces and all(x.get('complete') for x in surfaces) else "PARTIAL" if good else "SOURCE_ERROR"
    from .alerts import settings
    status = {"state": health, "automation": automation, "last_attempt": at, "last_success": s["last_success"], "successful_scans": s["successful_scans"], "hot_window": hot_window(at), "surfaces": surfaces, "notifications": push, "rules": settings(rules), "schedule": {"timezone": "America/Mexico_City", "hot_windows": ["02:00–04:59", "14:00–17:59"], "hot_minutes": 5, "normal_minutes": 15}, "mode": "telcel_anomaly", "version": "0.2.0"}
    # A single dashboard bundle prevents cross-generation JSON inconsistencies in the UI.
    dashboard = {"schema_version": 1, "generated_at": at, "status": status, "products": list(s["products"].values()), "alerts": s["alerts"], "events": s["events"][:150]}
    atomic(DATA / "state.json", s)
    atomic(DATA / "latest.json", list(s["products"].values()))
    atomic(DATA / "history.json", s["history"])
    atomic(DATA / "events.json", s["events"])
    atomic(DATA / "missing.json", {k: {"count": v["missing_count"], "present": v["present"], "episode": v["episode"]} for k, v in s["products"].items()})
    atomic(DATA / "alerts.json", s["alerts"])
    atomic(DATA / "status.json", status)
    atomic(DATA / "evidence.json", evidence)
    atomic(DATA / "dashboard.json", dashboard)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Development/first-run only; not a user scan control")
    parser.add_argument("--rendered", action="store_true", help="Read the public rendered catalog in the deployed runner")
    args = parser.parse_args()
    s = read(DATA / "state.json", initial_state())
    at = now_iso()
    if not args.force and not due(s.get("last_attempt"), at):
        print("SKIP: ejecución reciente")
        return
    config = read(ROOT / "sources.json", {})
    rules = read(ROOT / "alert_rules.json", {})
    if args.rendered:
        from .rendered import collect as rendered_collect
        observations, surfaces, evidence = rendered_collect(config,s,rules)
    else:
        observations, surfaces, evidence = collect(config)
    s = ingest(s, observations, surfaces, at, rules)
    s.setdefault('detail_attempts',{}).update(evidence.get('detail_attempts',{}))
    s['detail_attempts']={k:v for k,v in s['detail_attempts'].items() if k in s['products']}
    s['source_cursors']={x['id']:x.get('resume_url') for x in surfaces}
    automation = "github_actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "not_deployed"
    # The workflow commits this outbox BEFORE running radar.delivery.
    # Topic/token never enter the snapshot. Failed deliveries remain queued.
    publish_data(s, surfaces, evidence, automation, {"configured": bool(os.environ.get("NTFY_TOPIC")), "delivered": 0, "errors": 0, "pending": len(s["outbox"]), "device_verified": False, "last_accepted_at": s.get("last_notification_accepted")}, rules)
    print(json.dumps({"observed": len(observations), "surfaces": [{"id": x["id"], "status": x["status"], "discovered": x["discovered"], "detected": x["detected"]} for x in surfaces], "automatic": automation == "github_actions", "complete": all(x.get("complete") for x in surfaces)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
