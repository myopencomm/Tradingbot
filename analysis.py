from datetime import datetime
import pytz
import portfolio
import prices
import research
from ai_provider import get_provider, VISION_PROMPT
from config import TRADING_CONTEXT_PATH

PARIS = pytz.timezone("Europe/Paris")

TRADER_SYSTEM = """Tu es un expert trader spécialisé en small et mid caps compatibles avec Bourse Direct (France).
Compte-titres ordinaire (CTO), horizon court à moyen terme (jours à quelques semaines).
Règles strictes: stop-loss -10% sur PRU, objectif minimum +15%, pas de levier, univers prioritaire Euronext Paris / Euronext Growth."""

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
            chg     = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
            pnl     = (price - cfg["entry_price"]) * cfg["qty"]
            currency = quote.get("currency", "EUR")
            native   = quote.get("price_native")
            prix_str = (
                f"{price}€ (${native})" if currency != "EUR" and native
                else f"{price}€"
            )
            lines.append(
                f"  {name} ({cfg['ticker']}): {prix_str} ({chg:+.2f}%) | "
                f"PRU {cfg['entry_price']}€ | {cfg['qty']}t | P&L {pnl:+.0f}€ | "
                f"SL {cfg['target_low']}€ | TP {cfg['target_high']}€"
            )
        elif quote.get("status") in ("suspended", "error"):
            lines.append(
                f"  {name} ({cfg['ticker']}): ⛔ COURS SUSPENDU — non vendable (liquidation judiciaire ?) | "
                f"PRU {cfg['entry_price']}€ | {cfg['qty']}t"
            )
        else:
            lines.append(f"  {name}: prix indisponible | PRU {cfg['entry_price']}€ | {cfg['qty']}t")
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

        prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
PORTEFEUILLE
{snapshot}

CONTEXTE MARCHÉ
{macro}

MISSION
1. Pour chaque position : signal (conserver/alléger/vendre), tendance courte, commentaire bref.
2. Top 3 opportunités pour le cash disponible ({cash}€) :
   TICKER — prix entrée — SL — TP — raison — risque
3. Risque global : LOW / MEDIUM / HIGH"""

        result = _strip_markdown(ai.complete(prompt, max_tokens=900))
        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        send_fn(f"🌅 BRIEFING — {date}\n\n{snapshot}\n\n{result}")

    except Exception as e:
        print(f"Erreur briefing: {e}")
        send_fn(f"⚠️ Erreur briefing matinal: {e}")


def scan_opportunities(send_fn, ticker: str = None) -> None:
    """Scan général ou analyse d'un ticker spécifique."""
    try:
        ai = get_provider()
        cash = portfolio.get_cash()
        snapshot = _portfolio_snapshot()

        ctx = _trading_context()
        ctx_block = f"\n{ctx}\n" if ctx else ""

        if ticker:
            web = research.research_stock(ticker)
            prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
{snapshot}

RECHERCHE WEB {ticker}
{web}

Donne un avis actionnable : signal (ACHAT/VENTE/NEUTRE), prix d'entrée, SL (-10%), TP (+15%), raison courte, niveau de risque."""
            header = f"🔍 ANALYSE {ticker}"
        else:
            macro = research.market_context()
            prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
{snapshot}

CONTEXTE MARCHÉ
{macro}

Cash disponible : {cash}€
Propose 3 opportunités Euronext adaptées.
Pour chaque opportunité :
TICKER
- Entrée : Xé  SL : X€  TP : X€
- Raison : ...
- Risque : LOW / MEDIUM / HIGH"""
            header = "🔍 SCAN OPPORTUNITÉS"

        result = _strip_markdown(ai.complete(prompt, max_tokens=700))
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
