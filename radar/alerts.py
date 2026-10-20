"""Personal alert rules, independent of evidence-based S grades. No secrets."""
import hashlib
import json
import re
from datetime import datetime, timedelta

DEFAULTS = {
    "phone_price_limit_cents": 180000,
    "published_discount_min_pct": 50,
    "published_savings_min_cents": 100000,
    "observed_drop_min_pct": 30,
    "realert_drop_pct": 3,
    "freshness_minutes": 30,
    "max_notifications_per_run": 12,
}
RANK = {"WATCH": 0, "B": 1, "A": 2, "S+": 3, "S++": 4}


def settings(overrides=None):
    result = {**DEFAULTS, **(overrides or {})}
    for k in DEFAULTS:
        if isinstance(result[k], bool) or not isinstance(result[k], (int, float)) or result[k] <= 0:
            raise ValueError(f"Regla inválida: {k}")
    if not 0 < result["realert_drop_pct"] < 100:
        raise ValueError("Realerta fuera de rango")
    return result


def moment(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def evaluate(p, rules=None):
    r = settings(rules)
    if p.get("availability") == "out_of_stock" or not p.get("present", True):
        return None
    caption = p.get("price_caption", "").casefold()
    # A full Amigo Kit price is allowed. Monthly payments/down payments are not.
    if re.search(r"mensual|enganche|al mes|con plan|pospago|renta mensual", caption):
        return None
    a = p.get("anomaly", {})
    price = p["price_cents"]
    old = p.get("listed_old_price_cents") or 0
    discount = 100 * (old - price) / old if old > price else 0
    reasons = []
    handset = p.get("category") == "Celulares" and not re.search(
        r"\bfunda\b|\bcargador\b|\baccesorio\b|\bchip\b|tarjeta sim", p.get("name", ""), re.I)
    if handset and price < r["phone_price_limit_cents"]:
        reasons.append({"code": "PRICE_TARGET", "label": "PRECIO OBJETIVO", "detail": f"Celular debajo de ${r['phone_price_limit_cents']/100:,.0f} MXN"})
    if discount >= r["published_discount_min_pct"] and old - price >= r["published_savings_min_cents"]:
        reasons.append({"code": "PUBLISHED_PROMOTION", "label": "PROMOCIÓN FUERTE", "detail": f"{discount:.1f}% sobre el precio anterior anunciado por Telcel; no es caída observada"})
    if a.get("drop_pct", 0) >= r["observed_drop_min_pct"]:
        reasons.append({"code": "OBSERVED_DROP", "label": "BAJA OBSERVADA", "detail": a["reason"]})
    if RANK.get(a.get("level"), 0) >= 3:
        reasons.insert(0, {"code": "S_ANOMALY", "label": a["level"], "detail": a["reason"]})
    notify = bool(reasons)
    if not reasons and RANK.get(a.get("level"), 0) >= 1:
        reasons.append({"code": "HISTORY_MOVEMENT", "label": a["level"], "detail": a["reason"]})
    if not reasons:
        return None
    confirmed = p.get("price_basis", "exact") == "exact" and p.get("availability") == "in_stock"
    codes = [x["code"] for x in reasons]
    urgent = confirmed and ("PRICE_TARGET" in codes or a.get("level") == "S++")
    return {"label": reasons[0]["label"], "reasons": reasons, "notify": notify,
            "confirmed": confirmed, "status": "CONFIRMADA" if confirmed else "PRELIMINAR",
            "priority": 5 if urgent else 4 if notify else 3,
            "published_discount_pct": round(discount, 1)}


def should_deliver(p, previous, rules=None):
    signal = p.get("signal") or evaluate(p, rules)
    if not signal or not signal["notify"]:
        return False
    if not previous:
        return True
    r = settings(rules)
    codes = {x["code"] for x in signal["reasons"]}
    old_codes = set(previous.get("codes", ["S_ANOMALY"] if RANK.get(previous.get("level"), 0) >= 3 else []))
    was_confirmed = previous.get("confirmed", previous.get("confidence") == "histórico")
    return (p["price_cents"] <= previous["price_cents"] * (1 - r["realert_drop_pct"] / 100)
            or bool(codes - old_codes)
            or p.get("episode", 0) > previous.get("episode", 0)
            or (signal["confirmed"] and not was_confirmed)
            or RANK.get(p.get("anomaly", {}).get("level"), 0) > RANK.get(previous.get("level"), 0))


def prepare_outbox(s, at, rules=None):
    """Replace obsolete pending prices; preserve retries for the same opportunity."""
    r = settings(rules)
    queue = s.setdefault("outbox", {})
    current = {}
    for alert in s["alerts"]:
        p = s["products"][alert["product_id"]]
        if (moment(at) - moment(p["last_seen"])).total_seconds() >= r["freshness_minutes"] * 60:
            continue
        if not should_deliver(p, s.get("delivery", {}).get(p["id"]), r):
            continue
        signal = p["signal"]
        identity = [p["id"], p["price_cents"], p.get("episode", 0),
                    [x["code"] for x in signal["reasons"]], signal["confirmed"]]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        current[key] = queue.get(key, {"product_id": p["id"], "created_at": at, "attempts": 0, "next_attempt": at})
    s["outbox"] = current
    return current


def retry_at(at, attempts):
    return (moment(at) + timedelta(seconds=min(60 * 2 ** min(attempts, 6), 3600))).isoformat(timespec="seconds")
