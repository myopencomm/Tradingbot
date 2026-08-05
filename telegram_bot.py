"""
Interface Telegram : polling + commandes interactives.
Toutes les commandes sont disponibles depuis l'app iPhone/web.
"""
import requests
import time
import threading
from config import (TELEGRAM_TOKEN, CHAT_ID, AUTHORIZED_CHAT_IDS,
                    GMAIL_USER, GMAIL_APP_PASSWORD,
                    DEFAULT_SL_PCT, DEFAULT_TP_PCT, BREAKEVEN_THRESHOLD)
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
    # Trace compacte de TOUT message sortant : indispensable pour diagnostiquer
    # a posteriori pourquoi le bot a pris (ou pas) une décision.
    flat = text.replace("\n", " ⏎ ")
    print(f"[TG] {flat[:200]}")
    if not TELEGRAM_TOKEN:
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


def send_editable(text: str, chat_id: str = None) -> int | None:
    """Envoie un message et retourne son message_id (pour édition/suppression)."""
    if not TELEGRAM_TOKEN:
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id or CHAT_ID, "text": text},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"Telegram send_editable error: {e}")
    return None


def edit_message(msg_id: int, text: str, chat_id: str = None) -> bool:
    """Édite un message existant (max 4096 chars, Telegram ignore si identique)."""
    if not TELEGRAM_TOKEN or not msg_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            json={"chat_id": chat_id or CHAT_ID, "message_id": msg_id, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram edit error: {e}")
    return False


def delete_message(msg_id: int, chat_id: str = None) -> bool:
    """Supprime un message Telegram (typiquement un message de progression)."""
    if not TELEGRAM_TOKEN or not msg_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": chat_id or CHAT_ID, "message_id": msg_id},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram delete error: {e}")
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
    ("dashboard",  "Graphique P&L + resume visuel"),
    ("lessons",    "Ce que le bot a appris de ses trades"),
    ("morning",    "Briefing du jour (macro + positions + opps)"),
    ("scan",       "Meilleures opportunites avec mon cash"),
    ("scan_us",    "Scan des valeurs US uniquement (/scan us)"),
    ("research",   "Analyser une action — /research TICKER"),
    ("add",        "Acheter (deduit le cash) — TICKER QTE PRU SL TP"),
    ("remove",     "Retirer une position — /remove TICKER"),
    ("reticker",   "Corriger le ticker Yahoo d'une position — /reticker POSITION TICKER"),
    ("hold",       "HOLD long terme, hors gestion bot — /hold TICKER [off]"),
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
    ("auto",       "Mode autonome — /auto on 500 | off | status"),
    ("sync",       "Lire portefeuille + ordres reels depuis BD"),
    ("trailing",   "Verifier le trailing stop (SL au PRU) maintenant"),
    ("ordre",      "Passer un ordre reel sur BD — acheter|vendre TICKER QTE ..."),
    ("annuler_bd", "Annuler un ordre en cours sur BD — /annuler_bd TICKER"),
    ("mode",       "Etat connexion BD"),
    ("disconnect", "Repasser en mode Classic"),
    ("syncmail",   "Detecter les ventes via emails BD"),
    ("import",     "Guide import CSV"),
    ("fallback",   "IA de secours — /fallback gemini CLE_API"),
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
        "/add TICKER QTE PRU SL TP — acheter (deduit le cash)\n"
        "/remove TICKER — retirer\n"
        "/hold TICKER [off] — HOLD long terme, hors gestion bot\n"
        "/sl TICKER PRIX — changer le stop-loss\n"
        "/tp TICKER PRIX — changer le take-profit\n"
        "\n"
        "ANALYSE IA\n"
        "/morning — briefing du jour (macro + positions + opps)\n"
        "/scan — meilleures opportunites avec ton cash\n"
        "/scan us — valeurs US uniquement (seance 15h35-22h)\n"
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
        f"  → texte des 2 ordres protection (SL -{DEFAULT_SL_PCT:.0f}% + TP +{DEFAULT_TP_PCT:.0f}%)\n"
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
        "/trailing — verifier le trailing stop (SL au PRU) maintenant\n"
        "/ordre acheter TICKER QTE marche [validite]\n"
        "/ordre acheter TICKER QTE limite PRIX [validite]\n"
        "/ordre acheter TICKER QTE expert ENTREE SL TP [validite]\n"
        "/ordre vendre TICKER QTE marche [validite]\n"
        "/ordre vendre TICKER QTE limite PRIX [validite]\n"
        "/ordre vendre TICKER QTE expert SL TP [validite]\n"
        "  validite : seance | max (defaut) | JJ/MM/AAAA\n"
        "  /oui confirme et envoie  |  /non annule\n"
        "/annuler_bd TICKER — annule un ordre en cours sur BD\n"
        "/mode — etat connexion  |  /disconnect — repasser Classic\n"
        "\n"
        "MODE AUTONOME (Playwright requis)\n"
        "/auto on 500   — active avec 500€ de budget\n"
        "/auto positions 3 — nb max de positions simultanees\n"
        "/auto on 20%   — active avec 20% du cash\n"
        "/auto off      — desactive\n"
        "/auto status   — etat + positions autonomes\n"
        "  Le bot scanne, entre, gere SL/TP, releve\n"
        "  le SL au PRU a +3% — tout seul\n"
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
        "\n"
        "GESTION DU BOT (terminal)\n"
        "./bot.sh start|stop|restart|status|logs\n"
        "./bot.sh update — maj en 1 commande\n"
        "./bot.sh autostart — relance auto au boot\n"
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
        # HOLD long terme : affichage informatif, hors P&L trading, pas d'alerte
        if cfg.get("hold"):
            q = prices.get_quote(cfg["ticker"])
            best = portfolio.best_price(cfg, q)
            price = best["price"]
            sym = prices.currency_symbol(best["currency"])
            px = f"{sym}{price}" if price else "cours indispo"
            if price and best["source"] != "yf":
                px += " ᴮᴰ"
            lines.append(
                f"🔒 {name} ({cfg['ticker']}) — HOLD long terme, hors gestion bot\n"
                f"  {cfg['qty']} titres | PRU {sym}{cfg['entry_price']} | {px}"
            )
            continue

        q = prices.get_quote(cfg["ticker"])
        # Cours retenu : yfinance s'il est frais, sinon le relevé BD.
        best = portfolio.best_price(cfg, q)
        price = best["price"]
        if price:
            chg = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
            pnl = (price - cfg["entry_price"]) * cfg["qty"]
            total_pnl += pnl
            arrow  = "+" if chg >= 0 else ""
            sl_tag = " ⚠️ SL DÉPASSÉ" if price < cfg["target_low"] else ""
            tp_tag = " ⚠️ TP DÉPASSÉ" if price > cfg["entry_price"] * 1.25 else ""
            sym    = prices.currency_symbol(best["currency"])
            cur_tag = ""
            if best["source"] != "yf":
                cur_tag = f"\n  ⚠️ {best['note']}"
            if best["currency"] != "EUR" and abs(chg) > 80:
                cur_tag = (f"\n  ❗ Perf aberrante — PRU dans la mauvaise devise ?"
                           f"\n  (/remove {name} puis /add avec PRU/SL/TP en {q['currency']})")
            pend = cfg.get("pending_sl")
            pend_tag = ("" if not pend or pend <= (cfg.get("target_low") or 0) else
                        f"\n  ⏳ SL {pend} calculé mais PAS posé sur BD — "
                        f"le stop actif reste {cfg.get('target_low')}")
            prot = ("" if cfg.get("protected") is not False else
                    "\n  🚨 AUCUN ordre SL/TP actif sur BD — seuils non protecteurs\n"
                    f"  → /ordre vendre {cfg['ticker']} {cfg['qty']} expert "
                    f"{cfg.get('target_low')} {cfg.get('target_high')}")
            lines.append(
                f"{name} ({cfg['ticker']})\n"
                f"  Prix: {sym}{price} ({arrow}{chg:.2f}%) | P&L: {sym}{pnl:+.0f}{sl_tag}{tp_tag}\n"
                f"  PRU: {sym}{cfg['entry_price']} | {cfg['qty']} titres\n"
                f"  SL: {sym}{cfg['target_low']}  TP: {sym}{cfg['target_high']}{pend_tag}{prot}{cur_tag}"
            )
        else:
            # Le relevé BD tranche : un titre que le courtier valorise n'est pas
            # suspendu, c'est le ticker stocké qui est faux (cas NVDA.PA).
            code, msg = portfolio.quote_problem(cfg, q)
            if code == "ticker":
                lines.append(
                    f"{name} ({cfg['ticker']})\n"
                    f"  🚨 {msg}\n"
                    f"  Corriger : /reticker {name} <TICKER_YAHOO>\n"
                    f"  PRU: {cfg['entry_price']} | {cfg['qty']} titres"
                )
            elif code == "suspended":
                lines.append(
                    f"{name} ({cfg['ticker']})\n"
                    f"  ⛔ {msg} (liquidation judiciaire ?)\n"
                    f"  PRU: {cfg['entry_price']}€ | {cfg['qty']} titres"
                )
            else:
                lines.append(f"{name}: {msg} | PRU {cfg['entry_price']}€")

    lines.append(f"\nP&L total positions gérées (hors HOLD): {total_pnl:+.0f}€")

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

        # Fail-safe newbie : si le ticker ne cote pas sur Yahoo, c'est
        # probablement un nom de societe (LVMH, GOOGLE...). On cherche le
        # vrai ticker et on prepare la commande corrigee — sans rien ajouter.
        q = prices.get_quote(ticker)
        if not q.get("price"):
            if "." not in ticker:
                sugg = prices.search_ticker(args[0], max_results=3)
                if sugg:
                    lines = [f"❓ {ticker} n'est pas un ticker Yahoo valide."]
                    lines.append("Tu cherchais peut-etre :")
                    for s in sugg:
                        lines.append(f"  • {s['symbol']} — {s['name']} ({s['exchange']})")
                    lines.append("")
                    lines.append("Commande prete avec le 1er resultat :")
                    lines.append(f"/add {sugg[0]['symbol']} {qty} {pru} {sl} {tp}")
                    lines.append("")
                    lines.append("Position NON ajoutee — verifie et relance.")
                    send("\n".join(lines), cid)
                    return
                send(
                    f"❓ {ticker} introuvable sur Yahoo Finance et aucune "
                    f"suggestion.\nFormat : .PA pour Euronext Paris (ex: MC.PA), "
                    f".DE pour Xetra, rien pour NYSE/NASDAQ.\nPosition NON ajoutee.",
                    cid,
                )
                return
            # Ticker avec suffixe de place : probablement une vraie valeur
            # suspendue (ex: import GVN) — on ajoute avec avertissement.
            send(f"⚠️ {ticker} ne renvoie aucune cotation (suspendu ?) — ajout quand meme.", cid)

        # Si un ordre en attente existait pour cette valeur, l'annuler sans rendre le cash
        # (le cash était déjà réservé = déjà déduit du disponible)
        # Recherche par nom exact OU par ticker (évite les écarts de nommage)
        data = portfolio.load()
        pending = data.get("pending_orders", {})
        pending_key = name if name in pending else next(
            (k for k, v in pending.items() if v.get("ticker") == ticker), None
        )
        had_pending = pending_key is not None
        cost = round(qty * pru, 2)
        if had_pending:
            # Cash déjà réservé à la pose de l'ordre — on ajuste juste l'écart
            # entre le montant réservé et le coût réel d'exécution.
            reserved = pending[pending_key].get("reserved_cash", 0)
            pending.pop(pending_key, None)
            data["cash_available"] = round(data.get("cash_available", 0) + reserved - cost, 2)
        else:
            # Achat direct : on déduit le coût du cash disponible
            data["cash_available"] = round(data.get("cash_available", 0) - cost, 2)
        portfolio.save(data)

        portfolio.add_position(name, ticker, qty, pru, sl, tp)
        new_cash = portfolio.get_cash()
        note = " (ordre en attente cloture)" if had_pending else ""
        send(
            f"Position ajoutee: {name}{note}\n"
            f"{qty}t @ PRU {pru}€ | SL {sl}€ | TP {tp}€\n"
            f"💰 Cash : -{cost}€ → {new_cash}€",
            cid,
        )

        # Garde-fou devise : un titre cote en USD avec un PRU saisi en EUR
        # fausse toutes les perfs et déclenche de fausses alertes TP
        cur = q.get("currency", "EUR")
        if cur != "EUR" and q.get("price"):
            csym = prices.currency_symbol(cur)
            warn = (f"⚠️ {ticker} cote en {cur} — PRU, SL et TP doivent etre "
                    f"saisis en {cur} (cours actuel : {csym}{q['price']}).")
            if abs(pru / q["price"] - 1) > 0.5:
                warn += (f"\n❗ Ton PRU ({pru}) est tres eloigne du cours "
                         f"({csym}{q['price']}) — PRU saisi en EUR ?\n"
                         f"Corriger : /remove {name} puis /add en {cur}.")
            send(warn, cid)
        if new_cash < 0:
            send(
                f"⚠️ Cash negatif ({new_cash}€) — si cette position etait deja "
                f"comptee dans ton cash (import d'une position existante), "
                f"corrige avec /cash MONTANT_REEL.",
                cid,
            )
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


def cmd_reticker(args, cid):
    """Corrige le ticker Yahoo d'une position SANS la recréer.

    Un ticker faux rend la position invisible du suivi (aucun cours → ni SL, ni
    TP, ni trailing). La réparer par /remove + /add perdrait tout le reste :
    flag autonome, PRU brut BD, contexte d'entrée, compteurs de notification.
    """
    if len(args) < 2:
        send("Usage: /reticker POSITION TICKER_YAHOO\n"
             "Ex: /reticker NVDA NVDA   (corrige NVDA.PA → NVDA)", cid)
        return
    positions = portfolio.get_positions()
    name = _find_position(args[0], positions)
    if not name:
        send(f"Position '{args[0]}' introuvable.\nPositions: {list(positions.keys())}", cid)
        return
    new_t = args[1].strip().upper()
    old_t = positions[name].get("ticker")
    if new_t == (old_t or "").upper():
        send(f"{name} est déjà sur {new_t} — rien à changer.", cid)
        return

    # Vérifié AVANT d'écrire : remplacer un ticker faux par un autre ticker faux
    # laisserait la position tout aussi aveugle, sans que rien ne le signale.
    q = prices.get_quote(new_t)
    if not q.get("price"):
        send(f"❌ {new_t} ne renvoie aucun cours sur Yahoo — ticker refusé.\n"
             f"{name} reste sur {old_t}.\n"
             f"Rappel : US sans suffixe (NVDA), Paris .PA, Amsterdam .AS, "
             f"Bruxelles .BR, Londres .L, Xetra .DE", cid)
        return

    data = portfolio.load()
    data["positions"][name]["ticker"] = new_t
    portfolio.save(data)
    sym = prices.currency_symbol(q.get("currency", "EUR"))
    send(f"✅ {name} : ticker corrigé {old_t} → {new_t}\n"
         f"Cours retrouvé : {sym}{q['price']}\n"
         f"La position repasse sous surveillance (SL/TP/trailing).", cid)


def cmd_hold(args, cid):
    # /hold TICKER [off] — marque une position HOLD long terme (hors gestion bot) :
    # plus d'alertes SL/TP, hors P&L trading, jamais proposée à la vente/swap.
    if not args:
        holds = {k: v for k, v in portfolio.get_positions().items() if v.get("hold")}
        if holds:
            lines = ["🔒 Positions HOLD long terme (hors gestion bot) :"]
            for name, cfg in holds.items():
                lines.append(f"  {name} ({cfg['ticker']}) — {cfg.get('hold_note', '')}")
            lines.append("\n/hold TICKER off pour remettre en gestion")
            send("\n".join(lines), cid)
        else:
            send("Aucune position HOLD.\nUsage: /hold TICKER [off]", cid)
        return
    positions = portfolio.get_positions()
    name = _find_position(args[0], positions)
    if not name:
        send(f"Position '{args[0]}' introuvable.\nPositions: {list(positions.keys())}", cid)
        return
    off = len(args) > 1 and args[1].lower() in ("off", "non", "no")
    from datetime import datetime
    note = f"HOLD long terme (décision du {datetime.now().strftime('%d/%m/%Y')}) — hors gestion bot"
    portfolio.set_hold(name, not off, note)
    if off:
        send(f"🔓 {name} remis en gestion bot : alertes SL/TP et P&L trading réactivés.", cid)
    else:
        send(
            f"🔒 {name} marqué HOLD long terme — hors gestion bot :\n"
            f"- plus d'alertes SL/TP ni trailing stop\n"
            f"- exclu du P&L trading (/stats)\n"
            f"- jamais proposé à la vente ou au swap par l'IA\n"
            f"- le sync BD continue de suivre la quantité/PRU\n"
            f"/hold {name} off pour annuler",
            cid,
        )


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
    # Un cours manquant amputait le latent en silence (bug 29/07) :
    # on nomme les positions non valorisées plutôt que de mentir.
    if s.get("unpriced"):
        lines.append(f"  ⚠️ INCOMPLET — cours indisponible pour "
                     f"{', '.join(s['unpriced'])} (exclues du latent)")
    lines.append(f"\nTOTAL P&L     : {s['total_pnl']:+.0f}€")
    if s.get("api_cost_eur"):
        lines.append(f"Couts API IA  : -{s['api_cost_eur']:.2f}€ "
                     f"(dont {s['api_month_eur']:.2f}€ ce mois)")
        if s.get("api_model"):
            lines.append(f"Modele servi  : {s['api_model']} (30 derniers jours)")
        lines.append(f"NET apres IA  : {s['net_pnl']:+.0f}€")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    send("\n".join(lines), cid)


_incoming_msg_id = None

_PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}


def _set_env_var(key: str, value: str):
    """Écrit/remplace KEY=value dans .env (préserve le reste) + os.environ.
    Le .env est gitignoré : la clé ne quitte jamais la machine."""
    import os
    from pathlib import Path
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def cmd_fallback(args, cid):
    """
    /fallback                     → état de la chaîne IA
    /fallback gemini CLE_API      → enregistre + teste + active le fallback
    /fallback gemini              → (ré)active un fallback dont la clé est déjà connue
    /fallback off                 → désactive tous les fallbacks
    Le message contenant la clé est SUPPRIMÉ du chat après traitement.
    """
    import os
    import ai_provider
    from config import AI_PROVIDER

    if not args:
        chain = ai_provider.get_fallback_chain()
        lines = [f"CHAÎNE IA\nPrincipal : {AI_PROVIDER}"]
        if chain:
            for name in chain:
                key = os.environ.get(_PROVIDER_ENV_KEYS[name], "")
                masked = f"…{key[-4:]}" if key else "⚠️ clé absente"
                lines.append(f"Fallback  : {name} (clé {masked})")
        else:
            lines.append("Fallback  : aucun")
        lines.append("\n/fallback gemini CLE_API pour en ajouter un\n"
                     "/fallback off pour tout désactiver")
        send("\n".join(lines), cid)
        return

    if args[0].lower() == "off":
        _set_env_var("AI_FALLBACK_PROVIDERS", "")
        send("Fallbacks IA désactivés.", cid)
        return

    name = args[0].lower()
    if name not in _PROVIDER_ENV_KEYS:
        send(f"Provider inconnu : {name}\nValides : {', '.join(_PROVIDER_ENV_KEYS)}", cid)
        return
    if name == AI_PROVIDER:
        send(f"{name} est déjà le provider PRINCIPAL — choisis-en un autre en fallback.", cid)
        return

    env_key = _PROVIDER_ENV_KEYS[name]

    # Clé fournie → confidentialité d'abord : suppression du message du chat
    # (la clé ne doit pas rester lisible dans l'historique Telegram).
    if len(args) >= 2:
        new_key = args[1].strip()
        if _incoming_msg_id:
            deleted = delete_message(_incoming_msg_id, cid)
            note = ("🗑️ Ton message avec la clé a été supprimé du chat."
                    if deleted else
                    "⚠️ Impossible de supprimer ton message — efface-le manuellement.")
        else:
            note = "⚠️ Efface manuellement ton message contenant la clé."
        os.environ[env_key] = new_key   # provisoire, le temps du test
    elif not os.environ.get(env_key):
        send(f"Aucune clé connue pour {name}.\nUsage : /fallback {name} CLE_API", cid)
        return
    else:
        note = ""

    # Test réel de la clé AVANT de persister
    send(f"Test de la clé {name}…", cid)
    try:
        resp = ai_provider._PROVIDERS[name]().complete_cheap("Réponds uniquement : OK", max_tokens=10)
        if not resp:
            raise RuntimeError("réponse vide")
    except Exception as e:
        send(f"❌ Clé {name} invalide ou service indisponible : {str(e)[:200]}\n"
             f"Rien n'a été enregistré.", cid)
        os.environ.pop(env_key, None)
        return

    # Persistance : clé (si fournie) + ajout à la chaîne de fallback
    if len(args) >= 2:
        _set_env_var(env_key, args[1].strip())
    current = [p for p in os.environ.get("AI_FALLBACK_PROVIDERS", "").split(",") if p.strip()]
    if name not in current:
        current.append(name)
    _set_env_var("AI_FALLBACK_PROVIDERS", ",".join(current))

    key_now = os.environ.get(env_key, "")
    send(f"✅ Fallback {name} ACTIF (clé …{key_now[-4:]}, testée).\n"
         f"Chaîne IA : {AI_PROVIDER} → {' → '.join(current)}\n"
         f"Clé stockée uniquement dans .env local (gitignoré). {note}", cid)


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
    import os
    project_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        local_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir, text=True
        ).strip()
        local_short = local_hash[:7]
        local_info = subprocess.check_output(
            ["git", "log", "-1", "--format=%ad %s", "--date=format:%d/%m/%Y"],
            cwd=project_dir, text=True
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
            _b = portfolio.best_price(cfg)
            exit_price = _b["price"]
            price_source = "cours live" if _b["source"] == "yf" else f"cours {_b['source']}"
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


_scan_lock = threading.Lock()


def cmd_scan(args, cid):
    """/scan — univers complet. /scan us — valeurs US uniquement.

    Le scan US automatique ne tourne qu'à US_SCAN_TIME (16h). Cette variante
    permet de le relancer à la demande pendant la séance de Wall Street
    (15h35-22h) : 36 tickers au lieu de ~100, donc plus rapide et bien moins
    coûteux en appels IA que le scan complet.
    """
    us_only = bool(args) and args[0].lower() in ("us", "usa", "🇺🇸")

    if not _scan_lock.acquire(blocking=False):
        send("Scan déjà en cours, patiente...", cid)
        return

    label = "🇺🇸 Scan US en cours..." if us_only else "🔍 Scan en cours..."
    prog_id = send_editable(label, cid)

    def update_fn(text: str):
        # Édite le message de progression en place
        edit_message(prog_id, text, cid)

    def send_final(text: str):
        # Supprime le message de progression puis envoie le résultat final
        delete_message(prog_id, cid)
        send(text, cid)

    def _run():
        try:
            if us_only:
                # Marché fermé : les opportunités validées resteront en file
                # jusqu'à l'ouverture — on le dit plutôt que de laisser croire
                # à une entrée imminente.
                import autonomous_engine
                if not autonomous_engine.market_open_for("NVDA"):
                    send("ℹ️ Séance US fermée (ouverture 15h35 Paris) — les "
                         "opportunités validées attendront l'ouverture.", cid)
                # Pas de plancher de cash ici : contrairement au scan US
                # PLANIFIÉ, une demande explicite doit toujours répondre.
                # Univers US : cache découvert automatiquement si frais
                # (~2500 valeurs liquides issues de la liste officielle
                # Nasdaq Trader), sinon repli sur les 36 curatées.
                us_univ, us_ind = list(analysis.US_UNIVERSE), {}
                try:
                    import market_universe
                    us_ind = market_universe.load_indicators("us")
                    if us_ind:
                        us_univ = sorted(set(us_univ) | set(us_ind))
                        send(f"🇺🇸 Univers US étendu : {len(us_univ)} valeurs "
                             f"(liste officielle Nasdaq Trader, filtrées par liquidité).", cid)
                except Exception as e:
                    print(f"[scan us] univers etendu indisponible : {e}")
                analysis.scan_opportunities(
                    send_final, universe=us_univ, precomputed=us_ind,
                    scan_label="🇺🇸 ", update_fn=update_fn,
                )
            else:
                analysis.scan_opportunities(send_final, update_fn=update_fn)
        finally:
            _scan_lock.release()

    _run_long(cid, _run)


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
            "  Mode Playwright : connexion BD, ordres\n"
            "  Expert achat/vente, validite, mode auto\n"
            "\n"
            "/tuto avance\n"
            "  Fonctions avancees : ordres en attente,\n"
            "  trailing stop, Gmail sync, stats\n"
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
        "  ./bot.sh start\n"
        "\n"
        "Le bot tourne en arriere-plan — tu peux\n"
        "fermer le terminal. Envoie /start ici\n"
        "pour verifier.\n"
        "\n"
        "Recommande — demarrage auto au boot du Mac/PC\n"
        "+ relance automatique apres un crash :\n"
        "  ./bot.sh autostart\n"
        "\n"
        "Autres commandes :\n"
        "  ./bot.sh status   → tourne ou pas ?\n"
        "  ./bot.sh logs     → logs en direct\n"
        "  ./bot.sh stop     → arreter",
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
        "  16h → scan US · 18/20/21h40 → check positions US\n"
        "  Lundi 9h10 → analyse de rotation\n"
        "\n"
        "Le scan US de 16h et la recherche de\n"
        "candidats du briefing sont SAUTES quand\n"
        "aucun achat n'est possible (cash trop bas,\n"
        "ou mode auto sans place libre) : pas\n"
        "d'analyse IA payee pour rien. Un message\n"
        "par jour explique pourquoi. /scan force\n"
        "toujours une analyse complete.\n"
        "\n"
        "A LA DEMANDE\n"
        "  /status   → portefeuille + P&L live\n"
        "  /morning  → briefing maintenant\n"
        "  /scan     → 3 opportunites avec ton cash\n"
        "  /scan us  → valeurs US uniquement\n"
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
        "Mode Playwright — Ordres\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "SYNTAXE (validite optionnelle en dernier)\n"
        "  /ordre acheter TICKER QTE marche\n"
        "  /ordre acheter TICKER QTE limite PRIX\n"
        "  /ordre acheter TICKER QTE expert ENTREE SL TP\n"
        "  /ordre vendre  TICKER QTE marche\n"
        "  /ordre vendre  TICKER QTE limite PRIX\n"
        "  /ordre vendre  TICKER QTE expert SL TP\n"
        "\n"
        "VALIDITE (defaut : max)\n"
        "  seance         → expire fin de seance\n"
        "  max            → jusqu'a fin d'annee\n"
        "  JJ/MM/AAAA     → date precise\n"
        "\n"
        "EXEMPLES\n"
        "  /ordre acheter TTE.PA 3 expert 54.2 49.0 61.0\n"
        "  /ordre acheter MSFT 2 limite 420 seance\n"
        "  /ordre vendre GNFT.PA 100 expert 7.66 9.80\n"
        "  /ordre vendre EXENS.PA 17 marche\n"
        "\n"
        "CONFIRMATION\n"
        "  Le bot affiche recap + montant previsionnel\n"
        "  /oui → envoie au marche (irreversible)\n"
        "  /non → annule (timeout 120s)\n"
        "  /annuler_bd TICKER → annuler un ordre BD\n"
        "\n"
        "TICKERS : format Yahoo Finance\n"
        "  Euronext : EXENS.PA  TTE.PA  ASML.AS\n"
        "  NASDAQ/NYSE : AAPL  MSFT  NVDA\n"
        "  LSE : BP.L  GSK.L  |  Xetra : SAP.DE",
        cid,
    )
    time.sleep(0.4)
    send(
        "Mode Playwright — Mode Autonome\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Le bot gere un budget isole en totale\n"
        "autonomie : scan, entree, suivi, sortie.\n"
        "\n"
        "ACTIVER\n"
        "  /auto on 500     → budget fixe 500€\n"
        "  /auto on 20%     → 20% du cash dispo\n"
        "\n"
        "CE QUE LE BOT FAIT SEUL\n"
        "  - Scan quant + validation IA a chaque check\n"
        "  - Passe les ordres Expert (entree+SL+TP)\n"
        "  - Releve le SL au PRU quand +3% (breakeven)\n"
        "  - Detecte les sorties SL/TP et te notifie\n"
        "\n"
        "BALAYAGE DU RELIQUAT\n"
        "Si le cash restant apres l'achat tombe\n"
        "sous 500EUR, la position est AGRANDIE pour\n"
        "l'absorber : ce fond ne pouvait financer\n"
        "aucun autre trade.\n"
        "  /!\\ la perte au SL grandit d'autant —\n"
        "  elle est annoncee dans le message d'achat.\n"
        "  CASH_SWEEP_MIN_LEFTOVER=0 pour desactiver.\n"
        "\n"
        "  Max 2 positions simultanees | SL/TP garanti\n"
        "  Playwright doit etre connecte pour entrer\n"
        "  Les sorties se gerent via Expert BD (auto)\n"
        "\n"
        "  /auto status    → etat + positions autonomes\n"
        "  /auto off       → desactiver\n"
        "  /auto pause     → suspendre les nouvelles entrees",
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
        "Fonctions avancees — IA de secours\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Si ton IA principale tombe en panne\n"
        "(credits epuises...), le bot peut basculer\n"
        "automatiquement sur une IA de secours :\n"
        "\n"
        "  /fallback gemini TA_CLE_API\n"
        "\n"
        "→ La cle est testee avant activation\n"
        "→ Ton message est supprime du chat\n"
        "  (la cle ne reste pas dans l'historique)\n"
        "→ Stockee uniquement dans .env local\n"
        "→ /fallback = etat | /fallback off = stop\n"
        "\n"
        "Cle Gemini gratuite : aistudio.google.com",
        cid,
    )
    time.sleep(0.4)
    send(
        "Fonctions avancees — Trailing stop\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "DEUX PALIERS, le plus haut l'emporte :\n"
        "\n"
        "1. BREAKEVEN — a +" + f"{BREAKEVEN_THRESHOLD:.0f}" + "% (manuel) / +6%\n"
        "   (autonome) : SL au PRU. Plus de perte\n"
        "   possible sur la position.\n"
        "\n"
        "2. SECURISATION DU GAIN — des 60% du\n"
        "   chemin parcouru du PRU vers le TP :\n"
        "   SL AU-DESSUS du PRU, sur une part du\n"
        "   gain deja acquis. Cette part grandit\n"
        "   quand on approche du TP (50% -> 80%).\n"
        "   Ex: PRU 100, TP 110, cours 108\n"
        "       -> SL a 105.20 (+5.2% verrouilles)\n"
        "   Le SL garde toujours 2% (ou 1xATR) de\n"
        "   marge sous le cours : un stop colle au\n"
        "   cours sort sur du bruit avant le TP.\n"
        "\n"
        "Dans les deux cas :\n"
        "\n"
        "→ Session BD connectee : l'ordre Expert\n"
        "  est REMPLACE automatiquement sur BD\n"
        "  (SL remonte au PRU, TP inchange).\n"
        "  Tu n'as RIEN a faire.\n"
        "→ Deconnecte : alerte avec la commande\n"
        "  /ordre vendre prete a l'emploi.\n"
        "\n"
        "P&L garanti >= 0 des le palier 1, et\n"
        "STRICTEMENT POSITIF des le palier 2.\n"
        "Seules les positions protegees par un\n"
        "Expert actif sont gerees (les positions\n"
        "historiques sans ordre ne sont pas touchees).\n"
        "\n"
        "QUAND le bot verifie :\n"
        "→ chaque heure a :35 (9h-22h, jours\n"
        "  de marche, session BD connectee)\n"
        "→ et des qu'une position franchit son\n"
        "  seuil aux checks 9h/12h/15h/17h.\n"
        "\n"
        "/trailing → verification IMMEDIATE, avec\n"
        "le detail de chaque position (le cycle\n"
        "auto reste silencieux s'il n'a rien a\n"
        "faire ; /trailing repond toujours).",
        cid,
    )
    time.sleep(0.4)
    send(
        "Fonctions avancees — HOLD long terme\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/hold TICKER → sort une position du\n"
        "perimetre de gestion du bot :\n"
        "\n"
        "→ plus d'alertes SL/TP ni trailing\n"
        "→ exclue du P&L trading (/stats)\n"
        "→ jamais proposee a la vente/swap par l'IA\n"
        "→ le sync BD suit toujours qte/PRU\n"
        "\n"
        "Pour les lignes de fond de portefeuille\n"
        "qu'on garde des annees, hors trading.\n"
        "\n"
        "  /hold          → liste les HOLD\n"
        "  /hold TICKER off → remet en gestion",
        cid,
    )
    time.sleep(0.4)
    send(
        "Fonctions avancees — Dashboard\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/dashboard → graphique P&L cumule +\n"
        "resume (win rate, ROI, €/jour) en image.\n"
        "\n"
        "Version web complete (tableau filtrable,\n"
        "positions live, cash engage par deal) :\n"
        "  http://localhost:8642 sur la machine du bot\n"
        "\n"
        "Acces distant via Tailscale :\n"
        "  tailscale serve --bg 8642\n"
        "  (ou DASHBOARD_BIND=0.0.0.0 dans .env)",
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
        "Une seule commande dans le terminal :\n"
        "   ./bot.sh update\n"
        "(git pull + dependances + redemarrage)\n"
        "\n"
        "Gestion du bot au quotidien :\n"
        "   ./bot.sh start|stop|restart|status|logs\n"
        "\n"
        "Demarrage auto au boot + relance apres crash :\n"
        "   ./bot.sh autostart\n"
        "\n"
        "⚠️ Avec autostart actif, ne JAMAIS utiliser\n"
        "pkill — toujours ./bot.sh stop ou restart\n"
        "(sinon double instance du bot).\n"
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
    print("[connect] Lancement de la connexion a Bourse Direct...")

    def _log_and_send(msg):
        print(f"[connect] {msg}")
        send(msg, cid)

    def _do_connect():
        ok = playwright_session.start()
        print(f"[connect] playwright_session.start() -> {ok}")
        if not ok:
            send(
                "Impossible de lancer Playwright (navigateur Chromium manquant ou corrompu).\n\n"
                "Depuis le dossier du bot, lance :\n"
                "  venv/bin/python3 -m playwright install chromium\n\n"
                "Puis relance /connect.\n\n"
                "Astuce : ne pas utiliser 'pip install' seul — le bot utilise son propre venv, "
                "pas le Python systeme (sur macOS 'pip' n'existe souvent pas, seulement 'pip3').",
                cid,
            )
            return

        try:
            # login s'exécute dans le thread worker via run()
            success = playwright_session.run(
                lambda page: bourse_direct_auth.login(page, _log_and_send),
                timeout=140,  # > OTP_TIMEOUT (90s) pour laisser le temps au 2FA
            )
            print(f"[connect] login() -> {success}")
        except Exception as e:
            print(f"[connect] Exception : {e}")
            send(f"Erreur connexion : {e}", cid)
            playwright_session.stop()
            return

        if success:
            playwright_session.mark_connected()
            bot_mode.set_mode(bot_mode.BotMode.PLAYWRIGHT)
            send(
                "Mode Playwright actif\n"
                "Connecte a Bourse Direct\n\n"
                "ORDRES (validite optionnelle : seance | max | JJ/MM/AAAA)\n"
                "/ordre acheter TICKER QTE expert ENTREE SL TP [validite]\n"
                "/ordre acheter TICKER QTE limite PRIX [validite]\n"
                "/ordre acheter TICKER QTE marche [validite]\n"
                "/ordre vendre TICKER QTE expert SL TP [validite]\n"
                "/oui — confirmer l'ordre affiché\n"
                "/non — annuler l'ordre affiché\n"
                "/annuler_bd TICKER — annuler un ordre en cours sur BD\n\n"
                "MODE AUTONOME\n"
                "/auto on 500    — activer avec 500€ de budget\n"
                "/auto positions 3 — nb max de positions simultanees\n"
                "/auto on 20%    — activer avec 20% du cash\n"
                "/auto off       — désactiver\n"
                "/auto status    — état + positions autonomes\n\n"
                "PORTEFEUILLE\n"
                "/sync — synchroniser positions et ordres depuis BD\n\n"
                "SESSION\n"
                "/disconnect — fermer la session et revenir en mode Classic",
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

    # Messages d'étape ÉPHÉMÈRES : « Synchronisation en cours » et les traces
    # de lecture n'ont aucune valeur une fois le résultat affiché — ils
    # encombraient la conversation à chaque /sync. Ils sont supprimés dès que
    # le résultat part. En cas d'échec (aucun résultat envoyé), ils RESTENT :
    # c'est alors le seul message qui explique pourquoi.
    step_ids = []

    def send_step(m):
        mid = send_editable(m, cid)
        if mid:
            step_ids.append(mid)
        else:
            send(m, cid)

    def send_result(m):
        while step_ids:
            delete_message(step_ids.pop(), cid)
        send(m, cid)

    def _do_sync():
        try:
            playwright_session.run(
                lambda page: sync_engine.sync(page, send_result, progress_fn=send_step),
                timeout=90,
            )
        except Exception as e:
            send(f"Erreur sync : {e}", cid)

    _run_long(cid, _do_sync)


def cmd_trailing(args, cid):
    """/trailing — force une vérification du trailing stop (SL au PRU) maintenant.

    Le cycle automatique tourne chaque heure à :35 (jours de marché, 9h-22h) et
    dès qu'une position franchit son seuil lors des checks de 9h/12h/15h/17h.
    Cette commande fait la même chose à la demande, en rendant compte de CHAQUE
    position évaluée (le cycle auto, lui, reste silencieux s'il n'a rien à faire).
    """
    if not bot_mode.is_playwright():
        send("Le mode Playwright n'est pas actif. /connect pour l'activer.", cid)
        return
    if not playwright_session.is_connected():
        send("Session Playwright non connectee. /connect pour relancer.", cid)
        return
    import autonomous_engine

    def _do_trailing():
        try:
            # Réarme les notifications d'échec : l'utilisateur demande
            # explicitement un état, il doit le recevoir même si le même
            # échec a déjà été signalé lors d'un cycle automatique.
            autonomous_engine._trailing_cancel_failed.clear()
            autonomous_engine.trailing_stop_cycle(lambda m: send(m, cid), verbose=True)
        except Exception as e:
            send(f"Erreur trailing : {e}", cid)

    _run_long(cid, _do_trailing)


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


def send_photo(image_bytes: bytes, caption: str = "", chat_id: str = None) -> bool:
    """Envoie une image (PNG) sur Telegram."""
    if not TELEGRAM_TOKEN:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": chat_id or CHAT_ID, "caption": caption[:1024]},
            files={"photo": ("dashboard.png", image_bytes, "image/png")},
            timeout=30,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram send_photo error: {e}")
        return False


def cmd_lessons(args, cid):
    """/lessons — ce que le bot a appris de ses trades passés + garde-fous actifs."""
    try:
        import lessons
        block = lessons.build_lessons_block()
        streak = lessons.loss_streak()
        factor = lessons.size_factor()
        parts = [block] if block else [
            "Pas encore assez de trades tagués (min 3) pour dégager des leçons.\n"
            "La boucle d'apprentissage démarre : chaque nouveau trade enregistre "
            "sa thèse et ses indicateurs d'entrée."
        ]
        parts.append(
            f"\nGARDE-FOUS ACTIFS\n"
            f"- Série de pertes en cours : {streak}\n"
            f"- Taille des prochaines entrées : {int(factor*100)}% du budget\n"
            f"- Cooldown : pas de re-entrée sur un titre perdu depuis < 10 jours"
        )
        send("🧠 APPRENTISSAGE DU BOT\n\n" + "\n".join(parts), cid)
    except Exception as e:
        send(f"Erreur lessons : {e}", cid)


def cmd_dashboard(args, cid):
    """/dashboard — graphique P&L + résumé, et lien vers la version locale."""
    def _do():
        try:
            import dashboard
            png = dashboard.render_png()
            txt = dashboard.summary_text()
            if png:
                if not send_photo(png, caption=txt, chat_id=cid):
                    send(txt, cid)
            else:
                send("Aucun trade clôturé pour l'instant.\n\n" + txt, cid)
        except Exception as e:
            send(f"Erreur dashboard : {e}", cid)

    _run_long(cid, _do)


def cmd_capture(args, cid):
    """/capture — trace toutes les requêtes POST vers l'API trading BD dans le log.
    Utilisation : /capture, PUIS refaire À LA MAIN, dans la fenêtre Chromium du
    bot, l'action que le bot rate (passer un ordre, annuler, modifier un SL/TP…)
    — le payload exact du site apparaît dans tradingbot.log ([CAPTURE]).
    Deux pièges : l'action doit avoir lieu dans le Chromium DU BOT (une action
    faite sur téléphone ou dans un autre navigateur n'est pas vue), et /capture
    doit précéder l'action. Actif jusqu'au redémarrage."""
    if not _check_playwright_ready(cid):
        return

    def _arm(page):
        # Écoute au niveau du CONTEXTE (tous les onglets, y compris ceux que
        # BD ouvrira ensuite) — le module d'ordre BD s'ouvre souvent dans un
        # nouvel onglet que le listener de page unique ne voyait pas.
        ctx = page.context

        def on_request(req):
            try:
                if "/hub/" in req.url and req.method == "POST":
                    print(f"[CAPTURE] POST {req.url}")
                    print(f"[CAPTURE PAYLOAD] {req.post_data}")
            except Exception:
                pass

        def on_response(resp):
            try:
                if "/hub/" in resp.url:
                    print(f"[CAPTURE RESP] {resp.status} {resp.url}")
            except Exception:
                pass

        def on_page(p):
            try:
                print(f"[CAPTURE] nouvel onglet : {p.url}")
            except Exception:
                pass

        ctx.on("request", on_request)
        ctx.on("response", on_response)
        ctx.on("page", on_page)
        return True

    def _do():
        try:
            playwright_session.run(_arm, timeout=15)
            send(
                "🎥 CAPTURE RÉSEAU ACTIVE\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Toutes les requêtes que le site BD envoie sont maintenant "
                "tracées dans tradingbot.log ([CAPTURE PAYLOAD]).\n\n"
                "⚠️ 2 CONDITIONS, sinon rien n'est enregistré :\n"
                "• L'action doit se faire DANS LA FENÊTRE CHROMIUM DU BOT "
                "(sur le Mac) — pas sur ton téléphone, pas dans ton propre "
                "navigateur : le bot ne voit que sa propre fenêtre.\n"
                "• /capture AVANT l'action, jamais après.\n\n"
                "MODE D'EMPLOI (n'importe quelle action)\n"
                "Fais simplement à la main, dans le Chromium du bot, l'action "
                "que le bot n'arrive pas à faire. Va jusqu'au bout — y compris "
                "la confirmation finale si c'est nécessaire pour que le site "
                "envoie la requête.\n\n"
                "Exemples :\n"
                "• Ordre refusé → remplis le formulaire jusqu'à Vérifier/Valider\n"
                "• Annulation impossible → annule l'ordre à la main depuis "
                "« Mes ordres » (et repose la protection derrière si tu viens "
                "de supprimer un SL/TP !)\n"
                "• Modification de SL/TP → fais la modif à la main\n\n"
                "Reste actif jusqu'au prochain redémarrage du bot.\n"
                "Préviens-moi quand c'est fait : je lirai le payload exact.",
                cid,
            )
        except Exception as e:
            send(f"Erreur capture : {e}", cid)

    _run_long(cid, _do)


def cmd_testordre(args, cid):
    """/testordre TICKER — diagnostic payload BD : teste les variantes /order/create
    (validation seule, rien n'est envoyé au marché)."""
    if not _check_playwright_ready(cid):
        return
    tickers = [a for a in args if a.lower() not in ("acheter", "vendre", "buy", "sell")]
    if not tickers:
        send("Usage : /testordre TICKER (ex: /testordre RTX)", cid)
        return
    ticker = tickers[0].upper()
    import bourse_direct_orders as bd_orders

    def _do_test():
        try:
            playwright_session.run(
                lambda page: bd_orders.debug_order_variants(page, ticker, lambda m: send(m, cid)),
                timeout=120,
            )
        except Exception as e:
            send(f"Erreur testordre : {e}", cid)

    _run_long(cid, _do_test)


def _parse_validity_arg(args, start_idx: int) -> str:
    """Extrait le dernier argument optionnel de validité s'il est présent."""
    import re
    if len(args) > start_idx:
        v = args[start_idx].strip()
        if v.lower() in ("seance", "max", "revocation") or re.match(r"\d{2}/\d{2}/\d{4}$", v):
            return v
    return "max"


def cmd_ordre(args, cid):
    """
    Syntaxe :
    /ordre vendre TICKER QTE expert SL TP [validite]
    /ordre vendre TICKER QTE limite PRIX [validite]
    /ordre vendre TICKER QTE marche [validite]
    /ordre acheter TICKER QTE expert ENTREE SL TP [validite]
    /ordre acheter TICKER QTE limite PRIX [validite]
    /ordre acheter TICKER QTE marche [validite]

    Validite (optionnel, defaut=max) : seance | max | revocation | JJ/MM/AAAA
    """
    global _pending_order
    if not _check_playwright_ready(cid):
        return
    if len(args) < 4:
        send(
            "Usage :\n"
            "/ordre vendre TICKER QTE expert SL TP [validite]\n"
            "/ordre vendre TICKER QTE limite PRIX [validite]\n"
            "/ordre acheter TICKER QTE expert ENTREE SL TP [validite]\n"
            "/ordre acheter TICKER QTE limite PRIX [validite]\n"
            "/ordre acheter TICKER QTE marche [validite]\n\n"
            "Validite : seance | max (defaut) | revocation | JJ/MM/AAAA\n"
            "Ex: /ordre acheter TTE.PA 3 expert 54.2 49 61 max\n"
            "Ex: /ordre vendre AIR.PA 1 expert 170 235 seance",
            cid,
        )
        return

    sens     = args[0].lower()
    ticker   = args[1].upper()
    try:
        qty  = int(args[2])
    except ValueError:
        send("Quantite invalide.", cid)
        return
    type_arg = args[3].lower()

    if sens not in ("vendre", "acheter"):
        send("Sens invalide : vendre ou acheter.", cid)
        return

    side = "sell" if sens == "vendre" else "buy"

    import bourse_direct_orders as bd_orders

    info = bd_orders.get_ticker_info(ticker)
    if not info:
        send(f"Ticker {ticker} non reconnu.", cid)
        return

    send(f"Preparation de l'ordre {sens} {qty}x {ticker}...", cid)

    def _do_order():
        global _pending_order
        try:
            if type_arg == "expert" and side == "sell":
                # VENTE expert : SL + TP sur position existante
                if len(args) < 6:
                    send("Expert vente : /ordre vendre TICKER QTE expert SL TP [validite]", cid)
                    return
                sl       = float(args[4].replace(",", "."))
                tp       = float(args[5].replace(",", "."))
                validity = _parse_validity_arg(args, 6)
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_expert_order(page, ticker, qty, sl, tp, validity)
                )
                is_expert = True
                summary   = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "meta",
                    validity=validity, sl=sl, tp=tp,
                )
            elif type_arg == "expert" and side == "buy":
                # ACHAT expert : entrée à cours limité + SL/TP intégrés
                if len(args) < 7:
                    send("Expert achat : /ordre acheter TICKER QTE expert ENTREE SL TP [validite]", cid)
                    return
                entree   = float(args[4].replace(",", "."))
                sl       = float(args[5].replace(",", "."))
                tp       = float(args[6].replace(",", "."))
                validity = _parse_validity_arg(args, 7)
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_expert_buy_order(
                        page, ticker, qty, entree, sl, tp, validity)
                )
                is_expert = True
                summary   = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "meta",
                    limit_price=entree, validity=validity, sl=sl, tp=tp,
                )
                # Boucle d'apprentissage : mémorise le contexte d'entrée. Si le
                # titre vient d'un scan/briefing, un contexte riche existe déjà
                # (on ne l'écrase pas) ; sinon on capte au moins RSI/momentum.
                if order_data and not portfolio.get_entry_context(ticker):
                    try:
                        tech = prices.get_technicals(ticker) or {}
                        pctx = prices.get_price_context(ticker) or {}
                        portfolio.set_entry_context(ticker, {
                            "source": "manuel", "entry": entree,
                            "rsi": tech.get("rsi"), "momentum_1m": tech.get("momentum_1m"),
                            "vol_ratio": tech.get("vol_ratio"),
                            "perf_1y": pctx.get("perf_1y"),
                            "from_52w_low": pctx.get("from_52w_low"),
                            "tp_pct": round((tp - entree) / entree * 100, 1) if entree else None,
                            "thesis": "ordre manuel",
                        })
                    except Exception:
                        pass
            elif type_arg == "limite":
                if len(args) < 5:
                    send("Limite requiert un prix : /ordre acheter TICKER QTE limite PRIX [validite]", cid)
                    return
                prix     = float(args[4].replace(",", "."))
                validity = _parse_validity_arg(args, 5)
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_order(
                        page, ticker, side, qty, order_type="limit",
                        limit_price=prix, validity=validity)
                )
                is_expert = False
                summary   = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "limit",
                    limit_price=prix, validity=validity,
                )
            else:  # marche
                validity = _parse_validity_arg(args, 4)
                order_data = playwright_session.run(
                    lambda page: bd_orders.create_order(
                        page, ticker, side, qty, order_type="market", validity=validity)
                )
                is_expert = False
                summary   = bd_orders.format_order_summary(
                    order_data or {}, ticker, side, qty, "market", validity=validity,
                )

            if not order_data:
                # Montre la réponse brute de la dernière requête pour diagnostic
                last = bd_orders._last_raw
                raw_txt = (
                    f"\nHTTP {last.get('status', '?')} — "
                    f"{str(last.get('data') or last.get('error', ''))[:300]}"
                ) if last else ""
                send(
                    f"Echec creation ordre {ticker}.{raw_txt}\n\n"
                    f"Si session expirée : /connect pour reconnecter.",
                    cid,
                )
                return

            order_id = order_data.get("id") or order_data.get("order_id")
            with _pending_lock:
                _pending_order = {
                    "order_id":  order_id,
                    "is_buy_smart": is_expert and side == "buy",
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
                # Expert ACHAT (limit+smart) → /order/send ; Expert VENTE (meta)
                # → /order/execute/strategy. Bascule auto en cas d'échec.
                result = playwright_session.run(
                    lambda page: bd_orders.confirm_order_auto(
                        page, pending["order_id"], pending.get("is_buy_smart", False)))
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

            # Sync silencieux différé : si l'ordre a été exécuté immédiatement
            # (limite au cours), le portefeuille est à jour tout de suite —
            # message envoyé uniquement si une exécution est détectée.
            schedule_post_order_sync(cid)
        except Exception as e:
            send(f"Erreur envoi : {e}", cid)

    _run_long(cid, _do_send)


def schedule_post_order_sync(cid=None, delay: float = 8.0):
    """Planifie un sync BD silencieux `delay` secondes après un passage d'ordre.
    Détecte les exécutions immédiates (achat limite au cours) sans attendre
    le sync horaire. Silencieux : notifie uniquement si un événement est détecté."""
    def _run():
        try:
            import sync_engine
            playwright_session.run(
                lambda page: sync_engine.sync(page, lambda m: send(m, cid), silent=True),
                timeout=90,
            )
        except Exception as e:
            print(f"[post-order sync] {e}")
    threading.Timer(delay, _run).start()


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
                o_name = target.get("name") or ticker_base
                o_type = target.get("type")
                o_type_str = f" — {o_type}" if o_type else ""
                send(f"Ordre {o_name}{o_type_str} annule sur BD.", cid)
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


def cmd_auto(args, cid):
    """
    /auto on 500        → active avec 500€
    /auto on 20%        → active avec 20% du cash disponible
    /auto off           → désactive (positions existantes toujours surveillées)
    /auto status        → état + positions autonomes
    /auto pause         → suspend les nouvelles entrées sans changer le budget
    /auto positions 3   → nombre max de positions autonomes simultanées
    """
    import autonomous_engine

    sub = args[0].lower() if args else "status"

    if sub == "status":
        cfg  = portfolio.get_autonomous_config()
        info = autonomous_engine.get_budget_info()
        auto_pos = portfolio.get_autonomous_positions()

        state = "ACTIF ✅" if cfg.get("enabled") else "INACTIF ⛔"
        lines = [
            f"🤖 MODE AUTONOME — {state}",
            f"Budget : {info['total']:.0f}€ total | {info['engaged']:.0f}€ engagé | {info['available']:.0f}€ libre",
            f"Max positions : {cfg.get('max_positions', 2)} | Breakeven : +{cfg.get('breakeven_pct', 3.0):.0f}%",
        ]

        if auto_pos:
            lines.append("\nPOSITIONS AUTONOMES")
            for name, pos in auto_pos.items():
                q  = prices.get_quote(pos["ticker"])
                px = portfolio.best_price(pos, q)["price"]
                if px:
                    chg = (px - pos["entry_price"]) / pos["entry_price"] * 100
                    pnl = (px - pos["entry_price"]) * pos["qty"]
                    lines.append(
                        f"  {name} ({pos['ticker']}) : {px}€ ({chg:+.1f}%) | "
                        f"P&L {pnl:+.0f}€ | SL {pos['target_low']} | TP {pos['target_high']}"
                    )
                else:
                    lines.append(f"  {name} ({pos['ticker']}) : prix indispo")
        else:
            if cfg.get("enabled"):
                lines.append("Aucune position autonome active.")

        if cfg.get("enabled"):
            block = autonomous_engine.entry_capacity_block()
            if block:
                lines.append(f"\n⏸️ Aucune entrée possible : {block}")
                lines.append("Tant que c'est le cas, les analyses IA planifiées "
                             "(scan US 16h, candidats du briefing) sont sautées — "
                             "elles ne pourraient rien acheter. /scan force une "
                             "analyse complète quand même.")

        if not cfg.get("enabled"):
            lines.append("\nUsage :\n/auto on 500      (budget fixe)\n/auto on 20%      (% du cash)\n/auto positions 3 (places simultanees)")

        send("\n".join(lines), cid)
        return

    if sub == "off":
        cfg = portfolio.get_autonomous_config()
        cfg["enabled"] = False
        portfolio.set_autonomous_config(cfg)
        auto_pos = portfolio.get_autonomous_positions()
        nb = len(auto_pos)
        send(
            f"🤖 Mode autonome désactivé.\n"
            f"{'Aucune' if nb == 0 else str(nb)} position{'s' if nb > 1 else ''} autonome{'s' if nb > 1 else ''} "
            f"{'active — toujours surveillée.' if nb == 1 else ('actives — toujours surveillées.' if nb > 1 else '.')}",
            cid,
        )
        return

    if sub in ("positions", "position", "places"):
        cfg = portfolio.get_autonomous_config()
        cur = cfg.get("max_positions", 2)
        if len(args) < 2:
            send(f"Nombre max de positions autonomes : {cur}\n"
                 f"Usage : /auto positions 3", cid)
            return
        try:
            n = int(args[1])
        except ValueError:
            send("Nombre invalide. Ex : /auto positions 3", cid)
            return
        if n < 1 or n > 10:
            send("Hors limites (1-10).", cid)
            return
        used = len(portfolio.get_autonomous_positions()) + len(portfolio.get_auto_pending_orders())
        cfg["max_positions"] = n
        portfolio.set_autonomous_config(cfg)
        from config import RISK_PER_TRADE_PCT
        send(
            f"🤖 Max positions autonomes : {cur} → {n}\n"
            f"Occupées actuellement : {used}/{n}\n\n"
            f"⚠️ Plus de positions = plus de risque cumulé : chaque ligne peut "
            f"perdre {RISK_PER_TRADE_PCT:.1f}% du budget au SL, donc {n} lignes "
            f"exposent jusqu'à {n * RISK_PER_TRADE_PCT:.1f}% simultanément.\n"
            f"Le budget total ne change pas — les positions seront plus petites.",
            cid,
        )
        return

    if sub == "pause":
        cfg = portfolio.get_autonomous_config()
        cfg["enabled"] = False
        portfolio.set_autonomous_config(cfg)
        send("🤖 Mode autonome mis en pause — nouvelles entrées suspendues. /auto on pour reprendre.", cid)
        return

    if sub == "on":
        if len(args) < 2:
            send("Usage : /auto on 500  ou  /auto on 20%", cid)
            return

        raw = args[1].strip()
        if raw.endswith("%"):
            try:
                pct = float(raw[:-1])
            except ValueError:
                send("Pourcentage invalide. Ex : /auto on 20%", cid)
                return
            if pct <= 0 or pct > 100:
                send("Pourcentage hors limites (1-100).", cid)
                return
            cash = portfolio.get_cash()
            budget = round(cash * pct / 100, 2)
            cfg = autonomous_engine.set_config(True, budget_pct=pct)
            send(
                f"🤖 Mode autonome ACTIVÉ\n"
                f"Budget : {pct:.0f}% du cash = {budget:.0f}€\n"
                f"Max {cfg.get('max_positions', 2)} positions | Breakeven +{cfg.get('breakeven_pct', 3.0):.0f}%\n\n"
                f"Le bot entrera en position au prochain check planifié\n"
                f"(Playwright doit être connecté via /connect)\n\n"
                f"/auto status — voir l'état\n"
                f"/auto off — désactiver",
                cid,
            )
        else:
            try:
                budget = float(raw.replace(",", ".").replace("€", ""))
            except ValueError:
                send("Montant invalide. Ex : /auto on 500", cid)
                return
            if budget < 50:
                send("Budget minimum : 50€", cid)
                return
            cfg = autonomous_engine.set_config(True, budget_total=budget)
            send(
                f"🤖 Mode autonome ACTIVÉ\n"
                f"Budget : {budget:.0f}€\n"
                f"Max {cfg.get('max_positions', 2)} positions | Breakeven +{cfg.get('breakeven_pct', 3.0):.0f}%\n\n"
                f"Le bot entrera en position au prochain check planifié\n"
                f"(Playwright doit être connecté via /connect)\n\n"
                f"/auto status — voir l'état\n"
                f"/auto off — désactiver",
                cid,
            )
        return

    send("Commande non reconnue. Usage : /auto on 500 | /auto off | /auto status | "
         "/auto positions 3 | /auto pause", cid)


# ─── Routeur ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/help": cmd_help,
    "/start": cmd_start,
    "/status": cmd_status,
    "/mode": cmd_mode,
    "/connect": cmd_connect,
    "/disconnect": cmd_disconnect,
    "/sync": cmd_sync,
    "/trailing": cmd_trailing,
    "/testordre": cmd_testordre,
    "/capture": cmd_capture,
    "/dashboard": cmd_dashboard,
    "/lessons": cmd_lessons,
    "/ordre": cmd_ordre,
    "/oui": cmd_oui,
    "/non": cmd_non,
    "/annuler_bd": cmd_annuler_bd,
    "/cash": cmd_cash,
    "/add": cmd_add,
    "/remove": cmd_remove,
    "/reticker": cmd_reticker,
    "/hold": cmd_hold,
    "/sl": cmd_sl,
    "/tp": cmd_tp,
    "/buy": cmd_buy,
    "/order": cmd_order,
    "/setup": cmd_setup,
    "/stats": cmd_stats,
    "/fallback": cmd_fallback,
    "/close": cmd_close,
    "/attente": cmd_attente,
    "/annuler": cmd_annuler,
    "/vendu": cmd_vendu,
    "/syncmail": cmd_syncmail,
    "/update": cmd_update,
    "/morning": cmd_morning,
    "/scan": cmd_scan,
    # Alias : le menu Telegram n'accepte pas d'argument, /scan_us y donne
    # accès au scan US en un tap (équivaut à « /scan us »).
    "/scan_us": lambda args, cid: cmd_scan(["us"], cid),
    "/research": cmd_research,
    "/import": cmd_import,
    "/tuto": cmd_tuto,
    "/auto": cmd_auto,
}


def _handle_message(message: dict):
    cid = str(message.get("chat", {}).get("id", ""))

    # ── AUTORISATION (sécurité critique) ─────────────────────────────────────
    # N'exécuter les commandes QUE pour les chats autorisés. Sans ce filtre,
    # tout inconnu ayant trouvé le bot pourrait passer des ordres réels, lire
    # le portefeuille, ou relayer le code 2FA de connexion Bourse Direct.
    if AUTHORIZED_CHAT_IDS and cid not in AUTHORIZED_CHAT_IDS:
        print(f"[SECURITY] message ignoré d'un chat non autorisé : {cid}")
        return

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

    # message_id du message entrant — permet aux commandes sensibles (/fallback
    # avec une clé API) de SUPPRIMER le message du chat après traitement.
    global _incoming_msg_id
    _incoming_msg_id = message.get("message_id")

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
                # Avance l'offset AVANT de traiter pour éviter le double-envoi
                # si le handler crash ou si Telegram redelivre après un timeout.
                offset = upd["update_id"] + 1
                if "message" in upd:
                    _handle_message(upd["message"])
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
            # offset conservé intentionnellement — on reprend là où on s'était arrêté


def start_polling():
    set_bot_commands()
    t = threading.Thread(target=_poll, daemon=True, name="telegram-poll")
    t.start()
    return t
