"""Deliver the committed outbox. Server acceptance is not a device receipt."""
import argparse
import json
import os
import re
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from .alerts import moment, prepare_outbox, retry_at, settings
from .core import now_iso


def message(p, topic):
    signal = p["signal"]
    reasons = "; ".join(x["detail"] for x in signal["reasons"])
    stock = "Disponible al revisar la ficha" if signal["confirmed"] else "Precio o disponibilidad pendientes de confirmar; puede variar"
    seen = moment(p["last_seen"]).astimezone(ZoneInfo("America/Mexico_City")).strftime("%d/%m %H:%M")
    return {"topic": topic, "title": f"RADAR VARO · {signal['label']} · {signal['status']}",
            "message": f"{p['name']} · ${p['price_cents']/100:,.2f} MXN{' desde catálogo' if p.get('price_basis') == 'advertised_from' else ''}. SKU {p['sku']}. {reasons}. {stock}. Observado {seen} CDMX. Reventa sin referencia externa.",
            "priority": signal["priority"], "click": p["url"],
            "tags": ["rotating_light" if signal["priority"] == 5 else "moneybag"]}


def post(payload):
    request = urllib.request.Request("https://ntfy.sh/", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        receipt = json.load(response)
    if not receipt.get("id"):
        raise ValueError("Respuesta sin confirmación del servidor")
    return receipt


def send(s, topic, at=None, rules=None, transport=post, persist=None):
    at = at or now_iso()
    r = settings(rules)
    queue = prepare_outbox(s, at, r)
    report = {"configured": False, "delivered": 0, "errors": 0, "pending": len(queue),
              "device_verified": False, "last_accepted_at": s.get("last_notification_accepted")}
    if not topic:
        return report
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,160}", topic):
        report["errors"] = 1
        return report
    report["configured"] = True
    # Keep urgent confirmed phones first; never release a backlog of expired prices.
    entries = sorted(list(queue), key=lambda key: -s["products"][queue[key]["product_id"]]["signal"]["priority"])
    attempts = 0
    for key in entries:
        item = queue[key]
        if moment(item["next_attempt"]) > moment(at):
            continue
        if attempts >= r["max_notifications_per_run"]:
            break
        attempts += 1
        p = s["products"][item["product_id"]]
        try:
            receipt = transport(message(p, topic))
            if not receipt.get("id"):
                raise ValueError("Respuesta sin confirmación del servidor")
            signal = p["signal"]
            s.setdefault("delivery", {})[p["id"]] = {
                "price_cents": p["price_cents"], "level": p["anomaly"]["level"],
                "episode": p.get("episode", 0), "confidence": p["anomaly"]["confidence"],
                "codes": [x["code"] for x in signal["reasons"]], "confirmed": signal["confirmed"], "at": at}
            s["last_notification_accepted"] = at
            report["last_accepted_at"] = at
            del queue[key]
            report["delivered"] += 1
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            item["attempts"] += 1
            item["next_attempt"] = retry_at(at, item["attempts"])
            item["last_error"] = "Servidor no confirmó el envío; se reintentará si el precio sigue vigente"
            report["errors"] += 1
            # A network error or rate limit should not trigger a burst of requests.
            break
        finally:
            report["pending"] = len(queue)
            if persist:
                persist(s, report)
    return report


def main():
    from .scan import DATA, ROOT, atomic, publish_data, read
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Send a clearly labelled connection test")
    args = parser.parse_args()
    topic = os.environ.get("NTFY_TOPIC", "")
    s = read(DATA / "state.json", {})
    status = read(DATA / "status.json", {})
    evidence = read(DATA / "evidence.json", {})
    rules = read(ROOT / "alert_rules.json", {})

    def persist(state, report):
        publish_data(state, status["surfaces"], evidence, status["automation"], report, rules)

    if args.test:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,160}", topic):
            raise SystemExit("Falta configurar NTFY_TOPIC en GitHub Actions")
        post({"topic": topic, "title": "RADAR VARO · PRUEBA DE CONEXIÓN",
              "message": "Prueba técnica: el emisor funciona. No es una oferta. Comprueba que aparece también con el teléfono bloqueado.",
              "priority": 4, "click": "https://msanz95.github.io/radar-varo/", "tags": ["bell"]})
        status.setdefault("notifications", {})["last_test_accepted_at"] = now_iso()
        persist(s, status["notifications"])
        print("Prueba aceptada por ntfy; la recepción en el teléfono requiere suscripción")
        return
    report = send(s, topic, rules=rules, persist=persist)
    persist(s, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
