"""
Interface Telegram : polling + commandes interactives.
Toutes les commandes sont disponibles depuis l'app iPhone/web.
"""
import requests
import time
import threading
from config import TELEGRAM_TOKEN, CHAT_ID
import portfolio
import prices
import analysis
import orders
import stats

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
        "/stats — Win Rate, P&L realise/latent, Profit Factor\n"
        "/close TICKER QTY PRIX [FRAIS] — Cloturer un trade\n"
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
        qty = int(args[1])
        pru = float(args[2].replace(",", "."))
        sl = float(args[3].replace(",", "."))
        tp = float(args[4].replace(",", "."))
        name = ticker.split(".")[0]
        portfolio.add_position(name, ticker, qty, pru, sl, tp)
        send(f"Position ajoutee: {name}\n{qty}t @ PRU {pru}€ | SL {sl}€ | TP {tp}€", cid)
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


def cmd_morning(args, cid):
    send("Briefing en cours de generation...", cid)
    threading.Thread(target=analysis.morning_briefing, args=(lambda m: send(m, cid),), daemon=True).start()


def cmd_scan(args, cid):
    send("Scan en cours...", cid)
    threading.Thread(target=analysis.scan_opportunities, args=(lambda m: send(m, cid),), daemon=True).start()


def cmd_research(args, cid):
    if not args:
        send("Usage: /research TICKER\nEx: /research LBIRD", cid)
        return
    ticker = args[0].upper()
    send(f"Analyse de {ticker} en cours...", cid)
    threading.Thread(
        target=analysis.scan_opportunities,
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
    send(
        "TradingBot — Guide d'installation (1/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 1 — Creer ton bot Telegram\n"
        "\n"
        "1. Ouvre Telegram, cherche @BotFather\n"
        "2. Envoie : /newbot\n"
        "3. Choisis un nom (ex: MonTraderBot)\n"
        "4. Choisis un username (ex: montrader_bot)\n"
        "5. BotFather te donne un TOKEN\n"
        "   Ex: ***REMOVED***:ABCDefgh...\n"
        "   → Copie-le, c'est ton TELEGRAM_TOKEN",
        cid,
    )
    time.sleep(0.5)
    send(
        "TradingBot — Guide d'installation (2/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 2 — Obtenir ton Chat ID\n"
        "\n"
        "1. Cherche @userinfobot sur Telegram\n"
        "2. Envoie /start\n"
        "3. Il te repond avec ton ID numerique\n"
        "   Ex: ***REMOVED***\n"
        "   → C'est ton CHAT_ID\n"
        "\n"
        "Alternative : demarre ton bot puis envoie /start\n"
        "Ouvre dans un navigateur :\n"
        "api.telegram.org/bot<TOKEN>/getUpdates\n"
        "Cherche \"id\" dans le JSON",
        cid,
    )
    time.sleep(0.5)
    send(
        "TradingBot — Guide d'installation (3/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 3 — Choisir et configurer ton IA\n"
        "\n"
        "GRATUIT — Groq (recommande pour commencer)\n"
        "  Inscription : console.groq.com\n"
        "  Cle : API Keys → Create\n"
        "  .env : AI_PROVIDER=groq\n"
        "         GROQ_API_KEY=gsk_...\n"
        "\n"
        "GRATUIT — Google Gemini\n"
        "  Inscription : aistudio.google.com\n"
        "  Cle : Get API Key\n"
        "  .env : AI_PROVIDER=gemini\n"
        "         GEMINI_API_KEY=AIza...\n"
        "\n"
        "PAYANT — Anthropic Claude\n"
        "  Inscription : console.anthropic.com\n"
        "  .env : AI_PROVIDER=anthropic\n"
        "         ANTHROPIC_API_KEY=***REMOVED***\n"
        "\n"
        "PAYANT — OpenAI GPT\n"
        "  Inscription : platform.openai.com\n"
        "  .env : AI_PROVIDER=openai\n"
        "         OPENAI_API_KEY=sk-proj-...\n"
        "\n"
        "PAYANT — Mistral\n"
        "  Inscription : console.mistral.ai\n"
        "  .env : AI_PROVIDER=mistral\n"
        "         MISTRAL_API_KEY=...",
        cid,
    )
    time.sleep(0.5)
    send(
        "TradingBot — Guide d'installation (4/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 4 — Telecharger et ouvrir le projet\n"
        "\n"
        "Dans ton terminal (Terminal sur Mac,\n"
        "Invite de commandes sur Windows) :\n"
        "\n"
        "  git clone https://github.com/myopencomm/Tradingbot.git\n"
        "  cd Tradingbot\n"
        "\n"
        "cd signifie 'change directory' : ca place\n"
        "le terminal dans le dossier du bot.\n"
        "Toutes les commandes qui suivent doivent\n"
        "etre tapees depuis ce dossier.\n"
        "\n"
        "Si tu fermes et rouvres ton terminal,\n"
        "refais cd Tradingbot avant tout.\n"
        "\n"
        "IMPORTANT — Verifie ta version Python AVANT\n"
        "de continuer :\n"
        "  python3 --version\n"
        "→ Doit afficher 3.10 ou +\n"
        "→ Sur Mac : si c'est 3.9, installe d'abord\n"
        "  Homebrew + brew install python\n"
        "  (voir README sur GitHub pour le detail)\n"
        "\n"
        "Ensuite installe les dependances :\n"
        "  python3 -m venv venv\n"
        "  venv/bin/pip install -r requirements.txt\n"
        "\n"
        "Et copie les fichiers de config :\n"
        "  cp .env.example .env\n"
        "  cp positions.example.json positions.json",
        cid,
    )
    time.sleep(0.5)
    send(
        "TradingBot — Guide d'installation (5/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 5 — Configurer le fichier .env\n"
        "\n"
        "Ouvre le fichier .env avec un editeur :\n"
        "  nano .env   (Mac/Linux)\n"
        "  notepad .env  (Windows)\n"
        "\n"
        "Remplis UNIQUEMENT les lignes de ton choix :\n"
        "\n"
        "  AI_PROVIDER=groq\n"
        "  GROQ_API_KEY=<colle_ta_cle_ici>\n"
        "  TELEGRAM_TOKEN=***REMOVED***\n"
        "  CHAT_ID=***REMOVED***\n"
        "\n"
        "Laisse les autres lignes vides.\n"
        "Ne partage JAMAIS ce fichier .env.\n"
        "Il est dans .gitignore, il ne sera jamais\n"
        "envoye sur GitHub.",
        cid,
    )
    time.sleep(0.5)
    send(
        "TradingBot — Guide d'installation (6/6)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "ETAPE 6 — Lancer le bot\n"
        "\n"
        "Depuis le dossier Tradingbot (cd Tradingbot) :\n"
        "  venv/bin/python3 main.py\n"
        "\n"
        "PREMIERES COMMANDES\n"
        "  /help — voir toutes les commandes\n"
        "  /cash 1000 — definir ton cash disponible\n"
        "  Envoie une photo de ton portefeuille BD\n"
        "    → le bot lit tes positions\n"
        "  /add TICKER QTE PRU SL TP — ajouter\n"
        "  /morning — premier briefing IA\n"
        "\n"
        "MAINTENIR LE BOT ACTIF\n"
        "Sur Mac, utilise un service LaunchAgent.\n"
        "Sur serveur Linux, utilise systemd ou screen.\n"
        "\n"
        "Code source : github.com/votre-user/TradingBot\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Installation terminee ! Tape /help",
        cid,
    )


# ─── Routeur ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/help": cmd_help,
    "/start": cmd_start,
    "/status": cmd_status,
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
