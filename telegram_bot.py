"""
Interface Telegram : polling + commandes interactives.
Toutes les commandes sont disponibles depuis l'app iPhone/web.
"""
import requests
import time
import threading
from config import TELEGRAM_TOKEN, CHAT_ID, GMAIL_USER, GMAIL_APP_PASSWORD
import portfolio
import prices
import analysis
import orders
import stats
import bot_mode
import playwright_session
import bourse_direct_auth

# ─── Buffer multi-screenshots ────────────────────────────────────────────────
# Collecte toutes les photos envoyées dans les N secondes qui suivent la 1ère,
# puis les traite ensemble pour reconstituer le portefeuille complet.

BUFFER_WAIT = 12          # secondes d'attente après la dernière photo reçue
_photo_buf: dict = {}     # cid -> {"images": [bytes], "timer": Timer}
_buf_lock = threading.Lock()


# ─── Envoi ──────────────────────────────────────────────────────────────────

def send(text: str, chat_id: str = None) -> bool:
    if not TELEGRAM_TOKEN:
        print(f"[NO TOKEN] {text[:80]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id or CHAT_ID, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


# ─── Handlers de commandes ──────────────────────────────────────────────────

def cmd_start(args, cid):
    cash = portfolio.get_cash()
    positions = portfolio.get_positions()
    nb = len(positions)
    send(
        "Bienvenue sur TradingBot\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Assistant de trading pour Bourse Direct,\n"
        "pilote depuis Telegram.\n"
        "\n"
        f"Portefeuille : {nb} position{'s' if nb != 1 else ''} | Cash : {cash}€\n"
        "\n"
        "Pour commencer :\n"
        "  /status — voir votre portefeuille\n"
        "  /help   — liste complete des commandes\n"
        "  /tuto   — guide de configuration\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        cid,
    )


def cmd_help(args, cid):
    send(
        "TradingBot — Liste des commandes\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "PORTEFEUILLE\n"
        "/status — P&L en temps réel pour chaque position\n"
        "/cash — Affiche le cash disponible\n"
        "/cash 1234.56 — Met à jour le cash\n"
        "\n"
        "POSITIONS\n"
        "/add TICKER QTE PRU SL TP — Ajoute une position\n"
        "  ex: /add LBIRD.PA 48 24.46 22.01 28.13\n"
        "/remove TICKER — Supprime une position\n"
        "/sl TICKER PRIX — Change le stop-loss\n"
        "/tp TICKER PRIX — Change le take-profit\n"
        "\n"
        "ORDRES BOURSE DIRECT\n"
        "/buy TICKER QTE PRU — Ordre Expert Take Profit\n"
        "  (achat + SL + TP automatiques en 1 seul ordre)\n"
        "/setup TICKER QTE PRU — 2 ordres de protection\n"
        "  apres un achat deja effectue\n"
        "/order buy TICKER QTE PRIX — Ordre achat simple\n"
        "/order sell TICKER QTE PRIX — Ordre vente simple\n"
        "\n"
        "PERFORMANCES\n"
        "ORDRES EN ATTENTE\n"
        "/attente NOM TICKER QTE PRIX [SL TP] — Enregistre un ordre limite\n"
        "  ex: /attente EXOSENS EXENS.PA 17 63\n"
        "/annuler NOM — Annule et libere le cash reserve\n"
        "\n"
        "/stats — Win Rate, P&L realise/latent, Profit Factor\n"
        "/vendu NOM [PRIX] — Cloturer (prix TP auto si omis)\n"
        "/close TICKER QTY PRIX [FRAIS] — Cloturer avec frais\n"
        "/syncmail — Sync Gmail : detecte les ordres BD executes\n"
        "/update — Version actuelle + alerte si mise a jour dispo\n"
        "\n"
        "ANALYSE IA\n"
        "/morning — Briefing complet (macro + positions + opps)\n"
        "/scan — Top 3 opportunites avec le cash dispo\n"
        "/research TICKER — Analyse approfondie d'une action\n"
        "\n"
        "IMPORT PORTEFEUILLE\n"
        "Envoie une photo de l'app BD — le bot lit tout\n"
        "/import — Guide import CSV (si disponible)\n"
        "\n"
        "AIDE\n"
        "/help — Cette liste\n"
        "/tuto — Guide de mise en place complet\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        cid,
    )


def cmd_status(args, cid):
    data = portfolio.load()
    positions = data.get("positions", {})
    cash = data.get("cash_available", 0)

    if not positions:
        send(f"Portefeuille vide.\nCash disponible: {cash}€", cid)
        return

    lines = ["PORTEFEUILLE", f"Cash: {cash}€", ""]
    total_pnl = 0

    for name, cfg in positions.items():
        q = prices.get_quote(cfg["ticker"])
        price = q.get("price")
        if price:
            chg = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
            pnl = (price - cfg["entry_price"]) * cfg["qty"]
            total_pnl += pnl
            arrow  = "+" if chg >= 0 else ""
            sl_tag = " ⚠️ SL DÉPASSÉ" if price < cfg["target_low"] else ""
            tp_tag = " ⚠️ TP DÉPASSÉ" if price > cfg["entry_price"] * 1.25 else ""
            sym    = prices.currency_symbol(q.get("currency", "EUR"))
            lines.append(
                f"{name} ({cfg['ticker']})\n"
                f"  Prix: {sym}{price} ({arrow}{chg:.2f}%) | P&L: {sym}{pnl:+.0f}{sl_tag}{tp_tag}\n"
                f"  PRU: {sym}{cfg['entry_price']} | {cfg['qty']} titres\n"
                f"  SL: {sym}{cfg['target_low']}  TP: {sym}{cfg['target_high']}"
            )
        elif q.get("status") in ("suspended", "error"):
            lines.append(
                f"{name} ({cfg['ticker']})\n"
                f"  ⛔ COURS SUSPENDU — non vendable (liquidation judiciaire ?)\n"
                f"  PRU: {cfg['entry_price']}€ | {cfg['qty']} titres"
            )
        else:
            lines.append(f"{name}: prix indisponible | PRU {cfg['entry_price']}€")

    lines.append(f"\nP&L total positions: {total_pnl:+.0f}€")

    pending = data.get("pending_orders", {})
    if pending:
        lines.append("\nORDRES EN ATTENTE")
        for name, cfg in pending.items():
            q = prices.get_quote(cfg["ticker"])
            price = q.get("price")
            if price:
                drift = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
                lines.append(
                    f"{name} ({cfg['ticker']})\n"
                    f"  Achat limite: {cfg['entry_price']}€ x {cfg['qty']}t "
                    f"({cfg['reserved_cash']:.0f}€ réservés)\n"
                    f"  Cours actuel: {price}€ ({drift:+.1f}%) | "
                    f"SL: {cfg['target_low']}€  TP: {cfg['target_high']}€\n"
                    f"  → /annuler {name} pour libérer le cash"
                )
            else:
                lines.append(
                    f"{name}: {cfg['entry_price']}€ x {cfg['qty']}t "
                    f"({cfg['reserved_cash']:.0f}€ réservés)"
                )

    send("\n".join(lines), cid)


def cmd_cash(args, cid):
    if args:
        try:
            amount = float(args[0].replace(",", "."))
            portfolio.update_cash(amount)
            send(f"Cash mis a jour: {amount}€", cid)
        except ValueError:
            send("Usage: /cash 1234.56", cid)
    else:
        send(f"Cash disponible: {portfolio.get_cash()}€", cid)


def cmd_add(args, cid):
    # /add TICKER QTY PRU SL TP
    if len(args) < 5:
        send("Usage: /add TICKER QTY PRU SL TP\nEx: /add GNFT.PA 100 8.51 7.66 9.79", cid)
        return
    try:
        ticker = args[0].upper()
        qty    = int(args[1])
        pru    = float(args[2].replace(",", "."))
        sl     = float(args[3].replace(",", "."))
        tp     = float(args[4].replace(",", "."))
        name   = ticker.split(".")[0]

        # Si un ordre en attente existait pour cette valeur, l'annuler sans rendre le cash
        # (le cash était déjà réservé = déjà déduit du disponible)
        # Recherche par nom exact OU par ticker (évite les écarts de nommage)
        data = portfolio.load()
        pending = data.get("pending_orders", {})
        pending_key = name if name in pending else next(
            (k for k, v in pending.items() if v.get("ticker") == ticker), None
        )
        had_pending = pending_key is not None
        if had_pending:
            pending.pop(pending_key, None)
            portfolio.save(data)

        portfolio.add_position(name, ticker, qty, pru, sl, tp)
        note = " (ordre en attente cloture)" if had_pending else ""
        send(f"Position ajoutee: {name}{note}\n{qty}t @ PRU {pru}€ | SL {sl}€ | TP {tp}€", cid)
    except (ValueError, IndexError):
        send("Format invalide.\nEx: /add GNFT.PA 100 8.51 7.66 9.79", cid)


def cmd_remove(args, cid):
    if not args:
        send("Usage: /remove TICKER", cid)
        return
    name = args[0].upper().split(".")[0]
    portfolio.remove_position(name)
    send(f"Position {name} supprimee.", cid)


def cmd_sl(args, cid):
    # /sl TICKER PRIX
    if len(args) < 2:
        send("Usage: /sl TICKER PRIX\nEx: /sl LBIRD 22.01", cid)
        return
    name = args[0].upper().split(".")[0]
    try:
        price = float(args[1].replace(",", "."))
    except ValueError:
        send("Prix invalide.", cid)
        return
    data = portfolio.load()
    if name not in data.get("positions", {}):
        send(f"Position {name} introuvable.", cid)
        return
    portfolio.update_sl(name, price)
    cfg = data["positions"][name]
    send(
        f"SL {name} mis a jour: {price}€\n\n"
        + orders.stop_loss(cfg["ticker"], cfg["qty"], price),
        cid,
    )


def cmd_tp(args, cid):
    # /tp TICKER PRIX
    if len(args) < 2:
        send("Usage: /tp TICKER PRIX\nEx: /tp LBIRD 28.13", cid)
        return
    name = args[0].upper().split(".")[0]
    try:
        price = float(args[1].replace(",", "."))
    except ValueError:
        send("Prix invalide.", cid)
        return
    if portfolio.update_tp(name, price):
        send(f"TP {name} mis a jour: {price}€", cid)
    else:
        send(f"Position {name} introuvable.", cid)


def cmd_order(args, cid):
    # /order buy|sell TICKER QTY PRIX
    if len(args) < 4:
        send("Usage: /order buy|sell TICKER QTY PRIX\nEx: /order sell LBIRD 48 28.13", cid)
        return
    side, ticker = args[0].lower(), args[1].upper()
    try:
        qty = int(args[2])
        price = float(args[3].replace(",", "."))
    except ValueError:
        send("Quantite et prix doivent etre des nombres.", cid)
        return
    fn = orders.buy_limit if side == "buy" else orders.take_profit
    send(fn(ticker, qty, price), cid)


def cmd_buy(args, cid):
    # /buy TICKER QTY PRU — Ordre Expert Take Profit (achat + SL + TP en 1 ordre)
    if len(args) < 3:
        send("Usage: /buy TICKER QTY PRU\nEx: /buy MC 10 750.00", cid)
        return
    ticker = args[0].upper()
    try:
        qty = int(args[1])
        pru = float(args[2].replace(",", "."))
    except ValueError:
        send("Format invalide.", cid)
        return
    send(orders.expert_take_profit_buy(ticker, qty, pru), cid)


def cmd_setup(args, cid):
    # /setup TICKER QTY PRU — génère 2 ordres de protection après un achat déjà fait
    if len(args) < 3:
        send("Usage: /setup TICKER QTY PRU\nEx: /setup LBIRD 48 24.46", cid)
        return
    ticker = args[0].upper()
    try:
        qty = int(args[1])
        pru = float(args[2].replace(",", "."))
    except ValueError:
        send("Format invalide.", cid)
        return
    send(orders.full_setup(ticker, qty, pru), cid)


def cmd_stats(args, cid):
    send("Calcul des performances...", cid)
    s = stats.get_stats()
    lines = [
        "PERFORMANCES — TradingBot",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if s["nb_closed"] == 0:
        lines.append("\nAucun trade cloture enregistre.")
        lines.append("Utilise /close TICKER QTY PRIX pour enregistrer une vente.")
    else:
        lines.append(f"\nTRADES CLOTURES — {s['nb_closed']} trades")
        lines.append(f"Win Rate      : {s['win_rate']}%  ({s['nb_wins']}W / {s['nb_losses']}L)")
        lines.append(f"P&L realise   : {s['realized_pnl']:+.0f}€")
        lines.append(f"Gain moyen    : {s['avg_win']:+.0f}€")
        lines.append(f"Perte moyenne : {s['avg_loss']:+.0f}€")
        if s["profit_factor"] is not None:
            pf = s["profit_factor"]
            pf_comment = "bon" if pf >= 1.5 else ("negatif" if pf < 1 else "limite")
            lines.append(f"Profit Factor : {pf} ({pf_comment})")
        if s["best_trade"]:
            b = s["best_trade"]
            lines.append(f"Meilleur trade: {b['name']} {b['pnl']:+.0f}€")
        if s["worst_trade"]:
            w = s["worst_trade"]
            lines.append(f"Pire trade    : {w['name']} {w['pnl']:+.0f}€")

    lines.append(f"\nPOSITIONS OUVERTES")
    lines.append(f"P&L latent    : {s['unrealized_pnl']:+.0f}€")
    lines.append(f"\nTOTAL P&L     : {s['total_pnl']:+.0f}€")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    send("\n".join(lines), cid)


def cmd_close(args, cid):
    # /close TICKER QTY PRIX_VENTE [FRAIS]
    if len(args) < 3:
        send(
            "Usage: /close TICKER QTY PRIX [FRAIS]\n"
            "Ex: /close LBIRD 48 28.13 2.90\n\n"
            "Enregistre la vente, met a jour le cash et l'historique.",
            cid,
        )
        return
    name = args[0].upper().split(".")[0]
    try:
        qty        = int(args[1])
        exit_price = float(args[2].replace(",", "."))
        fees       = float(args[3].replace(",", ".")) if len(args) > 3 else 0.0
    except ValueError:
        send("Format invalide.", cid)
        return

    data = portfolio.load()
    positions = data.get("positions", {})
    if name not in positions:
        send(f"Position {name} introuvable. Positions actuelles: {list(positions.keys())}", cid)
        return

    cfg = positions[name]
    pnl = stats.record_close(name, cfg["ticker"], qty, cfg["entry_price"], exit_price, fees)

    portfolio.remove_position(name)
    proceeds = round(exit_price * qty - fees, 2)
    portfolio.update_cash(round(portfolio.get_cash() + proceeds, 2))

    pct = ((exit_price - cfg["entry_price"]) / cfg["entry_price"]) * 100
    result = "WIN" if pnl > 0 else "LOSS"
    send(
        f"Trade cloture — {name}  {result}\n"
        f"  {qty}t @ {exit_price}€  (PRU {cfg['entry_price']}€)\n"
        f"  P&L : {pnl:+.0f}€  ({pct:+.1f}%)\n"
        f"  Frais : {fees}€\n"
        f"  Cash mis a jour : {portfolio.get_cash():.2f}€\n\n"
        "/stats pour voir l'historique complet.",
        cid,
    )


def cmd_attente(args, cid):
    # /attente NOM TICKER QTE PRIX [SL TP]
    if len(args) < 4:
        send(
            "Usage: /attente NOM TICKER QTE PRIX [SL TP]\n"
            "Ex: /attente EXOSENS EXENS.PA 17 63\n"
            "Ex: /attente EXOSENS EXENS.PA 17 63 56.70 72.45\n\n"
            "Reserve le cash et surveille le declenchement.",
            cid,
        )
        return
    try:
        name   = args[0].upper()
        ticker = args[1].upper()
        qty    = int(args[2])
        entry  = float(args[3].replace(",", "."))
        sl     = float(args[4].replace(",", ".")) if len(args) > 4 else round(entry * 0.90, 4)
        tp     = float(args[5].replace(",", ".")) if len(args) > 5 else round(entry * 1.15, 4)
    except (ValueError, IndexError):
        send("Format invalide.", cid)
        return

    cash     = portfolio.get_cash()
    reserved = round(entry * qty, 2)
    if reserved > cash:
        send(f"Cash insuffisant : {reserved}€ requis, {cash}€ disponible.", cid)
        return

    portfolio.add_pending_order(name, ticker, qty, entry, sl, tp)
    send(
        f"Ordre en attente enregistre — {name}\n"
        f"  {qty}t @ {entry}€  SL: {sl}€  TP: {tp}€\n"
        f"  {reserved:.0f}€ reserves\n"
        f"  Cash restant: {portfolio.get_cash():.2f}€\n\n"
        f"Alerte quand le cours atteint {entry}€.\n"
        f"→ /annuler {name} pour liberer le cash",
        cid,
    )


def cmd_annuler(args, cid):
    # /annuler NOM — annule un ordre en attente et libère le cash
    if not args:
        pending = portfolio.get_pending_orders()
        if not pending:
            send("Aucun ordre en attente.", cid)
        else:
            send(
                "Ordres en attente :\n" +
                "\n".join(f"- {n} ({cfg['entry_price']}€ x {cfg['qty']}t)"
                          for n, cfg in pending.items()) +
                "\n\nUsage: /annuler NOM",
                cid,
            )
        return

    name     = args[0].upper()
    released = portfolio.cancel_pending_order(name)
    if released:
        send(
            f"Ordre {name} annule.\n"
            f"  {released:.0f}€ liberes\n"
            f"  Cash disponible: {portfolio.get_cash():.2f}€",
            cid,
        )
    else:
        send(f"Aucun ordre en attente pour {name}.", cid)


def cmd_update(args, cid):
    import subprocess
    try:
        local_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd="/Users/yoksquare/TradingBot", text=True
        ).strip()
        local_short = local_hash[:7]
        local_info = subprocess.check_output(
            ["git", "log", "-1", "--format=%ad %s", "--date=format:%d/%m/%Y"],
            cwd="/Users/yoksquare/TradingBot", text=True
        ).strip()

        # Vérifie le dernier commit sur GitHub
        try:
            resp = requests.get(
                "https://api.github.com/repos/myopencomm/Tradingbot/commits/main",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=5,
            )
            remote_hash = resp.json().get("sha", "") if resp.status_code == 200 else ""
        except Exception:
            remote_hash = ""

        if remote_hash and remote_hash != local_hash:
            remote_short = remote_hash[:7]
            status = (
                f"MISE A JOUR DISPONIBLE\n"
                f"Version locale  : {local_short} ({local_info})\n"
                f"Version distante: {remote_short}\n\n"
                f"Pour mettre a jour :\n"
                f"git pull origin main\n"
                f"pkill -f main.py\n"
                f"venv/bin/python3 main.py > tradingbot.log 2>&1 &"
            )
        else:
            status = (
                f"Bot a jour\n"
                f"Commit : {local_short} — {local_info}"
            )

        send(f"TradingBot — version\n\n{status}", cid)

    except Exception as e:
        send(f"Impossible de lire la version : {e}", cid)


def cmd_vendu(args, cid):
    # /vendu NOM [PRIX] — clôture intelligente avec prix auto ou manuel
    if not args:
        send(
            "Usage: /vendu NOM [PRIX]\n"
            "Ex: /vendu VU         (prix = TP pose sur BD)\n"
            "Ex: /vendu VU 18.50   (prix manuel)",
            cid,
        )
        return

    name_input = args[0].upper().split(".")[0]
    data = portfolio.load()
    positions = data.get("positions", {})

    # Recherche de la position : nom exact, puis ticker, puis préfixe
    name = None
    if name_input in positions:
        name = name_input
    else:
        for n, cfg in positions.items():
            ticker_base = cfg["ticker"].split(".")[0].upper()
            if ticker_base == name_input or n.startswith(name_input):
                name = n
                break

    if not name:
        send(f"Position '{name_input}' introuvable.\nPositions: {list(positions.keys())}", cid)
        return

    cfg = positions[name]

    if len(args) >= 2:
        try:
            exit_price = float(args[1].replace(",", "."))
            price_source = "manuel"
        except ValueError:
            send("Prix invalide.", cid)
            return
    else:
        # Prix par défaut = TP posé (ordre limite take_profit exécuté au prix exact)
        exit_price = cfg.get("target_high")
        price_source = "TP Bourse Direct"
        if not exit_price:
            quote = prices.get_quote(cfg["ticker"])
            exit_price = quote.get("price")
            price_source = "cours live"
        if not exit_price:
            send(f"Prix indisponible pour {cfg['ticker']}. Utilise /vendu {name} PRIX", cid)
            return

    pnl      = stats.record_close(name, cfg["ticker"], cfg["qty"], cfg["entry_price"], exit_price)
    proceeds = round(exit_price * cfg["qty"], 2)
    portfolio.clear_gmail_triggered(name)
    portfolio.remove_position(name)
    portfolio.update_cash(round(portfolio.get_cash() + proceeds, 2))

    pct = ((exit_price - cfg["entry_price"]) / cfg["entry_price"]) * 100
    tag = "WIN" if pnl > 0 else "LOSS"
    send(
        f"Trade cloture — {name}  {tag}\n"
        f"  {cfg['qty']}t @ {exit_price}€  (PRU {cfg['entry_price']}€)\n"
        f"  P&L : {pnl:+.0f}€  ({pct:+.1f}%)\n"
        f"  Prix : {price_source}\n"
        f"  Cash : {portfolio.get_cash():.2f}€\n\n"
        "/stats pour voir l'historique complet.",
        cid,
    )


def cmd_syncmail(args, cid):
    # /syncmail — vérifie Gmail pour les déclenchements d'ordres Bourse Direct
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        send(
            "Gmail non configure.\n"
            "Ajoute dans .env :\n"
            "GMAIL_USER=ton@gmail.com\n"
            "GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx\n\n"
            "Cree un mot de passe d'application :\n"
            "myaccount.google.com > Securite > Mots de passe des applications",
            cid,
        )
        return
    send("Verification Gmail Bourse Direct...", cid)
    import gmail_sync
    notifications = gmail_sync.check_and_notify(GMAIL_USER, GMAIL_APP_PASSWORD)
    messages = gmail_sync.format_notifications(notifications)
    if messages:
        for msg in messages:
            send(msg, cid)
    else:
        send("Aucun nouvel ordre Bourse Direct detecte.", cid)


def cmd_morning(args, cid):
    send("Briefing en cours de generation...", cid)
    threading.Thread(target=analysis.morning_briefing, args=(lambda m: send(m, cid),), daemon=True).start()


def cmd_scan(args, cid):
    send("Scan en cours...", cid)
    threading.Thread(target=analysis.scan_opportunities, args=(lambda m: send(m, cid),), daemon=True).start()


def cmd_research(args, cid):
    if not args:
        send("Usage: /research TICKER\nEx: /research GNFT.PA", cid)
        return
    ticker = args[0].upper()
    send(f"Analyse de {ticker} en cours...", cid)
    threading.Thread(
        target=analysis.research_ticker,
        args=(lambda m: send(m, cid), ticker),
        daemon=True,
    ).start()


def cmd_import(args, cid):
    send(
        "Import portefeuille — 2 methodes :\n\n"
        "METHODE 1 — Screenshot (recommande mobile)\n"
        "Envoie directement une photo de ton portefeuille\n"
        "Bourse Direct dans ce chat. Tu peux envoyer\n"
        "plusieurs captures si tu dois scroller.\n"
        "Le bot extrait tout automatiquement.\n\n"
        "METHODE 2 — CSV (sur ordinateur)\n"
        "Bourse Direct → Portefeuille → Exporter CSV\n"
        "Envoie le fichier .csv dans ce chat.\n\n"
        "Dans les deux cas, utilise ensuite /add\n"
        "pour confirmer chaque position avec SL et TP.",
        cid,
    )


def cmd_tuto(args, cid):
    sections = {
        "install":    _tuto_install,
        "classic":    _tuto_classic,
        "playwright": _tuto_playwright,
        "avance":     _tuto_avance,
        "update":     _tuto_update,
    }
    if args and args[0].lower() in sections:
        sections[args[0].lower()](cid)
    else:
        send(
            "TradingBot — Guide interactif\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Choisis ce que tu veux apprendre :\n"
            "\n"
            "/tuto install\n"
            "  Installation complete depuis zero\n"
            "  (Telegram, Python, .env, lancement)\n"
            "\n"
            "/tuto classic\n"
            "  Mode Classic : screenshots, workflow\n"
            "  quotidien, ajouter/suivre ses positions\n"
            "\n"
            "/tuto playwright\n"
            "  Mode Playwright : connexion automatique\n"
            "  a Bourse Direct, 2FA, sync portefeuille\n"
            "\n"
            "/tuto avance\n"
            "  Fonctions avancees : ordres en attente,\n"
            "  Gmail sync, cloture de positions, stats\n"
            "\n"
            "/tuto update\n"
            "  Mettre a jour le bot",
            cid,
        )


def _tuto_install(cid):
    send(
        "Installation — Etape 1 : Bot Telegram\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. Ouvre Telegram → cherche @BotFather\n"
        "2. Envoie /newbot\n"
        "3. Choisis un nom puis un username (_bot)\n"
        "4. Copie le TOKEN recu : ***REMOVED***\n"
        "\n"
        "Ton Chat ID (pour limiter le bot a toi seul) :\n"
        "→ @userinfobot sur Telegram → envoie /start\n"
        "→ Il te repond avec ton Id numerique",
        cid,
    )
    time.sleep(0.4)
    send(
        "Installation — Etape 2 : Telecharger\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Dans ton terminal :\n"
        "  git clone https://github.com/myopencomm/Tradingbot.git\n"
        "  cd Tradingbot\n"
        "  python3 -m venv venv\n"
        "  venv/bin/pip install -r requirements.txt\n"
        "  cp .env.example .env\n"
        "  cp positions.example.json positions.json\n"
        "\n"
        "Python 3.10 minimum requis.\n"
        "Sur Mac si python3 --version affiche 3.9 :\n"
        "  brew install python",
        cid,
    )
    time.sleep(0.4)
    send(
        "Installation — Etape 3 : Configurer .env\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Edite le fichier .env :\n"
        "  TELEGRAM_TOKEN=***REMOVED***\n"
        "  CHAT_ID=***REMOVED***\n"
        "  AI_PROVIDER=groq          ← gratuit\n"
        "  GROQ_API_KEY=gsk_...\n"
        "\n"
        "Providers IA disponibles :\n"
        "  groq    → console.groq.com (gratuit)\n"
        "  gemini  → aistudio.google.com (gratuit)\n"
        "  anthropic / openai / mistral (payants)\n"
        "\n"
        "Ne partage JAMAIS ton .env — jamais commit.",
        cid,
    )
    time.sleep(0.4)
    send(
        "Installation — Etape 4 : Lancer\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  venv/bin/python3 main.py\n"
        "\n"
        "Le bot envoie un message de confirmation.\n"
        "Envoie /start pour verifier.\n"
        "\n"
        "En arriere-plan (reste actif apres fermeture\n"
        "du terminal) :\n"
        "  venv/bin/python3 main.py > tradingbot.log 2>&1 &\n"
        "\n"
        "Voir les logs :\n"
        "  tail -f tradingbot.log",
        cid,
    )


def _tuto_classic(cid):
    send(
        "Mode Classic — Importer ton portefeuille\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "3 facons d'entrer tes positions :\n"
        "\n"
        "1. SCREENSHOT (le plus simple)\n"
        "   Envoie une ou plusieurs photos de l'app\n"
        "   Bourse Direct → le bot lit tout auto\n"
        "   Tu peux envoyer plusieurs captures a la\n"
        "   suite, il les fusionne (attends 12s)\n"
        "\n"
        "2. MANUEL\n"
        "   /add TICKER QTE PRU SL TP\n"
        "   Ex: /add GNFT.PA 100 8.51 7.66 9.79\n"
        "\n"
        "3. CSV\n"
        "   Exporte depuis BD → envoie le fichier .csv\n"
        "   /import pour le guide\n"
        "\n"
        "Cash disponible : /cash 1500",
        cid,
    )
    time.sleep(0.4)
    send(
        "Mode Classic — Workflow quotidien\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "AUTOMATIQUE\n"
        "  9h05  → briefing IA (macro + positions)\n"
        "  9/12/15/17h → check SL/TP, alertes\n"
        "  Lundi 9h10 → analyse de rotation\n"
        "\n"
        "A LA DEMANDE\n"
        "  /status   → portefeuille + P&L live\n"
        "  /morning  → briefing maintenant\n"
        "  /scan     → 3 opportunites avec ton cash\n"
        "  /research TICKER → analyse approfondie\n"
        "\n"
        "ORDRES (instructions a saisir sur BD)\n"
        "  /buy TICKER QTE PRU\n"
        "    → ordre Expert Take Profit complet\n"
        "  /setup TICKER QTE PRU\n"
        "    → SL + TP apres achat deja effectue",
        cid,
    )


def _tuto_playwright(cid):
    send(
        "Mode Playwright — Installation\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Connexion directe a Bourse Direct via\n"
        "navigateur headless. Les screenshots\n"
        "restent disponibles en parallele.\n"
        "\n"
        "INSTALLATION (une seule fois)\n"
        "  venv/bin/pip install playwright\n"
        "  venv/bin/playwright install chromium\n"
        "\n"
        "CONFIGURATION (.env)\n"
        "  BD_LOGIN=ton_identifiant_bourse_direct\n"
        "  BD_PASSWORD=ton_mot_de_passe\n"
        "\n"
        "Redemarrer le bot apres avoir edite .env.",
        cid,
    )
    time.sleep(0.4)
    send(
        "Mode Playwright — Utilisation\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/connect\n"
        "  Lance la connexion a Bourse Direct\n"
        "  Si 2FA SMS : le bot te demande le code\n"
        "  → Envoie les chiffres directement ici\n"
        "  Tu as 90 secondes pour repondre\n"
        "\n"
        "/mode     → voir l'etat de la connexion\n"
        "/sync     → synchroniser depuis BD\n"
        "/disconnect → revenir en mode Classic\n"
        "\n"
        "Le bot demarre toujours en mode Classic.\n"
        "/connect est a relancer apres chaque\n"
        "redemarrage du bot.",
        cid,
    )


def _tuto_avance(cid):
    send(
        "Fonctions avancees — Ordres en attente\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Pour placer un ordre limite sur BD et\n"
        "laisser le bot surveiller son declenchement :\n"
        "\n"
        "  /attente NOM TICKER QTE PRIX [SL TP]\n"
        "  Ex: /attente EXOSENS EXENS.PA 17 63\n"
        "\n"
        "→ Reserve le cash automatiquement\n"
        "→ Alerte si le cours touche ton prix\n"
        "→ Alerte si le cours s'eloigne trop (+15%)\n"
        "→ /scan reevalue la viabilite a chaque analyse\n"
        "\n"
        "  /annuler NOM → annule et libere le cash",
        cid,
    )
    time.sleep(0.4)
    send(
        "Fonctions avancees — Cloture & Gmail\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CLOTURE DE POSITIONS\n"
        "  /vendu NOM       → prix TP automatique\n"
        "  /vendu NOM PRIX  → prix manuel\n"
        "  /close TICKER QTE PRIX FRAIS → avec frais\n"
        "\n"
        "SYNC GMAIL BOURSE DIRECT\n"
        "Detecte les emails 'Finalisation strategie'\n"
        "et cloture auto les positions concernees.\n"
        "\n"
        "  .env :\n"
        "    GMAIL_USER=ton@gmail.com\n"
        "    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx\n"
        "  Mot de passe app :\n"
        "    myaccount.google.com/apppasswords\n"
        "\n"
        "  /syncmail → verifie maintenant\n"
        "  Auto : check aux horaires (9/12/15/17h)\n"
        "\n"
        "STATS\n"
        "  /stats → win rate, P&L, profit factor",
        cid,
    )


def _tuto_update(cid):
    send(
        "Mettre a jour le bot\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. Recupere les nouveautes :\n"
        "   git pull origin main\n"
        "\n"
        "2. Relance le bot :\n"
        "   pkill -f main.py\n"
        "   venv/bin/python3 main.py > tradingbot.log 2>&1 &\n"
        "\n"
        "Voir ce qui a change :\n"
        "   git log --oneline -10\n"
        "\n"
        "Verifier la version actuelle :\n"
        "   /update\n"
        "\n"
        "Code source :\n"
        "github.com/myopencomm/Tradingbot",
        cid,
    )


# ─── Mode Playwright ────────────────────────────────────────────────────────

def cmd_mode(args, cid):
    mode = bot_mode.get_mode()
    if mode == bot_mode.BotMode.PLAYWRIGHT:
        age = playwright_session.session_age_str()
        connected = playwright_session.is_connected()
        status = f"connecte depuis {age}" if connected else "session non connectee"
        send(
            f"Mode actuel : Playwright ({status})\n\n"
            f"Commandes disponibles :\n"
            f"/disconnect — fermer la session et revenir en mode Classic\n"
            f"/sync — synchroniser le portefeuille depuis Bourse Direct",
            cid,
        )
    else:
        send(
            "Mode actuel : Classic\n"
            "Les donnees viennent de Yahoo Finance.\n"
            "Les screenshots sont analyses par vision IA.\n\n"
            "/connect — activer le mode Playwright (Bourse Direct live)",
            cid,
        )


def cmd_connect(args, cid):
    if bot_mode.is_playwright() and playwright_session.is_connected():
        send(f"Deja connecte a Bourse Direct (session active depuis {playwright_session.session_age_str()}).", cid)
        return

    send("Lancement de la connexion a Bourse Direct...", cid)

    def _do_connect():
        ok = playwright_session.start()
        if not ok:
            send("Impossible de lancer Playwright. Verifie que playwright est installe (pip install playwright && playwright install chromium).", cid)
            return

        success = bourse_direct_auth.login(lambda msg: send(msg, cid))
        if success:
            bot_mode.set_mode(bot_mode.BotMode.PLAYWRIGHT)
            send(
                "Mode Playwright actif\n"
                "Connecte a Bourse Direct\n\n"
                "/sync — synchroniser le portefeuille\n"
                "/disconnect — revenir en mode Classic",
                cid,
            )
        else:
            playwright_session.stop()

    threading.Thread(target=_do_connect, daemon=True).start()


def cmd_disconnect(args, cid):
    if not bot_mode.is_playwright():
        send("Deja en mode Classic.", cid)
        return
    playwright_session.stop()
    bot_mode.set_mode(bot_mode.BotMode.CLASSIC)
    send(
        "Session Playwright fermee.\n"
        "Mode Classic actif.\n"
        "Les screenshots et Yahoo Finance restent disponibles.",
        cid,
    )


def cmd_sync(args, cid):
    if not bot_mode.is_playwright():
        send("Le mode Playwright n'est pas actif. /connect pour l'activer.", cid)
        return
    if not playwright_session.is_connected():
        send("Session Playwright non connectee. /connect pour relancer.", cid)
        return
    import sync_engine
    threading.Thread(
        target=sync_engine.sync,
        args=(lambda m: send(m, cid),),
        daemon=True,
    ).start()


# ─── Routeur ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/help": cmd_help,
    "/start": cmd_start,
    "/status": cmd_status,
    "/mode": cmd_mode,
    "/connect": cmd_connect,
    "/disconnect": cmd_disconnect,
    "/sync": cmd_sync,
    "/cash": cmd_cash,
    "/add": cmd_add,
    "/remove": cmd_remove,
    "/sl": cmd_sl,
    "/tp": cmd_tp,
    "/buy": cmd_buy,
    "/order": cmd_order,
    "/setup": cmd_setup,
    "/stats": cmd_stats,
    "/close": cmd_close,
    "/attente": cmd_attente,
    "/annuler": cmd_annuler,
    "/vendu": cmd_vendu,
    "/syncmail": cmd_syncmail,
    "/update": cmd_update,
    "/morning": cmd_morning,
    "/scan": cmd_scan,
    "/research": cmd_research,
    "/import": cmd_import,
    "/tuto": cmd_tuto,
}


def _handle_message(message: dict):
    cid = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    doc = message.get("document")
    photo = message.get("photo")

    # Screenshot portefeuille (photo envoyée dans le chat)
    if photo:
        _handle_photo(photo, cid)
        return

    # Import CSV via fichier joint
    if doc and str(doc.get("file_name", "")).lower().endswith(".csv"):
        _handle_csv(doc, cid)
        return

    if not text.startswith("/"):
        # Relay 2FA : si une connexion Playwright attend un code OTP
        if bourse_direct_auth.is_waiting_for_otp() and text.strip().isdigit() and len(text.strip()) >= 4:
            bourse_direct_auth.set_otp(text)
        return

    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler:
        try:
            handler(args, cid)
        except Exception as e:
            send(f"Erreur commande {cmd}: {e}", cid)
    else:
        send(f"Commande inconnue: {cmd}\n/help pour la liste.", cid)


def _download_photo(photos: list) -> bytes | None:
    """Télécharge la meilleure résolution d'une photo Telegram."""
    try:
        file_id = photos[-1]["file_id"]
        path = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10,
        ).json()["result"]["file_path"]
        return requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}", timeout=20
        ).content
    except Exception as e:
        print(f"Photo download error: {e}")
        return None


def _flush_photo_batch(cid: str):
    """Appelé par le timer : traite toutes les photos bufférisées."""
    with _buf_lock:
        batch = _photo_buf.pop(cid, None)
    if not batch:
        return
    images = batch["images"]
    n = len(images)
    send(f"Analyse de {n} capture{'s' if n > 1 else ''} en cours...", cid)
    threading.Thread(
        target=lambda: send(analysis.import_screenshots(images), cid),
        daemon=True,
    ).start()


def _handle_photo(photos: list, cid: str):
    """
    Bufférise les photos pendant BUFFER_WAIT secondes après la dernière reçue,
    puis traite tout le batch d'un coup pour reconstituer le portefeuille complet.
    """
    img = _download_photo(photos)
    if img is None:
        send("Erreur téléchargement de l'image.", cid)
        return

    with _buf_lock:
        if cid not in _photo_buf:
            # Première photo du batch
            send(
                f"Screenshot reçu. Envoie toutes tes captures (scroll), "
                f"j'analyse dans {BUFFER_WAIT}s...",
                cid,
            )
            _photo_buf[cid] = {"images": [], "timer": None}
        else:
            # Photo suivante — on annule le timer précédent
            t = _photo_buf[cid].get("timer")
            if t:
                t.cancel()

        _photo_buf[cid]["images"].append(img)
        timer = threading.Timer(BUFFER_WAIT, _flush_photo_batch, args=[cid])
        _photo_buf[cid]["timer"] = timer
        timer.start()


def _handle_csv(doc: dict, cid: str):
    try:
        fid = doc["file_id"]
        path = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": fid}, timeout=10,
        ).json()["result"]["file_path"]
        content = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}", timeout=15
        ).text

        parsed = portfolio.import_from_csv(content)
        if not parsed:
            send("Aucune position trouvee dans le CSV. Verifie le format (separateur ;).", cid)
            return

        from config import DEFAULT_SL_PCT, DEFAULT_TP_PCT
        existing = portfolio.get_positions()
        existing_tickers = {cfg["ticker"].upper() for cfg in existing.values()}
        existing_keys = set(existing.keys())

        added, skipped, errors, breach_alerts = [], [], [], []
        for p in parsed:
            key = p["name"].upper().replace(" ", "_")[:20]
            if key in existing_keys:
                skipped.append(p)
                continue
            try:
                sl = round(p["pru"] * (1 - DEFAULT_SL_PCT / 100), 2)
                tp = round(p["pru"] * (1 + DEFAULT_TP_PCT / 100), 2)
                portfolio.add_position(key, key + ".PA", p["qty"], p["pru"], sl, tp)
                added.append({**p, "key": key, "sl": sl, "tp": tp})
                warning = analysis._breach_warning(key + ".PA", p["pru"], sl)
                if warning:
                    breach_alerts.append(f"  {p['name']} — {warning}")
            except Exception as e:
                errors.append(f"{p['name']} ({e})")

        lines = []
        if added:
            lines.append(f"Importe — {len(added)} position(s) :")
            for p in added:
                lines.append(f"  + {p['name']} {p['qty']}t @ {p['pru']}€ | SL {p['sl']}€ | TP {p['tp']}€")
            lines.append(f"SL -{DEFAULT_SL_PCT:.0f}% et TP +{DEFAULT_TP_PCT:.0f}% appliques.")
            lines.append("Verifie les tickers avec /status puis corrige si besoin (/remove + /add).")
            if breach_alerts:
                lines.append("\nAlertes :")
                lines.extend(breach_alerts)
        if skipped:
            lines.append(f"\nDeja dans le portfolio ({len(skipped)} ignores) :")
            for p in skipped:
                lines.append(f"  = {p['name']}")
        if errors:
            lines.append(f"\nErreurs : {', '.join(errors)}")
        send("\n".join(lines) if lines else "Aucune nouvelle position a importer.", cid)

    except Exception as e:
        send(f"Erreur import CSV: {e}", cid)


# ─── Polling ────────────────────────────────────────────────────────────────

def _poll():
    offset = None
    print("✅ Telegram polling demarre")
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            data = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params, timeout=35,
            ).json()
            for upd in data.get("result", []):
                if "message" in upd:
                    _handle_message(upd["message"])
                offset = upd["update_id"] + 1
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)


def start_polling():
    t = threading.Thread(target=_poll, daemon=True, name="telegram-poll")
    t.start()
    return t
