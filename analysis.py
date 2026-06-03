from datetime import datetime
import pytz
import portfolio
import prices
import research
from ai_provider import get_provider, VISION_PROMPT
from config import TRADING_CONTEXT_PATH

PARIS = pytz.timezone("Europe/Paris")

TRADER_SYSTEM = """Tu es un expert trader actif sur tous les marchés accessibles via Bourse Direct (France).
Compte-titres ordinaire (CTO), horizon court à moyen terme (jours à quelques semaines).
Règles strictes: stop-loss -10% sur PRU, objectif minimum +15%, pas de levier.
Univers : Euronext Paris/Growth, Euronext Amsterdam/Bruxelles, NYSE, NASDAQ, LSE, Xetra — tout ce qu'on peut acheter sur Bourse Direct.
Priorité au meilleur rapport risque/rendement, peu importe le marché."""

TICKER_RULES = """
RÈGLES ABSOLUES — TICKERS (format Yahoo Finance) :
- Euronext Paris / Growth : suffixe .PA (ex: AIR.PA, GNFT.PA)
- Euronext Amsterdam     : suffixe .AS (ex: ASML.AS, INPST.AS)
- Euronext Bruxelles     : suffixe .BR (ex: UCB.BR)
- NYSE / NASDAQ (US)     : pas de suffixe (ex: NVDA, AAPL, TSLA)
- London Stock Exchange  : suffixe .L (ex: GSK.L, BP.L)
- Xetra / Frankfurt      : suffixe .DE (ex: SAP.DE, SIE.DE)
- Si incertain du ticker exact : écris NOM_SOCIÉTÉ (TICKER?) et signale l'incertitude
- Ne JAMAIS inventer ou approximer un ticker

CRITÈRES DE RISQUE — définitions strictes :
- LOW : valeur liquide, tendance haussière confirmée, pas d'événement binaire
- MEDIUM : catalyseur identifié mais résultat incertain, volatilité normale
- HIGH : événement binaire (résultats pivots, OPA seuil non atteint), small cap illiquide
- OPA en cours : TOUJOURS MEDIUM minimum, HIGH si seuil non atteint
- Arbitrage OPA : préciser le seuil requis, le % atteint, et le risque de chute si échec
"""

FORMAT_TELEGRAM = """
RÈGLES DE FORMAT STRICTES — message Telegram mobile :
- Texte brut uniquement. Zéro Markdown : pas de #, ##, **, *, `, ```, pas de tableaux avec |
- Séparateurs : une ligne vide entre les sections
- Titres de section : en MAJUSCULES, pas d'emojis sauf 1 max par section
- Listes : tirets simples (- item)
- Chiffres : toujours avec unité (€, %, t)
- Maximum 25 lignes au total — va à l'essentiel
"""

import re

def _strip_markdown(text: str) -> str:
    """Supprime les symboles Markdown résiduels pour un affichage propre sur Telegram."""
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)   # titres #
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)         # gras/italique
    text = re.sub(r'`{1,3}([^`]*)`{1,3}', r'\1', text)           # code inline/block
    text = re.sub(r'^-{3,}\s*$', '---', text, flags=re.MULTILINE) # hr
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)       # blockquotes
    text = re.sub(r'\n{3,}', '\n\n', text)                         # espaces excessifs
    return text.strip()


def _trading_context() -> str:
    """Charge le contexte personnel de trading si le fichier existe."""
    try:
        if TRADING_CONTEXT_PATH.exists():
            return TRADING_CONTEXT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _portfolio_snapshot() -> str:
    data = portfolio.load()
    cash = data.get("cash_available", 0)
    positions = data.get("positions", {})
    lines = [f"💰 Cash: {cash}€", "📁 Positions:"]
    for name, cfg in positions.items():
        quote = prices.get_quote(cfg["ticker"])
        price = quote.get("price")
        if price:
            chg  = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
            pnl  = (price - cfg["entry_price"]) * cfg["qty"]
            sym  = prices.currency_symbol(quote.get("currency", "EUR"))
            lines.append(
                f"  {name} ({cfg['ticker']}): {sym}{price} ({chg:+.2f}%) | "
                f"PRU {sym}{cfg['entry_price']} | {cfg['qty']}t | P&L {sym}{pnl:+.0f} | "
                f"SL {sym}{cfg['target_low']} | TP {sym}{cfg['target_high']}"
            )
        elif quote.get("status") in ("suspended", "error"):
            lines.append(
                f"  {name} ({cfg['ticker']}): ⛔ COURS SUSPENDU — non vendable (liquidation judiciaire ?) | "
                f"PRU {cfg['entry_price']}€ | {cfg['qty']}t"
            )
        else:
            lines.append(f"  {name}: prix indisponible | PRU {cfg['entry_price']}€ | {cfg['qty']}t")

    pending = data.get("pending_orders", {})
    if pending:
        lines.append("⏳ Ordres en attente (cash réservé) :")
        for name, cfg in pending.items():
            quote = prices.get_quote(cfg["ticker"])
            price = quote.get("price") or "?"
            drift = ""
            if isinstance(price, float):
                d = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
                drift = f" | cours actuel {price}€ ({d:+.1f}% vs entrée)"
            lines.append(
                f"  {name} ({cfg['ticker']}): achat limite {cfg['entry_price']}€ "
                f"x {cfg['qty']}t — {cfg['reserved_cash']:.0f}€ réservés{drift}"
            )

    return "\n".join(lines)


def _breach_warning(ticker: str, pru: float, sl: float) -> str | None:
    """Retourne un message d'alerte si le cours actuel a déjà franchi le SL ou dépasse +25%."""
    quote = prices.get_quote(ticker)
    price = quote.get("price")
    if not price:
        return None
    if price < sl:
        return f"⚠️ SL déjà dépassé : cours {price}€ < SL {sl}€ → /research {ticker}"
    if price > pru * 1.25:
        gain = ((price / pru) - 1) * 100
        return f"⚠️ TP dépassé (+{gain:.0f}%) : cours {price}€ → vendre ou /research {ticker}"
    return None


def morning_briefing(send_fn) -> None:
    """Briefing quotidien 9h05 : analyse portefeuille + macro + opportunités."""
    print(f"[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Analyse matinale...")
    try:
        ai = get_provider()
        snapshot = _portfolio_snapshot()
        macro = research.market_context()
        cash = portfolio.get_cash()

        ctx = _trading_context()
        ctx_block = f"\n--- CONTEXTE PERSONNEL ---\n{ctx}\n" if ctx else ""

        if cash >= 1000:
            mission = f"""MISSION
1. Pour chaque position : signal (conserver/alléger/vendre), tendance courte, commentaire bref.
2. Top 3 opportunités pour le cash disponible ({cash}€) :
   TICKER — prix entrée — SL — TP — raison — risque
3. Risque global : LOW / MEDIUM / HIGH"""
        else:
            mission = f"""MISSION
1. Pour chaque position : signal (conserver/alléger/vendre), tendance courte, commentaire bref.
2. Risque global : LOW / MEDIUM / HIGH
(Cash insuffisant pour nouvelles positions : {cash}€ < 1000€)"""

        prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
PORTEFEUILLE
{snapshot}

CONTEXTE MARCHÉ
{macro}

{mission}"""

        result = _strip_markdown(ai.complete(prompt, max_tokens=900))
        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        send_fn(f"🌅 BRIEFING — {date}\n\n{snapshot}\n\n{result}")

    except Exception as e:
        print(f"Erreur briefing: {e}")
        send_fn(f"⚠️ Erreur briefing matinal: {e}")


def monthly_breach_review(send_fn) -> None:
    """Revue mensuelle (1er du mois) des positions dont le SL est dépassé."""
    data = portfolio.load()
    positions = data.get("positions", {})
    breach = {k: v for k, v in positions.items() if v.get("sl_breach_notified")}

    if not breach:
        return

    print(f"[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Revue mensuelle SL dépassés...")
    try:
        ai = get_provider()
        ctx = _trading_context()
        ctx_block = f"\n{ctx}\n" if ctx else ""

        lines = []
        for name, cfg in breach.items():
            quote = prices.get_quote(cfg["ticker"])
            price = quote.get("price")
            sym = prices.currency_symbol(quote.get("currency", "EUR"))
            if price:
                chg = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
                pnl = (price - cfg["entry_price"]) * cfg["qty"]
                lines.append(
                    f"  {name} ({cfg['ticker']}): {sym}{price} ({chg:+.2f}%) | "
                    f"PRU {sym}{cfg['entry_price']} | {cfg['qty']}t | P&L {sym}{pnl:+.0f} | "
                    f"SL initial {sym}{cfg['target_low']}"
                )
            else:
                lines.append(f"  {name} ({cfg['ticker']}): cours indisponible | PRU {sym}{cfg['entry_price']}")

        snapshot = "\n".join(lines)
        prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
POSITIONS EN SL DÉPASSÉ — REVUE MENSUELLE
{snapshot}

MISSION
Pour chaque position :
1. La thesis de départ est-elle encore valide ?
2. Signal : CONSERVER / COUPER / ATTENDRE CATALYSEUR
3. Raison courte (1-2 lignes)
4. Horizon de rétablissement estimé si on conserve"""

        result = _strip_markdown(ai.complete(prompt, max_tokens=600))
        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        send_fn(f"📋 REVUE MENSUELLE — SL DÉPASSÉS\n{date}\n\n{snapshot}\n\n{result}")

    except Exception as e:
        print(f"Erreur revue mensuelle: {e}")
        send_fn(f"⚠️ Erreur revue mensuelle: {e}")


def weekly_swap_analysis(send_fn) -> None:
    """Analyse hebdomadaire : vaut-il mieux vendre une position pour en acheter une autre ?"""
    print(f"[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Analyse swap hebdo...")
    try:
        ai = get_provider()
        snapshot = _portfolio_snapshot()
        macro = research.market_context()
        cash = portfolio.get_cash()

        ctx = _trading_context()
        ctx_block = f"\n--- CONTEXTE PERSONNEL ---\n{ctx}\n" if ctx else ""

        prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
PORTEFEUILLE
{snapshot}

CONTEXTE MARCHÉ
{macro}

MISSION — ANALYSE DE ROTATION HEBDOMADAIRE
Cash disponible : {cash}€ (insuffisant pour nouvelle position directe)

1. Identifie la ou les positions les moins prometteuses à court terme (momentum faible, proche SL, thesis invalidée).
2. Propose 1-2 alternatives Euronext avec meilleur potentiel court terme.
3. Pour chaque swap envisagé :
   - Vendre : TICKER_A — raison en 1 ligne
   - Acheter : TICKER_B — entrée / SL / TP — raison
4. Conclusion : vaut-il mieux attendre ou swapper ?"""

        result = _strip_markdown(ai.complete(prompt, max_tokens=800))
        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        send_fn(f"🔄 ANALYSE SWAP — {date}\n\n{result}")

    except Exception as e:
        print(f"Erreur weekly swap: {e}")
        send_fn(f"⚠️ Erreur analyse swap: {e}")


def _validate_tickers(text: str) -> str:
    """Extrait les tickers du texte IA et avertit pour ceux non reconnus par yfinance."""
    import yfinance as yf
    found = re.findall(r'\(([A-Z0-9]{2,8}(?:\.[A-Z]{1,3})?)\)', text)
    bad = []
    for t in set(found):
        try:
            hist = yf.Ticker(t).history(period="5d")
            if hist.empty:
                bad.append(t)
        except Exception:
            bad.append(t)
    if bad:
        text += (
            f"\n\n⚠️ TICKER(S) NON VERIFIE(S) : {', '.join(bad)}\n"
            "Ces tickers n'ont pas de cours sur Yahoo Finance — verifie le symbole exact avant de passer un ordre."
        )
    return text


def research_ticker(send_fn, ticker: str) -> None:
    """Analyse approfondie d'un ticker spécifique — focalisée sur cette seule action."""
    try:
        ai   = get_provider()
        ctx  = _trading_context()
        ctx_block = f"\nCONTEXTE PERSONNEL\n{ctx}\n" if ctx else ""

        # Cherche si le ticker est en portefeuille (par ticker ou par nom)
        data      = portfolio.load()
        positions = data.get("positions", {})
        held      = next(
            (cfg for cfg in positions.values()
             if cfg["ticker"].upper() == ticker.upper()),
            None,
        )
        if held:
            quote   = prices.get_quote(held["ticker"])
            price   = quote.get("price", "?")
            sym     = prices.currency_symbol(quote.get("currency", "EUR"))
            chg     = ((price - held["entry_price"]) / held["entry_price"]) * 100 if isinstance(price, float) else 0
            pnl     = (price - held["entry_price"]) * held["qty"] if isinstance(price, float) else 0
            pos_block = (
                f"\nPOSITION EN PORTEFEUILLE\n"
                f"- Cours actuel : {sym}{price} ({chg:+.2f}%)\n"
                f"- PRU : {sym}{held['entry_price']} | Quantité : {held['qty']}t\n"
                f"- P&L latent : {sym}{pnl:+.0f}\n"
                f"- SL : {sym}{held['target_low']} | TP : {sym}{held['target_high']}\n"
            )
            position_question = (
                "Cette action est dans mon portefeuille. Dois-je conserver, renforcer, alléger ou vendre ?\n"
                "Évalue aussi si le SL/TP actuel reste pertinent au vu des catalyseurs récents."
            )
        else:
            pos_block = "\nACTION NON DÉTENUE — analyse pour une éventuelle entrée.\n"
            position_question = (
                "Je ne détiens pas cette action. Y a-t-il une opportunité d'entrée ?\n"
                "Si oui : signal (ACHAT/NEUTRE), prix d'entrée, SL (-10%), TP (+15%), catalyseur principal."
            )

        web       = research.research_stock(ticker)
        catalysts = research.search_catalysts(ticker)
        tech      = prices.get_technicals(ticker)
        if tech:
            rsi = tech.get("rsi", "N/A")
            mom = tech.get("momentum_1m", "N/A")
            vol = tech.get("vol_ratio", "N/A")
            tech_block = (
                f"\nINDICATEURS TECHNIQUES\n"
                f"- RSI 14j : {rsi}\n"
                f"- Momentum 1 mois : {mom:+}%\n"
                f"- Volume ratio : {vol}x moyenne 20j\n"
            )
        else:
            tech_block = ""

        prompt = f"""{TRADER_SYSTEM}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_block}
TICKER ANALYSÉ : {ticker}
{pos_block}
{tech_block}
RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{catalysts}

QUESTION : {position_question}
Niveau de risque selon les critères stricts (LOW/MEDIUM/HIGH). Maximum 25 lignes."""

        result = _strip_markdown(ai.complete(prompt, max_tokens=600))
        result = _validate_tickers(result)
        send_fn(f"🔍 ANALYSE {ticker}\n\n{result}")

    except Exception as e:
        send_fn(f"Erreur analyse {ticker}: {e}")


def scan_opportunities(send_fn, ticker: str = None) -> None:
    """Scan général du marché — top 3 opportunités avec le cash disponible."""
    try:
        ai = get_provider()
        cash = portfolio.get_cash()
        snapshot = _portfolio_snapshot()

        ctx = _trading_context()
        ctx_block = f"\n{ctx}\n" if ctx else ""

        if ticker:  # kept for backward compat — prefer research_ticker directly
            return research_ticker(send_fn, ticker)
        else:
            macro      = research.market_context()
            catalysts  = research.market_catalysts()
            prompt = f"""{TRADER_SYSTEM}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_block}
{snapshot}

CONTEXTE MARCHÉ
{macro}

CATALYSEURS IMMINENTS EURONEXT
{catalysts}

Cash disponible : {cash}€

Si des ordres en attente sont listés ci-dessus, commence par les évaluer :
ORDRE EN ATTENTE NOM — MAINTENIR ou ANNULER ?
- Justification courte (conditions changées ? thèse toujours valide ?)

Ensuite propose jusqu'à 3 opportunités avec catalyseur imminent identifié.
Tous les marchés accessibles via Bourse Direct sont valides (Euronext, NYSE, NASDAQ, LSE, Xetra).
Pour chaque opportunité, format exact :
NOM SOCIETE (TICKER)
- Marché : ex Euronext Paris / NASDAQ / LSE
- Entrée : X€  SL : X€  TP : X€
- Catalyseur : ...
- Raison : ...
- Risque : LOW / MEDIUM / HIGH  (applique les critères stricts ci-dessus)"""
            header = "🔍 SCAN OPPORTUNITÉS"

        result = _strip_markdown(ai.complete(prompt, max_tokens=700))
        result = _validate_tickers(result)
        send_fn(f"{header}\n\n{result}\n\n💰 Cash: {cash}€")

    except Exception as e:
        send_fn(f"⚠️ Erreur scan: {e}")


def _parse_vision_output(raw: str) -> list[dict]:
    """Parse la sortie pipe-delimitée de l'IA vision en liste de dicts."""
    positions = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        name_raw = parts[0]
        ticker_raw = parts[1]
        qty_raw = parts[2]
        pru_raw = parts[3]

        if name_raw in ("N/A", "") and ticker_raw in ("N/A", ""):
            continue

        try:
            qty = int(qty_raw.replace(" ", "").replace("\xa0", "").replace(",", ""))
            pru = float(pru_raw.replace(",", ".").replace(" ", "").replace("\xa0", ""))
        except ValueError:
            continue

        ticker = ticker_raw if ticker_raw not in ("N/A", "") else ""
        # Clé propre : base du ticker ou nom nettoyé
        if ticker:
            key = ticker.split(".")[0].upper()
        else:
            key = name_raw.upper().replace(" ", "_")[:20]

        positions.append({
            "key":    key,
            "name":   key,
            "ticker": ticker or key + ".PA",
            "qty":    qty,
            "pru":    pru,
        })
    return positions


def _deduplicate(all_positions: list[dict]) -> dict[str, dict]:
    """
    Fusionne les positions identiques vues sur plusieurs screenshots.
    Garde la version la plus complète (PRU non nul, ticker précis).
    """
    merged: dict[str, dict] = {}
    for pos in all_positions:
        key = pos["key"]
        if key not in merged:
            merged[key] = pos
        else:
            # Préfère la version avec ticker précis (.PA) ou PRU plus élevé (plus frais inclus)
            existing = merged[key]
            if pos["pru"] > 0 and (existing["pru"] == 0 or "." in pos["ticker"]):
                merged[key] = pos
    return merged


def import_screenshots(images: list) -> str:
    """
    Analyse un batch de screenshots, fusionne, déduplique,
    et importe automatiquement les nouvelles positions.
    """
    ai = get_provider()

    # 1 — Analyser chaque image
    all_raw: list[dict] = []
    for i, img_bytes in enumerate(images):
        try:
            raw = ai.complete_with_image(VISION_PROMPT, img_bytes)
            parsed = _parse_vision_output(raw)
            all_raw.extend(parsed)
            print(f"  Screenshot {i+1}: {len(parsed)} positions détectées")
        except NotImplementedError as e:
            return f"Vision non disponible avec ce provider : {e}"
        except Exception as e:
            print(f"  Screenshot {i+1} error: {e}")

    if not all_raw:
        return "Aucune position détectée dans les captures. Essaie avec des images plus nettes."

    # 2 — Fusionner les doublons inter-screenshots
    merged = _deduplicate(all_raw)
    print(f"  Après déduplication : {len(merged)} positions uniques")

    # 3 — Comparer avec le portfolio existant
    existing = portfolio.get_positions()
    existing_tickers = {cfg["ticker"].upper() for cfg in existing.values()}
    existing_keys = set(existing.keys())

    added, skipped, errors, breach_alerts = [], [], [], []

    for key, pos in merged.items():
        # Déjà présent ?
        if key in existing_keys or pos["ticker"].upper() in existing_tickers:
            skipped.append(pos)
            continue

        try:
            pru = float(pos["pru"])
            qty = int(pos["qty"])
            sl  = round(pru * 0.90, 2)
            tp  = round(pru * 1.15, 2)
            portfolio.add_position(pos["name"], pos["ticker"], qty, pru, sl, tp)
            added.append(pos)
            warning = _breach_warning(pos["ticker"], pru, sl)
            if warning:
                breach_alerts.append(f"  {pos['name']} — {warning}")
        except Exception as e:
            errors.append(f"{pos.get('name', '?')} ({e})")

    # 4 — Résumé
    lines = []

    if added:
        lines.append(f"Importé — {len(added)} nouvelle(s) position(s) :")
        for p in added:
            pru = p["pru"]
            sl  = round(pru * 0.90, 2)
            tp  = round(pru * 1.15, 2)
            lines.append(f"  + {p['name']} ({p['ticker']}) {p['qty']}t @ {pru}€ | SL {sl}€ | TP {tp}€")
        lines.append("SL -10% et TP +15% appliques. Ajuste avec /sl ou /tp si besoin.")
        if breach_alerts:
            lines.append("\nAlertes sur positions importées :")
            lines.extend(breach_alerts)

    if skipped:
        lines.append(f"\nDeja dans le portfolio ({len(skipped)} ignores) :")
        for p in skipped:
            lines.append(f"  = {p['name']} ({p['ticker']})")

    if errors:
        lines.append(f"\nErreurs (donnees incompletes) : {', '.join(errors)}")

    if not added and not skipped and not errors:
        lines.append("Aucune position exploitable detectee.")

    return "\n".join(lines)


# Gardé pour compatibilité — utilise import_screenshots avec une seule image
def read_portfolio_screenshot(image_bytes: bytes) -> str:
    return import_screenshots([image_bytes])
