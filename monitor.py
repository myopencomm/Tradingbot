from datetime import datetime
import pytz
import portfolio
import prices
import orders

PARIS = pytz.timezone("Europe/Paris")


def check_positions(send_fn) -> None:
    """Scan des positions 4x/jour. Alerte si SL ou TP atteint."""
    data = portfolio.load()
    positions = data.get("positions", {})
    now = datetime.now(PARIS).strftime("%H:%M")

    print(f"\n[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Scan positions...")

    if not positions:
        send_fn(f"⚠️ STATUS {now} — Aucune position active.")
        return

    alerts = []
    status_lines = [f"📊 STATUS {now}"]

    for name, cfg in positions.items():
        quote = prices.get_quote(cfg["ticker"])
        price = quote.get("price")

        if price is None:
            status_lines.append(f"  ⚠️ {name}: prix indisponible")
            continue

        change_pct = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
        pnl = (price - cfg["entry_price"]) * cfg["qty"]
        icon = "📈" if change_pct >= 0 else "📉"
        status_lines.append(
            f"  {icon} {name}: {price}€ ({change_pct:+.2f}%) | P&L: {pnl:+.0f}€"
            f"\n     SL {cfg['target_low']}€ — TP {cfg['target_high']}€"
        )

        if price >= cfg["target_high"]:
            alerts.append({"type": "TP", "name": name, "cfg": cfg, "price": price, "change": change_pct, "pnl": pnl})
        elif price <= cfg["target_low"]:
            alerts.append({"type": "SL", "name": name, "cfg": cfg, "price": price, "change": change_pct, "pnl": pnl})

    for a in alerts:
        cfg = a["cfg"]
        if a["type"] == "TP":
            msg = (
                f"🎯 TAKE-PROFIT ATTEINT — {a['name']}\n\n"
                f"Prix: {a['price']}€ ({a['change']:+.2f}%)\n"
                f"P&L: {a['pnl']:+.0f}€\n\n"
                + orders.take_profit(cfg["ticker"], cfg["qty"], cfg["target_high"])
                + "\n\n💡 Vente conseillée. Confirme sur Bourse Direct."
            )
        else:
            msg = (
                f"🚨 STOP-LOSS PROCHE — {a['name']}\n\n"
                f"Prix: {a['price']}€ ({a['change']:+.2f}%)\n"
                f"P&L: {a['pnl']:+.0f}€\n"
                f"Seuil SL: {cfg['target_low']}€\n\n"
                f"⚠️ Vérifier que votre ordre stop est actif sur Bourse Direct.\n\n"
                + orders.stop_loss(cfg["ticker"], cfg["qty"], cfg["target_low"])
            )
        send_fn(msg)

    if not alerts:
        send_fn("\n".join(status_lines))
        print("✅ Status envoyé — pas d'alerte")
