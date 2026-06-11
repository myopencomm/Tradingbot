"""
Interface Telegram : polling + commandes interactives.
Toutes les commandes sont disponibles depuis l'app iPhone/web.
"""
import requests
import time
import threading
from config import TELEGRAM_TOKEN, CHAT_ID, GMAIL_USER, GMAIL_APP_PASSWORD, DEFAULT_SL_PCT, DEFAULT_TP_PCT
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


# ─── Indicateur « écrit… » (trois points) ────────────────────────────────────

class _typing:
    """Affiche « écrit… » dans Telegram tant que le bloc with est actif.

    Telegram efface l'indicateur après ~5 s ou dès qu'un message est envoyé :
    on le renvoie donc toutes les 4 s jusqu'à la fin du traitement.
    """
    def __init__(self, chat_id: str = None):
        self.chat_id = chat_id or CHAT_ID
        self._stop = threading.Event()

    def __enter__(self):
        if not TELEGRAM_TOKEN:
            return self
        def loop():
            while not self._stop.is_set():
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
                        json={"chat_id": self.chat_id, "action": "typing"},
                        timeout=5,
                    )
                except Exception:
                    pass
                self._stop.wait(4)
        threading.Thread(target=loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False


def _run_long(cid, fn, *args, **kwargs):
    """Exécute fn dans un thread avec l'indicateur « écrit… » jusqu'à la fin."""
    def worker():
        with _typing(cid):
            fn(*args, **kwargs)
    threading.Thread(target=worker, daemon=True).start()


# ─── Menu de commandes (bouton bas-gauche Telegram) ──────────────────────────
# Liste affichée dans le petit menu de l'app Telegram. Ordre = priorité d'usage.
# Noms sans le slash, minuscules, [a-z0-9_], descriptions courtes.

BOT_COMMANDS = [
    ("status",     "Voir mon portefeuille"),
    ("cash",       "Cash dispo  |  /cash 1234 le definir"),
    ("stats",      "Bilan : win rate, P&L, profit factor"),
    ("morning",    "Briefing du jour (macro + positions + opps)"),
    ("scan",       "Meilleures opportunites avec mon cash"),
    ("research",   "Analyser une action — /research TICKER"),
    ("add",        "Ajouter une position — TICKER QTE PRU SL TP"),
    ("remove",     "Retirer une position — /remove TICKER"),
    ("sl",         "Changer le stop-loss — /sl TICKER PRIX"),
    ("tp",         "Changer le take-profit — /tp TICKER PRIX"),
    ("vendu",      "Enregistrer une vente — /vendu NOM [PRIX]"),
    ("close",      "Vente avec frais — TICKER QTE PRIX [FRAIS]"),
    ("setup",      "Texte ordres protection SL+TP — TICKER QTE PRU"),
    ("buy",        "Texte ordre Expert achat+SL+TP — TICKER QTE PRU"),
    ("order",      "1 ordre simple (texte) — buy|sell TICKER QTE PRIX"),
    ("attente",    "Ordre en attente, alerte au cours — NOM TICKER QTE PRIX"),
    ("annuler",    "Annuler un ordre en attente (bot) — /annuler NOM"),
    ("connect",    "Se connecter a Bourse Direct (code TOTP)"),
    ("sync",       "Lire portefeuille + ordres reels depuis BD"),
    ("ordre",      "Passer un ordre reel sur BD — acheter|vendre TICKER QTE ..."),
    ("annuler_bd", "Annuler un ordre en cours sur BD — /annuler_bd TICKER"),
    ("mode",       "Etat connexion BD"),
    ("disconnect", "Repasser en mode Classic"),
    ("syncmail",   "Detecter les ventes via emails BD"),
    ("import",     "Guide import CSV"),
    ("tuto",       "Guide pas a pas"),
    ("update",     "Version du bot"),
    ("help",       "Liste complete des commandes"),
]


def set_bot_commands() -> bool:
    """Enregistre le menu de commandes Telegram (bouton bas-gauche)."""
    if not TELEGRAM_TOKEN:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]},
            timeout=10,
        )
        ok = r.status_code == 200 and r.json().get("ok")
        print("✅ Menu Telegram enregistre" if ok else f"⚠️ setMyCommands: {r.text[:120]}")
        return bool(ok)
    except Exception as e:
        print(f"setMyCommands error: {e}")
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
        "TradingBot — Aide\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "VOIR MON PORTEFEUILLE\n"
        "/status — positions + P&L en temps reel\n"
        "/cash — cash dispo  |  /cash 1234 — le definir\n"
        "/stats — bilan (win rate, P&L, profit factor)\n"
        "\n"
        "GERER MES POSITIONS (dans le bot)\n"
        "/add TICKER QTE PRU SL TP — ajouter\n"
        "/remove TICKER — retirer\n"
        "/sl TICKER PRIX — changer le stop-loss\n"
        "/tp TICKER PRIX — changer le take-profit\n"
        "\n"
        "ANALYSE IA\n"
        "/morning — briefing du jour (macro + positions + opps)\n"
        "/scan — meilleures opportunites avec ton cash\n"
        "/research TICKER [question] — analyse d'une action\n"
        "  ex: /research EXENS.PA dois-je vendre ?\n"
        "\n"
        "VENDRE / CLOTURER\n"
        "/vendu NOM [PRIX] — enregistre une vente (prix TP si omis)\n"
        "/close TICKER QTE PRIX [FRAIS] — vente avec frais\n"
        "\n"
        "━━━ 2 FACONS DE PASSER UN ORDRE ━━━\n"
        "\n"
        "A) MODE CLASSIC — le bot ecrit les instructions,\n"
        "   TU les saisis toi-meme sur Bourse Direct :\n"
        "/setup TICKER QTE PRU\n"
        "  → texte des 2 ordres protection (SL -7% + TP +10%)\n"
        "    a poser apres un achat deja fait\n"
        "/buy TICKER QTE PRU\n"
        "  → texte d'1 ordre Expert (achat+SL+TP groupes)\n"
        "/order buy|sell TICKER QTE PRIX — 1 ordre simple\n"
        "/attente NOM TICKER QTE PRIX [SL TP]\n"
        "  → reserve le cash, t'alerte quand le cours est atteint\n"
        "/annuler NOM — annule un ordre en attente (bot)\n"
        "\n"
        "B) MODE PLAYWRIGHT — le bot passe l'ordre\n"
        "   REELLEMENT sur Bourse Direct pour toi :\n"
        "/connect — se connecter a BD (code TOTP)\n"
        "/sync — lire portefeuille + ordres reels depuis BD\n"
        "/ordre acheter|vendre TICKER QTE marche\n"
        "/ordre acheter|vendre TICKER QTE limite PRIX\n"
        "/ordre vendre TICKER QTE expert SL TP\n"
        "  → /oui confirme et envoie  |  /non annule\n"
        "/annuler_bd TICKER — annule un ordre en cours sur BD\n"
        "/mode — etat connexion  |  /disconnect — repasser Classic\n"
        "\n"
        "DETECTION AUTO DES VENTES\n"
        "/syncmail — lit les emails BD 'strategie finalisee'\n"
        "  (utile si tu n'utilises PAS le mode Playwright)\n"
        "\n"
        "IMPORT\n"
        "Envoie une photo de l'app BD → import auto (vision IA)\n"
        "/import — guide import CSV\n"
        "\n"
        "AIDE\n"
        "/tuto — guide pas a pas  |  /update — version\n"
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
    positions = portfolio.get_positions()
    name = _find_position(args[0], positions)
    if not name:
        send(f"Position '{args[0]}' introuvable.\nPositions: {list(positions.keys())}", cid)
        return
    portfolio.remove_position(name)
    send(f"Position {name} supprimee.", cid)


def cmd_sl(args, cid):
    # /sl TICKER PRIX
    if len(args) < 2:
        send("Usage: /sl TICKER PRIX\nEx: /sl LBIRD 22.01", cid)
        return
    try:
        price = float(args[1].replace(",", "."))
    except ValueError:
        send("Prix invalide.", cid)
        return
    data = portfolio.load()
    name = _find_position(args[0], data.get("positions", {}))
    if not name:
        send(f"Position '{args[0]}' introuvable.", cid)
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
    try:
        price = float(args[1].replace(",", "."))
    except ValueError:
        send("Prix invalide.", cid)
        return
    data = portfolio.load()
    name = _find_position(args[0], data.get("positions", {}))
    if not name:
        send(f"Position '{args[0]}' introuvable.", cid)
        return
    if portfolio.update_tp(name, price):
        send(f"TP {name} mis a jour: {price}€", cid)
    else:
        send(f"Erreur mise a jour TP {name}.", cid)


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
    try:
        qty        = int(args[1])
        exit_price = float(args[2].replace(",", "."))
        fees       = float(args[3].replace(",", ".")) if len(args) > 3 else 0.0
    except ValueError:
        send("Format invalide.", cid)
        return

    data = portfolio.load()
    positions = data.get("positions", {})
    name = _find_position(args[0], positions)
    if not name:
        send(f"Position '{args[0]}' introuvable. Positions: {list(positions.keys())}", cid)
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
        sl     = float(args[4].replace(",", ".")) if len(args) > 4 else round(entry * (1 - DEFAULT_SL_PCT / 100), 4)
        tp     = float(args[5].replace(",", ".")) if len(args) > 5 else round(entry * (1 + DEFAULT_TP_PCT / 100), 4)
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


def _find_position(name_input: str, positions: dict) -> str | None:
    """
    Trouve la clé d'une position dans le dict par :
    1. Nom exact (GENFIT)
    2. Ticker base exact (GNFT pour GNFT.PA)
    3. Préfixe unique (GEN si seul GENFIT commence par GEN)
    Retourne None si introuvable ou ambigu.
    """
    key = name_input.upper().split(".")[0]
    if key in positions:
        return key
    for n, cfg in positions.items():
        if cfg["ticker"].split(".")[0].upper() == key:
            return n
    matches = [n for n in positions if n.startswith(key)]
    return matches[0] if len(matches) == 1 else None


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

    data = portfolio.load()
    positions = data.get("positions", {})
    name = _find_position(args[0], positions)
    if not name:
        send(f"Position '{args[0]}' introuvable.\nPositions: {list(positions.keys())}", cid)
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
    _run_long(cid, analysis.morning_briefing, lambda m: send(m, cid))


def cmd_scan(args, cid):
    send("Scan en cours...", cid)
    _run_long(cid, analysis.scan_opportunities, lambda m: send(m, cid))


def cmd_research(args, cid):
    if not args:
        send(
            "Usage: /research TICKER [question]\n"
            "Ex: /research EXENS.PA\n"
            "Ex: /research EXENS.PA dois-je vendre ou tenir ?\n"
            "Ex: /research MSFT est-ce un bon point d'entree ?",
            cid,
        )
        return
    ticker   = args[0].upper()
    question = " ".join(args[1:]) if len(args) > 1 else ""
    msg = f"Analyse de {ticker} en cours..." if not question else f"Analyse de {ticker} — '{question}'"
    send(msg, cid)
    _run_long(cid, analysis.research_ticker, lambda m: send(m, cid), ticker, question)


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
        "Connexion directe a Bourse Direct.\n"
        "Lit le portefeuille en temps reel et\n"
        "passe des ordres depuis Telegram.\n"
        "Les screenshots restent disponibles.\n"
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
        "Mode Playwright — Connexion\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/connect\n"
        "  Lance la connexion a Bourse Direct\n"
        "  2FA TOTP : le bot te demande le code\n"
        "  → Ouvre ton app d'authentification\n"
        "  → Envoie le code a 6 chiffres ici\n"
        "  → Coche 'Oui' quand demande\n"
        "\n"
        "/mode        → etat de la connexion\n"
        "/sync        → sync portefeuille depuis BD\n"
        "/disconnect  → revenir en mode Classic\n"
        "\n"
        "Le bot demarre toujours en mode Classic.\n"
        "/connect requis apres chaque redemarrage.",
        cid,
    )
    time.sleep(0.4)
    send(
        "Mode Playwright — Passer des ordres\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "SYNTAXE\n"
        "  /ordre vendre TICKER QTE marche\n"
        "  /ordre vendre TICKER QTE limite PRIX\n"
        "  /ordre vendre TICKER QTE expert SL TP\n"
        "  /ordre acheter TICKER QTE marche\n"
        "  /ordre acheter TICKER QTE limite PRIX\n"
        "\n"
        "EXEMPLES\n"
        "  /ordre vendre EXENS.PA 17 marche\n"
        "  /ordre vendre GNFT.PA 100 limite 9.80\n"
        "  /ordre vendre LBIRD.PA 48 expert 24.5 28.1\n"
        "  /ordre acheter MSFT 5 marche\n"
        "\n"
        "CONFIRMATION\n"
        "  Le bot affiche le recap + frais\n"
        "  /oui → envoie au marche (irreversible)\n"
        "  /non → annule (timeout 120s)\n"
        "\n"
        "TICKERS : utilise le format yfinance\n"
        "  Euronext : EXENS.PA  GNFT.PA  LBIRD.PA\n"
        "  NASDAQ/NYSE : ILMN  AAPL  MSFT\n"
        "  LSE : BP.L  GSK.L\n"
        "  Xetra : SAP.DE  SIE.DE",
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
            send("Impossible de lancer Playwright. Verifie l'installation (pip install playwright && playwright install chromium).", cid)
            return

        try:
            # login s'exécute dans le thread worker via run()
            success = playwright_session.run(
                lambda page: bourse_direct_auth.login(page, lambda msg: send(msg, cid)),
                timeout=140,  # > OTP_TIMEOUT (90s) pour laisser le temps au 2FA
            )
        except Exception as e:
            send(f"Erreur connexion : {e}", cid)
            playwright_session.stop()
            return

        if success:
            playwright_session.mark_connected()
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

    def _do_sync():
        try:
            playwright_session.run(
                lambda page: sync_engine.sync(page, lambda m: send(m, cid)),
                timeout=90,
            )
        except Exception as e:
            send(f"Erreur sync : {e}", cid)

    _run_long(cid, _do_sync)


# ─── Ordres Playwright ──────────────────────────────────────────────────────

_pending_order: dict | None = None  # {"order_id", "is_expert", "ticker", "summary", "expires"}
_pending_lock = threading.Lock()


def _check_playwright_ready(cid) -> bool:
    if not bot_mode.is_playwright():
        send("Mode Playwright requis. /connect pour l'activer.", cid)
        return False
    if not playwright_session.is_connected():
        send("Session BD non connectee. /connect pour relancer.", cid)
        return False
    return True


def cmd_ordre(args, cid):
    """
    /ordre vendre TICKER QTE marche
    /ordre vendre TICKER QTE limite PRIX
    /ordre vendre TICKER QTE expert SL TP
    /ordre acheter TICKER QTE marche
    /ordre acheter TICKER QTE limite PRIX
    """
    global _pending_order
    if not _check_playwright_ready(cid):
        return
    if len(args) < 4:
        send(
            "Usage :\n"
            "/ordre vendre TICKER QTE marche\n"
            "/ordre vendre TICKER QTE limite PRIX\n"
            "/ordre vendre TICKER QTE expert SL TP\n"
            "/ordre acheter TICKER QTE marche\n"
            "/ordre acheter TICKER QTE limite PRIX\n"
            "Ex: /ordre vendre EXENS.PA 17 expert 56.7 72.45",
            cid,
        )
        return

    sens      = args[0].lower()
    ticker    = args[1].upper()
    try:
        qty   = int(args[2])
    except ValueError:
        send("Quantite invalide.", cid)
        return
    type_arg  = args[3].lower()

    if sens not in ("vendre", "acheter"):
        send("Sens invalide : vendre ou acheter.", cid)
        return

    side = "sell" if sens == "vendre" else "buy"

    import bourse_direct_orders as bd_orders

    # Vérifie que le ticker est résolvable
    info = bd_orders.get_ticker_info(ticker)
    if not info:
        send(f"Ticker {ticker} non reconnu.", cid)
        return

    send(f"Preparation de l'ordre {sens} {qty}x {ticker}...", cid)

    def _do_order():
        global _pending_order
        try:
            if type_arg == "expert":
                if len(args) < 6:
                    send("Expert requiert SL et TP : /ordre vendre TICKER QTE expert SL TP", cid)
                    return
                sl = float(args[4].replace(",", "."))
                tp = float(args[5].replace(",", "."))
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_expert_order(page, ticker, qty, sl, tp)
                )
                is_expert  = True
                summary    = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "meta",
                    limit_price=tp, stop_price=sl
                )
            elif type_arg == "limite":
                if len(args) < 5:
                    send("Limite requiert un prix : /ordre vendre TICKER QTE limite PRIX", cid)
                    return
                prix = float(args[4].replace(",", "."))
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_order(
                        page, ticker, side, qty, order_type="limit", limit_price=prix)
                )
                is_expert  = False
                summary    = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "limit", limit_price=prix
                )
            else:  # marche
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_order(
                        page, ticker, side, qty, order_type="market")
                )
                is_expert  = False
                summary    = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "market"
                )

            if not order_data:
                send(f"Echec creation ordre {ticker}. Verifier session BD (/sync).", cid)
                return

            order_id = order_data.get("id") or order_data.get("order_id")
            with _pending_lock:
                _pending_order = {
                    "order_id":  order_id,
                    "is_expert": is_expert,
                    "ticker":    ticker,
                    "summary":   summary,
                    "expires":   time.time() + 120,
                }

            send(
                f"RECAPITULATIF ORDRE\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{summary}\n\n"
                f"/oui — Envoyer au marche (irreversible)\n"
                f"/non — Annuler (120s timeout)",
                cid,
            )

        except Exception as e:
            send(f"Erreur ordre : {e}", cid)

    _run_long(cid, _do_order)


def cmd_oui(args, cid):
    """Confirme et envoie l'ordre en attente de confirmation."""
    global _pending_order
    if not _check_playwright_ready(cid):
        return

    with _pending_lock:
        pending = _pending_order

    if not pending:
        send("Aucun ordre en attente de confirmation.", cid)
        return
    if time.time() > pending["expires"]:
        with _pending_lock:
            _pending_order = None
        send("Ordre expire (> 120s). Relance /ordre pour recommencer.", cid)
        return

    send("Envoi de l'ordre au marche...", cid)

    def _do_send():
        global _pending_order
        import bourse_direct_orders as bd_orders
        try:
            if pending["is_expert"]:
                result = playwright_session.run(
                    lambda page: bd_orders.execute_strategy(page, pending["order_id"]))
            else:
                result = playwright_session.run(
                    lambda page: bd_orders.send_order(page, pending["order_id"]))

            with _pending_lock:
                _pending_order = None

            if not result:
                send("Envoi echoue — verifier sur BD directement.", cid)
                return

            # ── Vérification post-ordre : relit le carnet pour confirmer ──────
            import bourse_direct_reader as reader
            ticker_base = pending["ticker"].upper().split(".")[0]
            try:
                bd = playwright_session.run(
                    lambda page: reader.get_portfolio(page), timeout=60
                )
                found = False
                if bd:
                    for o in bd.get("orders", []):
                        if (o.get("bd_ticker", "").upper() == ticker_base
                                or ticker_base in (o.get("name", "").upper())):
                            found = True
                            break
                if found:
                    send(f"Ordre envoye et CONFIRME dans le carnet BD\n{pending['summary']}", cid)
                else:
                    send(
                        f"Ordre envoye\n{pending['summary']}\n\n"
                        f"⚠️ Pas encore visible dans le carnet — verifie sur BD dans 1 min.",
                        cid,
                    )
            except Exception:
                send(f"Ordre envoye\n{pending['summary']}\n(verification carnet impossible)", cid)
        except Exception as e:
            send(f"Erreur envoi : {e}", cid)

    _run_long(cid, _do_send)


def cmd_annuler_bd(args, cid):
    """Annule un ordre en cours sur Bourse Direct (mode Playwright)."""
    if not _check_playwright_ready(cid):
        return
    if not args:
        send("Usage: /annuler_bd TICKER\nEx: /annuler_bd EXENS.PA", cid)
        return
    ticker_base = args[0].upper().split(".")[0]
    send(f"Recherche de l'ordre {ticker_base} sur BD...", cid)

    def _do_cancel():
        import bourse_direct_reader as reader
        import bourse_direct_orders as bd_orders
        try:
            bd = playwright_session.run(lambda page: reader.get_portfolio(page), timeout=60)
            if not bd:
                send("Lecture BD impossible.", cid)
                return
            target = None
            for o in bd.get("orders", []):
                if (o.get("bd_ticker", "").upper() == ticker_base
                        or ticker_base in (o.get("name", "").upper())):
                    target = o
                    break
            if not target:
                send(f"Aucun ordre en cours trouve pour {ticker_base}.", cid)
                return
            oid = target.get("order_id")
            if not oid:
                send(f"Ordre {ticker_base} trouve mais order_id illisible. Annule sur BD directement.", cid)
                return
            res = playwright_session.run(lambda page: bd_orders.cancel_order(page, oid), timeout=60)
            if res is not None:
                send(f"Ordre {target.get('name', ticker_base)} ({target.get('type','?')}) annule sur BD.", cid)
            else:
                send("Annulation echouee — verifier sur BD.", cid)
        except Exception as e:
            send(f"Erreur annulation : {e}", cid)

    _run_long(cid, _do_cancel)


def cmd_non(args, cid):
    """Annule l'ordre en attente de confirmation."""
    global _pending_order
    with _pending_lock:
        if _pending_order:
            ticker = _pending_order.get("ticker", "")
            _pending_order = None
            send(f"Ordre {ticker} annule.", cid)
        else:
            send("Aucun ordre en attente.", cid)


# ─── Routeur ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/help": cmd_help,
    "/start": cmd_start,
    "/status": cmd_status,
    "/mode": cmd_mode,
    "/connect": cmd_connect,
    "/disconnect": cmd_disconnect,
    "/sync": cmd_sync,
    "/ordre": cmd_ordre,
    "/oui": cmd_oui,
    "/non": cmd_non,
    "/annuler_bd": cmd_annuler_bd,
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
            # « écrit… » pendant toute commande synchrone (ex: /status qui
            # fetch les cours). Les handlers threadés gardent leur _run_long.
            with _typing(cid):
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
    _run_long(cid, lambda: send(analysis.import_screenshots(images), cid))


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
    set_bot_commands()
    t = threading.Thread(target=_poll, daemon=True, name="telegram-poll")
    t.start()
    return t
