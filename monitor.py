from datetime import datetime
import pytz
import portfolio
import prices
import orders

PARIS = pytz.timezone("Europe/Paris")


def check_pending_orders(send_fn) -> None:
    """Vérifie les ordres en attente sur le range intraday des 4 dernières heures."""
    pending = portfolio.get_pending_orders()
    if not pending:
        return

    for name, cfg in pending.items():
        rng   = prices.get_intraday_range(cfg["ticker"], hours=4)
        if not rng:
            continue

        entry   = cfg["entry_price"]
        current = rng["current"]
        low_4h  = rng["low"]
        high_4h = rng["high"]

        # Le low des 4 dernières heures a touché ou passé le prix d'entrée
        if low_4h <= entry:
            touched = low_4h
            note    = "" if current <= entry else f" (cours actuel revenu à {current}€)"
            send_fn(
                f"🟢 ORDRE DÉCLENCHÉ — {name}\n\n"
                f"Le cours a touché {touched}€ ces 4 dernières heures\n"
                f"(entrée cible: {entry}€){note}\n\n"
                f"Vérifie sur Bourse Direct si l'ordre est passé.\n"
                f"Si oui, enregistre ici :\n"
                f"/add {name} {cfg['ticker']} {cfg['qty']} {entry} "
                f"{cfg['target_low']} {cfg['target_high']}"
            )
        elif ((current - entry) / entry) * 100 > 15:
            drift_pct = ((current - entry) / entry) * 100
            send_fn(
                f"⚠️ ORDRE EXPIRANT — {name}\n\n"
                f"Cours actuel: {current}€ (+{drift_pct:.1f}% vs entrée {entry}€)\n"
                f"Le cours s'éloigne. La stratégie est peut-être dépassée.\n\n"
                f"→ /annuler {name}  (libère {cfg['reserved_cash']:.0f}€)\n"
                f"→ /research {cfg['ticker']}  (réévaluer)"
            )


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
        sym   = prices.currency_symbol(quote.get("currency", "EUR"))

        if price is None:
            if quote.get("status") in ("suspended", "error"):
                status_lines.append(f"  ⛔ {name}: COURS SUSPENDU — non vendable")
            else:
                status_lines.append(f"  ⚠️ {name}: prix indisponible")
            continue

        change_pct = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
        pnl        = (price - cfg["entry_price"]) * cfg["qty"]
        icon       = "📈" if change_pct >= 0 else "📉"
        status_lines.append(
            f"  {icon} {name}: {sym}{price} ({change_pct:+.2f}%) | P&L: {sym}{pnl:+.0f}"
            f"\n     SL {sym}{cfg['target_low']} — TP {sym}{cfg['target_high']}"
        )

        # Range intraday des 4 dernières heures pour détecter les franchissements entre checks
        rng    = prices.get_intraday_range(cfg["ticker"], hours=4)
        high4h = rng.get("high", price)
        low4h  = rng.get("low",  price)

        if high4h >= cfg["target_high"]:
            alerts.append({"type": "TP", "name": name, "cfg": cfg,
                           "price": price, "trigger": high4h,
                           "change": change_pct, "pnl": pnl, "sym": sym})
        elif low4h <= cfg["target_low"]:
            if not cfg.get("sl_breach_notified", False):
                alerts.append({"type": "SL_BREACH", "name": name, "cfg": cfg,
                               "price": price, "trigger": low4h,
                               "change": change_pct, "pnl": pnl, "sym": sym})
        elif low4h <= cfg["target_low"] * 1.05:
            alerts.append({"type": "SL_PROCHE", "name": name, "cfg": cfg,
                           "price": price, "trigger": low4h,
                           "change": change_pct, "pnl": pnl, "sym": sym})

    sent_any = False
    for a in alerts:
        cfg = a["cfg"]
        sym = a["sym"]
        trigger = a.get("trigger", a["price"])
        intraday_note = (
            f" (atteint {sym}{trigger} ces 4h, actuel {sym}{a['price']})"
            if trigger != a["price"] else ""
        )

        if a["type"] == "TP":
            msg = (
                f"🎯 TAKE-PROFIT ATTEINT — {a['name']}\n\n"
                f"Seuil TP: {sym}{cfg['target_high']}{intraday_note}\n"
                f"Cours actuel: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n\n"
                + orders.take_profit(cfg["ticker"], cfg["qty"], cfg["target_high"])
                + "\n\n💡 Vente conseillée. Confirme sur Bourse Direct."
            )
        elif a["type"] == "SL_BREACH":
            msg = (
                f"🚨 STOP-LOSS DÉPASSÉ — {a['name']}\n\n"
                f"Seuil SL: {sym}{cfg['target_low']}{intraday_note}\n"
                f"Cours actuel: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n\n"
                f"Analyse nécessaire pour décision — /research {cfg['ticker']}\n\n"
                f"Cette alerte ne sera plus répétée. Revue mensuelle le 1er du mois."
            )
            send_fn(msg)
            portfolio.mark_sl_breach(a["name"])
            sent_any = True
            continue
        else:
            msg = (
                f"⚠️ STOP-LOSS PROCHE — {a['name']}\n\n"
                f"Low 4h: {sym}{trigger} — Seuil SL: {sym}{cfg['target_low']}\n"
                f"Cours actuel: {sym}{a['price']} ({a['change']:+.2f}%)\n"
                f"P&L: {sym}{a['pnl']:+.0f}\n\n"
                f"Vérifier que l'ordre stop est actif sur Bourse Direct.\n\n"
                + orders.stop_loss(cfg["ticker"], cfg["qty"], cfg["target_low"])
            )
        send_fn(msg)
        sent_any = True

    if not sent_any:
        send_fn("\n".join(status_lines))
        print("✅ Status envoyé — pas d'alerte")
