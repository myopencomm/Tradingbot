from datetime import datetime
import pytz
import market
import portfolio
import position_view
import prices
import orders
from config import TP_ALERTS, BREAKEVEN_THRESHOLD

PARIS = pytz.timezone("Europe/Paris")

_is_us = market.is_us      # source unique : market.py


def _sl_proche_level(cfg: dict) -> float:
    """
    Cours à partir duquel on prévient que le SL approche.

    L'ancienne règle — SL + 5% — était inutilisable dès que le SL était plus
    serré que 5% : la zone d'alerte englobait le PRU, donc l'alerte partait à
    la SECONDE où la position s'ouvrait. Avec un SL à 2×ATR, c'est le cas de
    toute valeur dont l'ATR est sous ~2,5% (JNJ, 29/07/2026 : SL à -4,6%, zone
    d'alerte à +0,2% au-dessus du PRU).

    Nouvelle règle : le plus BAS des deux seuils — SL + 5%, ou les deux tiers
    du chemin parcouru du PRU vers le SL. Le second borne le premier sous le
    PRU, quelle que soit la largeur du SL.
    """
    sl = cfg.get("target_low") or 0
    entry = cfg.get("entry_price") or 0
    band = sl * 1.05
    if entry > sl > 0:
        band = min(band, entry - 0.67 * (entry - sl))
    return band


def check_pending_orders(send_fn, us_only: bool = False) -> None:
    """Vérifie les ordres en attente sur le range intraday des 4 dernières heures.
    us_only : ne traite que les tickers US (séance US, après clôture Euronext)."""
    pending = portfolio.get_pending_orders()
    if not pending:
        return

    for name, cfg in pending.items():
        if us_only and not _is_us(cfg["ticker"]):
            continue
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


def check_positions(send_fn, us_only: bool = False) -> None:
    """Scan des positions 4x/jour. Alerte si SL ou TP atteint.
    us_only : séance US — ne surveille que les positions US et reste silencieux
    (alertes uniquement, pas de status de routine) s'il n'y a rien à signaler."""
    data = portfolio.load()
    positions = data.get("positions", {})
    if us_only:
        positions = {n: c for n, c in positions.items() if _is_us(c["ticker"])}
    now = datetime.now(PARIS).strftime("%H:%M")

    print(f"\n[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] "
          f"Scan positions{' US' if us_only else ''}...")

    if not positions:
        if not us_only:
            send_fn(f"⚠️ STATUS {now} — Aucune position active.")
        return

    alerts = []
    status_lines = [f"📊 STATUS {now}{' 🇺🇸 US' if us_only else ''}"]

    for name, cfg in positions.items():
        # Position HOLD long terme : hors gestion bot — affichage informatif
        # uniquement, aucune alerte SL/TP/breakeven.
        if cfg.get("hold"):
            status_lines.append(f"  🔒 {name}: HOLD long terme — hors gestion bot")
            continue

        # Cours retenu, P&L, drapeaux : position_view (source unique). yfinance
        # saute des séances entières sans le dire (04/08/2026) — s'en remettre à
        # lui aveuglément fait afficher un P&L faux ET raisonner les alertes
        # SL/TP sur un cours mort.
        quote = prices.get_quote(cfg["ticker"])
        v     = position_view.view(name, cfg, quote)
        price = v["price"]
        sym   = v["sym"]

        if price is None:
            code, msg = v["problem"]
            icon = {"ticker": "🚨", "suspended": "⛔"}.get(code, "⚠️")
            status_lines.append(f"  {icon} {name}: {msg}")
            continue

        change_pct = v["chg_pct"]
        pnl        = v["pnl"]
        icon       = "📈" if change_pct >= 0 else "📉"
        src_tag    = "" if v["source"] == "yf" else f"\n     ⚠️ {v['note']}"
        # Un SL/TP mémorisé n'est pas un SL/TP actif : le dernier sync dit si un
        # ordre les porte réellement sur BD (cas BAC, 05/08/2026).
        pend_tag = position_view.alerte_stop_en_attente(v, indent="     ")
        prot_tag = position_view.alerte_protection(v, indent="     ")
        status_lines.append(
            f"  {icon} {name}: {sym}{price} ({change_pct:+.2f}%) | P&L: {sym}{pnl:+.0f}"
            f"\n     SL {sym}{v['sl']} — TP {sym}{v['tp']}{pend_tag}{prot_tag}{src_tag}"
        )

        # Range intraday des 4 dernières heures pour détecter les franchissements
        # entre checks. Le cours retenu y est INTÉGRÉ : si le range vient d'une
        # séance périmée, il ignorerait le cours réel — un SL franchi passerait
        # inaperçu.
        rng    = prices.get_intraday_range(cfg["ticker"], hours=4)
        high4h = max(rng.get("high", price) or price, price)
        low4h  = min(rng.get("low",  price) or price, price)

        # Réarme l'alerte TP quand le cours est repassé sous le seuil
        if cfg.get("tp_breach_notified") and high4h < cfg["target_high"]:
            portfolio.mark_tp_breach(name, False)

        # Zone d'alerte « SL proche » — voir _sl_proche_level : jamais au-dessus
        # du PRU, sinon l'alerte partait à l'ouverture de toute position dont le
        # SL est à moins de 5% (cas JNJ le 29/07/2026, SL à 2×ATR = 4.6%).
        sl_zone = _sl_proche_level(cfg)

        # Réarme l'alerte SL proche quand le cours ressort de la zone (+2%)
        if cfg.get("sl_proche_notified") and price > sl_zone * 1.02:
            portfolio.mark_sl_proche(name, False)

        # Réarme l'alerte breakeven si le SL a finalement été relevé au PRU
        if cfg.get("breakeven_notified") and cfg["target_low"] >= cfg["entry_price"]:
            portfolio.mark_breakeven(name, False)

        if high4h >= cfg["target_high"]:
            if TP_ALERTS and not cfg.get("tp_breach_notified", False):
                alerts.append({"type": "TP", "name": name, "cfg": cfg,
                               "price": price, "trigger": high4h,
                               "change": change_pct, "pnl": pnl, "sym": sym})
        elif low4h <= cfg["target_low"]:
            if not cfg.get("sl_breach_notified", False):
                alerts.append({"type": "SL_BREACH", "name": name, "cfg": cfg,
                               "price": price, "trigger": low4h,
                               "change": change_pct, "pnl": pnl, "sym": sym})
        elif low4h <= sl_zone:
            # Anti-spam : une seule alerte par épisode d'approche du SL.
            if not cfg.get("sl_proche_notified", False):
                alerts.append({"type": "SL_PROCHE", "name": name, "cfg": cfg,
                               "price": price, "trigger": low4h,
                               "change": change_pct, "pnl": pnl, "sym": sym})

        # Trailing stop : quand la position atteint +BREAKEVEN_THRESHOLD% au-dessus du PRU,
        # propose (et enregistre) un relevé du SL au PRU — zéro perte garanti.
        entry = cfg["entry_price"]
        if (change_pct >= BREAKEVEN_THRESHOLD
                and cfg["target_low"] < entry
                and not cfg.get("breakeven_notified", False)):
            alerts.append({"type": "BREAKEVEN", "name": name, "cfg": cfg,
                           "price": price, "trigger": price,
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
                + f"\n\n💡 Niveau atteint — vendre, ou relever le TP si la thèse"
                  f"\nreste haussière : /research {cfg['ticker']} pour trancher."
                  f"\n(Alerte unique. /tp {cfg['ticker']} PRIX pour relever |"
                  f"\nTP_ALERTS=off dans .env pour désactiver ces alertes)"
            )
            portfolio.mark_tp_breach(a["name"])
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
        elif a["type"] == "BREAKEVEN":
            entry = cfg["entry_price"]
            # Session BD connectée → le remplacement de l'ordre Expert sur BD
            # est AUTOMATIQUE (trailing_stop_cycle). On le déclenche tout de
            # suite au lieu de donner un mode d'emploi manuel. Le SL local et
            # la notification seront posés par le trailing lui-même.
            import bot_mode
            import playwright_session
            if bot_mode.is_playwright() and playwright_session.is_connected():
                import threading
                import autonomous_engine
                threading.Thread(
                    target=autonomous_engine.trailing_stop_cycle,
                    args=(send_fn,), daemon=True,
                ).start()
                print(f"[monitor] breakeven {a['name']} → trailing automatique déclenché")
                continue
            # Mode déconnecté : mise à jour locale + instructions manuelles
            portfolio.update_sl(a["name"], entry)
            portfolio.mark_breakeven(a["name"])
            msg = (
                f"🔒 TRAILING STOP — {a['name']}\n\n"
                f"Position à {a['change']:+.1f}% au-dessus du PRU ({sym}{entry})\n"
                f"SL relevé automatiquement au PRU dans le bot.\n"
                f"P&L garanti ≥ 0 si exécuté.\n\n"
                f"Place le nouvel ordre expert protection (SL = PRU) :\n\n"
                + orders.expert_protection(cfg["ticker"], cfg["qty"], entry, cfg["target_high"])
            )
        else:
            msg = (
                f"⚠️ SL PROCHE — {a['name']} (alerte unique par épisode)\n\n"
                f"Low 4h : {sym}{trigger} — Seuil SL : {sym}{cfg['target_low']}\n"
                f"Cours actuel : {sym}{a['price']} ({a['change']:+.2f}%) | P&L : {sym}{a['pnl']:+.0f}\n\n"
                f"Vérifie que ta protection est active sur Bourse Direct :\n\n"
                + orders.expert_protection(
                    cfg["ticker"], cfg["qty"],
                    cfg["target_low"], cfg["target_high"]
                )
            )
            portfolio.mark_sl_proche(a["name"])
        send_fn(msg)
        sent_any = True

    if not sent_any:
        if us_only:
            print("✅ Check US — pas d'alerte (status de routine supprimé)")
        else:
            send_fn("\n".join(status_lines))
            print("✅ Status envoyé — pas d'alerte")

    # Mode autonome : surveillance des sorties (SL/TP/breakeven) des positions
    # autonomes. En séance US on NE relance PAS run_entry_cycle : le sync horaire
    # (:35) le déclenche déjà toutes les heures jusqu'à 22h — inutile de doubler
    # les tentatives d'entrée (et le spam de messages).
    try:
        import autonomous_engine
        autonomous_engine.check_autonomous_positions(send_fn)
        if not us_only:
            import threading
            threading.Thread(
                target=autonomous_engine.run_entry_cycle,
                args=(send_fn,),
                daemon=True,
            ).start()
    except Exception as e:
        print(f"[Auto] Erreur cycle autonome : {e}")
