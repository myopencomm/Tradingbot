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
        sym = prices.currency_symbol(quote.get("currency", "EUR"))

        if price is None:
            if quote.get("status") in ("suspended", "error"):
                status_lines.append(f"  ⛔ {name}: COURS SUSPENDU — non vendable")
            else:
                status_lines.append(f"  ⚠️ {name}: prix indisponible")
            continue

        change_pct = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
        pnl = (price - cfg["entry_price"]) * cfg["qty"]
        icon = "📈" if change_pct >= 0 else "📉"
        status_lines.append(
            f"  {icon} {name}: {sym}{price} ({change_pct:+.2f}%) | P&L: {sym}{pnl:+.0f}"
            f"\n     SL {sym}{cfg['target_low']} — TP {sym}{cfg['target_high']}"
        )

        if price >= cfg["target_high"]:
            alerts.append({"type": "TP", "name": name, "cfg": cfg, "price": price, "change": change_pct, "pnl": pnl, "sym": sym})
        elif price <= cfg["target_low"]:
            # SL already breached — no order instructions, just flag it
            alerts.append({"type": "SL_BREACH", "name": name, "cfg": cfg, "price": price, "change": change_pct, "pnl": pnl, "sym": sym})
        elif price <= cfg["target_low"] * 1.05:
            # Approaching SL (within 5%) — send instructions
            alerts.append({"type": "SL_PROCHE", "name": name, "cfg": cfg, "price": price, "change": change_pct, "pnl": pnl, "sym": sym})

    for a in alerts:
        cfg = a["cfg"]
        sym = a["sym"]
        if a["type"] == "TP":
            msg = (
                f"🎯 TAKE-PROFIT ATTEINT — {a['name']}\n\n"
                f"Prix: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n\n"
                + orders.take_profit(cfg["ticker"], cfg["qty"], cfg["target_high"])
                + "\n\n💡 Vente conseillée. Confirme sur Bourse Direct."
            )
        elif a["type"] == "SL_BREACH":
            msg = (
                f"🚨 STOP-LOSS DÉPASSÉ — {a['name']}\n\n"
                f"Prix: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n"
                f"SL: {sym}{cfg['target_low']}\n\n"
                f"Analyse nécessaire pour décision — /research {cfg['ticker']}"
            )
        else:
            msg = (
                f"⚠️ STOP-LOSS PROCHE — {a['name']}\n\n"
                f"Prix: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n"
                f"Seuil SL: {sym}{cfg['target_low']}\n\n"
                f"Vérifier que votre ordre stop est actif sur Bourse Direct.\n\n"
                + orders.stop_loss(cfg["ticker"], cfg["qty"], cfg["target_low"])
            )
        send_fn(msg)

    if not alerts:
        send_fn("\n".join(status_lines))
        print("✅ Status envoyé — pas d'alerte")
