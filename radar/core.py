from __future__ import annotations

import copy
import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

LEVELS = {"WATCH": 0, "B": 1, "A": 2, "S+": 3, "S++": 4}
CATEGORIES = ("Celulares", "Consolas", "Tablets", "Laptops", "TVs", "Controles", "Audífonos", "Relojes", "Otros")


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hot_window(at):
    hour = dt(at).astimezone(ZoneInfo("America/Mexico_City")).hour
    return 2 <= hour < 5 or 14 <= hour < 18


def due(last, at):
    return not last or (dt(at) - dt(last)).total_seconds() >= (240 if hot_window(at) else 840)


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


def safe_url(url):
    try:
        u = urlsplit(url)
        return u.scheme == "https" and u.hostname == "www.telcel.com" and u.port in (None, 443) and not u.username and u.path.startswith("/tienda/producto/")
    except (ValueError, TypeError):
        return False


def validate(p):
    p = copy.deepcopy(p)
    if not str(p.get("sku", "")).isdigit() or not p.get("name") or not safe_url(p.get("url")):
        raise ValueError("Identidad o URL de producto inválida")
    price = p.get("price_cents")
    if isinstance(price, bool) or not isinstance(price, int) or not 0 < price <= 100_000_000:
        raise ValueError("Precio inválido; no se admiten cero, cuotas ni valores estimados")
    old = p.get("listed_old_price_cents")
    if old is not None and (not isinstance(old, int) or old <= price):
        p["listed_old_price_cents"] = None
    if p.get("category") not in CATEGORIES or p.get("availability") not in ("in_stock", "out_of_stock", "unknown"):
        raise ValueError("Categoría o disponibilidad inválida")
    if not p.get("surface"):
        raise ValueError("Falta superficie de origen")
    p["id"] = "telcel:" + str(p["sku"])
    p["source"] = "Telcel"
    p["currency"] = "MXN"
    p["condition"] = "new"
    return p


def initial_state():
    return {"products": {}, "history": {}, "events": [], "alerts": [], "delivery": {}, "outbox": {}, "last_attempt": None, "last_success": None, "successful_scans": 0}


def score(p, hist, at):
    # Independent observed history only. An advertised strikethrough is never a baseline.
    cutoff = dt(at) - timedelta(days=14)
    daily = {}
    observations = 0
    for row in hist:
        if dt(row["last"]) < cutoff:
            continue
        observations += row["count"]
        first = max(dt(row["first"]), cutoff)
        last = dt(row["last"])
        cursor = first.date()
        while cursor <= last.date():
            daily[cursor.isoformat()] = row["price_cents"]
            cursor += timedelta(days=1)
    prior_days = [v for k, v in daily.items() if k < dt(at).date().isoformat()]
    baseline = statistics.median(prior_days) if prior_days else (hist[-1]["price_cents"] if hist else None)
    age_days = (dt(at) - dt(hist[0]["first"])).total_seconds() / 86400 if hist else 0
    drop = round(100 * (1 - p["price_cents"] / baseline), 1) if baseline else 0
    level = "WATCH"
    if drop >= 15:
        level = "B"
    if drop >= 30:
        level = "A"
    mature = age_days >= 1 and observations >= 6 and p["availability"] == "in_stock" and p.get("price_basis", "exact") == "exact"
    if mature and drop >= 50:
        level = "S+"
    if mature and age_days >= 7 and len(prior_days) >= 7 and drop >= 80:
        level = "S++"
    reason = f"Caída del {drop:g}% frente al histórico propio" if drop > 0 else "Sin baja frente al histórico propio" if hist else "Primera observación; sin referencia propia todavía"
    if level == "S++":
        reason = f"Anomalía extrema: {drop:g}% bajo el histórico propio de al menos 7 días"
    return {"level": level, "drop_pct": max(0, drop), "baseline_cents": baseline, "reason": reason, "history_days": round(age_days, 1), "mode": "telcel_anomaly", "confidence": "histórico" if mature else "inicial"}


def ingest(state, observations, surfaces, at, rules=None):
    """Surface complete=True means all pages/variants, not a partial rotation."""
    s = copy.deepcopy(state)
    validated = [validate(p) for p in observations]
    if len({p["id"] for p in validated}) != len(validated):
        raise ValueError("SKU duplicado: no se mezclan variantes")
    seen = set()
    fresh_events = []

    def event(kind, p=None, **details):
        row = {"id": stable_id(at, kind, (p or {}).get("id"), details), "type": kind, "at": at, "product_id": (p or {}).get("id"), "name": (p or {}).get("name"), **details}
        fresh_events.append(row)

    for p in validated:
        pid = p["id"]
        seen.add(pid)
        prev = s["products"].get(pid)
        hist = s["history"].setdefault(pid, [])
        p["first_seen"] = prev["first_seen"] if prev else at
        p["last_seen"] = at
        p["missing_count"] = 0
        p["present"] = True
        p["episode"] = (prev or {}).get("episode", 0)
        if not prev:
            event("NEW_PRODUCT", p)
        else:
            if not prev.get("present", True) or (prev["availability"] == "out_of_stock" and p["availability"] == "in_stock"):
                p["episode"] += 1
                event("REAPPEARED", p)
            if p["price_cents"] != prev["price_cents"]:
                event("PRICE_DROP" if p["price_cents"] < prev["price_cents"] else "PRICE_INCREASE", p, before_cents=prev["price_cents"], after_cents=p["price_cents"])
            # Unknown promotion is not proof it ended.
            promo, oldpromo = p.get("promotion"), prev.get("promotion")
            if promo is not None and oldpromo is not None and bool(promo) != bool(oldpromo):
                event("PROMO_ON" if promo else "PROMO_OFF", p)
            if p.get("listed_old_price_cents") != prev.get("listed_old_price_cents"):
                event("LISTED_OLD_PRICE_CHANGE", p)
        p["anomaly"] = score(p, hist, at)
        signature = [p["price_cents"], p.get("promotion"), p.get("listed_old_price_cents"), p["availability"]]
        if hist and hist[-1]["signature"] == signature:
            hist[-1]["last"] = at
            hist[-1]["count"] += 1
        else:
            hist.append({"first": at, "last": at, "count": 1, "price_cents": p["price_cents"], "signature": signature})
        s["history"][pid] = [h for h in hist if dt(h["last"]) >= dt(at) - timedelta(days=180)][-600:]
        s["products"][pid] = p

    complete = {x["id"] for x in surfaces if x.get("complete") and x.get("status") == "OK"}
    absence_scopes = {x['id']: set(x['absence_checked_ids']) for x in surfaces if 'absence_checked_ids' in x}
    for pid, p in list(s["products"].items()):
        if pid in seen or p["surface"] not in complete:
            continue
        if p['surface'] in absence_scopes and pid not in absence_scopes[p['surface']]:
            continue
        p["missing_count"] = p.get("missing_count", 0) + 1
        if p["missing_count"] >= 3 and p.get("present", True):
            p["present"] = False
            event("DISAPPEARED", p)
        if dt(p["last_seen"]) < dt(at) - timedelta(days=180):
            del s["products"][pid]
            s["history"].pop(pid, None)
            s["delivery"].pop(pid, None)
    for surface in surfaces:
        if surface["status"] not in ("OK", "DEFERRED"):
            event("SOURCE_ERROR", surface=surface["id"], reason=surface.get("error", "Fuente incompleta"))
    s["events"] = (fresh_events[::-1] + s["events"])[:1500]
    from .alerts import evaluate, prepare_outbox, settings
    rules = settings(rules)
    s["alerts"] = []
    for p in s["products"].values():
        fresh = (dt(at) - dt(p["last_seen"])).total_seconds() < rules["freshness_minutes"] * 60
        p["signal"] = evaluate(p, rules)
        if p.get("present") and fresh and p["signal"]:
            s["alerts"].append({"id": stable_id(p["id"], p["price_cents"], p["signal"], p["episode"]), "product_id": p["id"], "at": p["last_seen"]})
    s["alerts"].sort(key=lambda a: (s["products"][a["product_id"]]["signal"]["priority"], LEVELS[s["products"][a["product_id"]]["anomaly"]["level"]], s["products"][a["product_id"]]["anomaly"]["drop_pct"]), reverse=True)
    prepare_outbox(s, at, rules)
    s["last_attempt"] = at
    if surfaces and all(x["status"] == "OK" and x.get("complete") for x in surfaces):
        s["last_success"] = at
        s["successful_scans"] += 1
    return s


def should_notify(p, previous):
    if LEVELS[p["anomaly"]["level"]] < 3:
        return False
    if not previous:
        return True
    return (p["price_cents"] <= previous["price_cents"] * .97
            or LEVELS[p["anomaly"]["level"]] > LEVELS[previous["level"]]
            or p["episode"] > previous["episode"]
            or (p["anomaly"]["confidence"] == "histórico" and previous["confidence"] != "histórico"))


def economic_score(quick_sale_cents, acquisition_cents, resale_costs_cents, liquid, confidence, extreme=False):
    """Future module. Acquisition includes landed cost; never double count fees."""
    if acquisition_cents <= 0 or min(quick_sale_cents, resale_costs_cents) < 0:
        raise ValueError("Costos inválidos")
    profit = quick_sale_cents - acquisition_cents - resale_costs_cents
    roi = profit / acquisition_cents
    level = "WATCH"
    if liquid and roi >= .25:
        level = "B"
    if liquid and confidence >= .8 and roi >= .5:
        level = "A"
    if liquid and confidence >= .8 and roi >= .75:
        level = "S++" if extreme else "S+"
    return {"roi_on_cost": roi, "profit_cents": profit, "level": level}
