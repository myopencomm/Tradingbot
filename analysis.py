import math
from datetime import datetime
import pytz
import portfolio
import prices
import research
from ai_provider import get_provider, VISION_PROMPT
from config import (TRADING_CONTEXT_PATH, DEFAULT_SL_PCT, DEFAULT_TP_PCT,
                    POSITION_BUDGET_PCT, POSITION_BUDGET_MAX)

PARIS = pytz.timezone("Europe/Paris")

_SL = f"{DEFAULT_SL_PCT:.0f}"
_TP = f"{DEFAULT_TP_PCT:.0f}"

TRADER_SYSTEM = f"""Tu es un expert trader actif sur tous les marchés accessibles via Bourse Direct (France).
Compte-titres ordinaire (CTO), horizon court à moyen terme (jours à quelques semaines).
Règles strictes: stop-loss -{_SL}% sur PRU, objectif minimum +{_TP}%, pas de levier.
TP STRETCH : +{_TP}% est un MINIMUM, pas un plafond. Si l'objectif analyste ou le
catalyseur justifie davantage, vise plus haut — et indique TOUJOURS le TP exact
en prix ET en % pour que l'ordre puisse être passé tel quel.
Univers : Euronext Paris/Growth, Euronext Amsterdam/Bruxelles, NYSE, NASDAQ, LSE, Xetra — tout ce qu'on peut acheter sur Bourse Direct.
Priorité au meilleur rapport risque/rendement, peu importe le marché."""

ANALYSIS_RULES = f"""
RÈGLES D'ANALYSE CRITIQUE — à appliquer AVANT tout signal ACHAT :
- RSI : survente = RSI < 30, surachat = RSI > 70. Entre 30 et 70 : NEUTRE —
  ne jamais parler de « survente relative » pour un RSI à 40-50.
- COUTEAU QUI TOMBE : si perf 1 an < -30% OU cours à moins de +15% du plus bas
  52 semaines → risque HIGH obligatoire, et ACHAT uniquement avec un catalyseur
  de RETOURNEMENT précis et daté. Des résultats trimestriels ordinaires ne
  suffisent PAS à retourner un titre en chute.
- OBJECTIFS ANALYSTES : cibles à 12 mois, souvent EN RETARD après une forte
  baisse (les analystes abaissent progressivement). Ne JAMAIS utiliser un
  objectif analyste comme TP ni comme preuve d'upside court terme — mention
  indicative uniquement.
- TP : doit être atteignable dans l'horizon du trade (jours à semaines).
  Plafond +{2 * DEFAULT_TP_PCT:.0f}% sauf événement binaire daté (OPA en cours,
  décision FDA). Une mégacap ne fait pas +50% sur des résultats trimestriels.
- TRADE MOMENTUM : l'absence de catalyseur daté n'est PAS un motif d'exclusion.
  Si tendance haussière confirmée — perf 3 mois positive ET momentum 1 mois
  positif ET RSI entre 40 et 70 — c'est une thèse ACHAT VALIDE de plein droit :
  TP +{DEFAULT_TP_PCT:.0f}% à +{1.5 * DEFAULT_TP_PCT:.0f}%, risque MEDIUM minimum,
  précise le niveau technique qui invalide la thèse (support/SL).
  Jamais sur un couteau qui tombe.
- SENTIMENT SOCIAL : signal d'appoint — jamais un argument principal d'achat.
- En cas de doute → EXCLUS. Mieux vaut zéro opportunité qu'une mauvaise.
"""

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
    today = datetime.now(PARIS).strftime("%d/%m/%Y")
    lines = [
        f"SNAPSHOT PORTEFEUILLE — SOURCE DE VÉRITÉ — {today}",
        f"💰 Cash: {cash}€",
        "📁 Positions (UNIQUEMENT ces positions sont actives — ignorer tout autre mention) :",
    ]
    for name, cfg in positions.items():
        quote = prices.get_quote(cfg["ticker"])
        price = quote.get("price")
        if price and not math.isnan(price):
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
    if not price or math.isnan(price):
        return None
    if price < sl:
        return f"⚠️ SL déjà dépassé : cours {price}€ < SL {sl}€ → /research {ticker}"
    if price > pru * 1.25:
        gain = ((price / pru) - 1) * 100
        return f"⚠️ TP dépassé (+{gain:.0f}%) : cours {price}€ → vendre ou /research {ticker}"
    return None


def morning_briefing(send_fn) -> None:
    """
    Briefing quotidien 9h05.
    - Analyse portefeuille : prompt direct avec données réelles.
    - Opportunités (si cash >= 1000€) : même 2 passes que scan_opportunities
      pour éviter que l'IA invente des prix depuis des articles web périmés.
    """
    print(f"[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Analyse matinale...")
    try:
        ai = get_provider()
        snapshot = _portfolio_snapshot()
        macro = research.market_context()
        cash = portfolio.get_cash()
        ctx = _trading_context()
        ctx_block = f"\n--- CONTEXTE PERSONNEL ---\n{ctx}\n" if ctx else ""
        today_str = datetime.now(PARIS).strftime("%d/%m/%Y")

        # News yfinance + fondamentaux clés par position
        enriched_lines = []
        for name, cfg in portfolio.load().get("positions", {}).items():
            pos_news = prices.get_yf_news(cfg["ticker"], max_items=2)
            funds    = prices.get_fundamentals(cfg["ticker"])
            parts    = []
            if funds.get("analyst_target"):
                parts.append(f"objectif analyste {funds['analyst_target']}")
            if funds.get("next_earnings"):
                parts.append(f"résultats le {funds['next_earnings']}")
            for n in pos_news:
                parts.append(n["title"])
            if parts:
                enriched_lines.append(f"  {name} : " + " | ".join(parts))
        enriched_block = ("\nNEWS & DONNÉES ANALYSTES\n" + "\n".join(enriched_lines)) if enriched_lines else ""

        # ── Passe 1 : analyse portefeuille (+ candidats si cash suffisant) ───
        if cash >= 1000:
            catalysts = research.market_catalysts()
            opps_mission = f"""
2. Identifie 3 à 6 tickers CANDIDATS de deux types :
   A) CATALYSEUR : événement futur daté après le {today_str}.
   B) MOMENTUM : tendance haussière établie rendant +10% atteignable sans événement.
   IMPÉRATIF : chaque candidat doit respecter TOUTES les règles et contraintes du contexte personnel.
   Réponds UNIQUEMENT avec les tickers Yahoo Finance (ex: ALFRE.PA, MSFT), un par ligne.
   Ne donne AUCUN prix — juste les tickers.
3. Risque global portefeuille : LOW / MEDIUM / HIGH"""
        else:
            catalysts = ""
            opps_mission = f"\n2. Risque global : LOW / MEDIUM / HIGH\n(Cash {cash}€ insuffisant pour nouvelles positions)"

        prompt1 = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}

AUJOURD'HUI : {today_str}
RÈGLE CATALYSEURS : événements futurs uniquement, datés après le {today_str}.
{ctx_block}
PORTEFEUILLE — SOURCE DE VÉRITÉ
{snapshot}
{enriched_block}

CONTEXTE MARCHÉ
{macro}

{f"CATALYSEURS — TOUS MARCHÉS{chr(10)}{catalysts}" if catalysts else ""}

MISSION
1. Pour chaque position : MAINTENIR / SURVEILLER / VENDRE + commentaire bref.
{opps_mission}"""

        pass1 = _strip_markdown(ai.complete(prompt1, max_tokens=600))

        # ── Passe 2 : validation des candidats avec prix réels ───────────────
        opportunities = []
        if cash >= 1000:
            # Extrait les tickers candidats (lignes courtes en majuscules)
            held_tickers = {cfg["ticker"].upper()
                            for cfg in portfolio.load().get("positions", {}).values()}
            raw_tickers = _extract_tickers(pass1)

            for t in raw_tickers[:5]:
                if t.upper() in held_tickers:
                    continue
                q = prices.get_quote(t)
                current_price = q.get("price")
                if not current_price:
                    continue

                tech   = prices.get_technicals(t)
                funds  = prices.get_fundamentals(t)
                pctx   = prices.get_price_context(t)
                yf_n   = prices.get_yf_news(t, max_items=4)
                social = research.get_social_sentiment(t)
                web    = research.research_stock(t)
                cats   = research.search_catalysts(t)

                tech_b = ""
                if tech:
                    tech_b = (
                        f"\nTECHNICALS : RSI {tech.get('rsi','N/A')} | "
                        f"Momentum {tech.get('momentum_1m','N/A'):+}% | "
                        f"Vol ratio {tech.get('vol_ratio','N/A')}x\n"
                    )
                if pctx:
                    tech_b += (
                        f"52 SEMAINES : perf 1 an {pctx['perf_1y']:+}% | "
                        f"+{pctx['from_52w_low']}% vs plus bas | "
                        f"{pctx['from_52w_high']}% vs plus haut\n"
                    )
                funds_b = ""
                fl = []
                if funds.get("analyst_target"):
                    fl.append(f"Objectif analyste : {funds['analyst_target']}")
                if funds.get("next_earnings"):
                    fl.append(f"Résultats le : {funds['next_earnings']}")
                if fl:
                    funds_b = "\n" + " | ".join(fl) + "\n"

                social_b = f"\nSENTIMENT : {social}" if social and "aucune donnée" not in social else ""
                news_b   = ("\nNEWS : " + " | ".join(n["title"] for n in yf_n[:3])) if yf_n else ""

                ctx_v = (f"\nCONTEXTE PERSONNEL — règles et contraintes à respecter "
                         f"IMPÉRATIVEMENT (toute violation → EXCLUS) :\n{ctx}\n") if ctx else ""
                val_prompt = f"""{TRADER_SYSTEM}
{ANALYSIS_RULES}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_v}
AUJOURD'HUI : {today_str}. TICKER : {t}. COURS RÉEL : {current_price}€. CASH DISPO : {cash}€.
{tech_b}{funds_b}{social_b}{news_b}

RECHERCHE WEB : {web}
CATALYSEURS : {cats}

Signal ACHAT ou NEUTRE/ÉVITER ?
Si NEUTRE/ÉVITER → réponds exactement : EXCLUS
Si le ticker viole une contrainte du contexte personnel → réponds exactement : EXCLUS
Si ACHAT → format :
{t} — Entrée : {current_price}€  SL : X€ (-{_SL}%)  TP : X€ (+X% — minimum +{_TP}%, plus si le potentiel le justifie)
- Thèse : [CATALYSEUR : événement + date après {today_str}] OU [MOMENTUM : tendance + niveau qui invalide]
- Raison : 1 phrase  Risque : LOW/MEDIUM/HIGH"""

                val = _strip_markdown(ai.complete(val_prompt, max_tokens=200))
                if val.strip().upper().startswith("EXCLU"):
                    continue
                val = _validate_tickers(val)
                opportunities.append(val)

        # ── Assemblage final ─────────────────────────────────────────────────
        # Extrait la partie analyse portefeuille (avant les tickers candidats)
        portfolio_analysis = "\n".join(
            l for l in pass1.splitlines()
            if not (len(l.strip()) <= 12 and l.strip().replace(".", "").replace("-", "").isupper())
        ).strip()

        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        msg  = f"🌅 BRIEFING — {date}\n\n{snapshot}\n\n{portfolio_analysis}"
        if opportunities:
            msg += "\n\nOPPORTUNITÉS VALIDÉES\n" + "\n\n".join(opportunities)
        elif cash >= 1000:
            msg += "\n\nAucune opportunité ne passe le filtre technique aujourd'hui."
        send_fn(msg)

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


def research_ticker(send_fn, ticker: str, question: str = "") -> None:
    """Analyse approfondie d'un ticker. Si question fournie, répond à cette question précise."""
    try:
        ai   = get_provider()
        ctx  = _trading_context()
        ctx_block = f"\nCONTEXTE PERSONNEL\n{ctx}\n" if ctx else ""

        # Cherche si le ticker est en portefeuille
        # Correspondance : ticker exact, ticker sans suffixe, nom de position
        data      = portfolio.load()
        positions = data.get("positions", {})
        query_up  = ticker.upper()
        query_base = query_up.split(".")[0]  # ex: "EXENS" depuis "EXENS.PA"

        held_name = None
        held      = None
        for pos_name, cfg in positions.items():
            stored = cfg["ticker"].upper()
            stored_base = stored.split(".")[0]
            if (stored == query_up
                    or stored_base == query_base
                    or pos_name.upper() == query_up
                    or pos_name.upper() == query_base):
                held      = cfg
                held_name = pos_name
                break

        # Fallback flou : nom de société complet vs mnémonique abrégé
        # (ex: /research EXOSENS → position EXENS, /research ILLUMINA → ILMN)
        if not held and positions:
            import difflib

            def _subseq(short: str, long_: str) -> bool:
                """Les lettres de short apparaissent dans l'ordre dans long_."""
                it = iter(long_)
                return all(ch in it for ch in short)

            scores = {}
            for pos_name, cfg in positions.items():
                stored_base = cfg["ticker"].upper().split(".")[0]
                score = max(
                    difflib.SequenceMatcher(None, query_base, pos_name.upper()).ratio(),
                    difflib.SequenceMatcher(None, query_base, stored_base).ratio(),
                )
                # Mnémonique (≥4 lettres) contenu en ordre dans le nom tapé
                if (len(stored_base) >= 4 and len(query_base) > len(stored_base)
                        and _subseq(stored_base, query_base)):
                    score = max(score, 0.9)
                scores[pos_name] = score
            best = max(scores, key=scores.get)
            if scores[best] >= 0.75:
                held      = positions[best]
                held_name = best
                send_fn(f"ℹ️ {ticker} interprété comme ta position {best} "
                        f"({positions[best]['ticker']})")

        # Utilise le ticker stocké (exact) pour toutes les requêtes de données
        real_ticker = held["ticker"] if held else ticker

        web       = research.research_stock(real_ticker)
        catalysts = research.search_catalysts(real_ticker)
        tech      = prices.get_technicals(real_ticker)
        funds     = prices.get_fundamentals(real_ticker)
        pctx      = prices.get_price_context(real_ticker)
        yf_news   = prices.get_yf_news(real_ticker)
        social    = research.get_social_sentiment(real_ticker)

        tech_block = ""
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
        if pctx:
            tech_block += (
                f"- Performance 1 an : {pctx['perf_1y']:+}%"
                + (f" | 3 mois : {pctx['perf_3m']:+}%" if "perf_3m" in pctx else "") + "\n"
                f"- Range 52 semaines : +{pctx['from_52w_low']}% vs plus bas, "
                f"{pctx['from_52w_high']}% vs plus haut\n"
            )

        funds_lines = []
        if funds.get("analyst_target"):
            funds_lines.append(f"- Objectif analyste moyen : {funds['analyst_target']}")
        if funds.get("pe"):
            funds_lines.append(f"- P/E : {funds['pe']}")
        if funds.get("beta"):
            funds_lines.append(f"- Beta : {funds['beta']}")
        if funds.get("week52_low") and funds.get("week52_high"):
            funds_lines.append(f"- Range 52 semaines : {funds['week52_low']} — {funds['week52_high']}")
        if funds.get("market_cap_m"):
            funds_lines.append(f"- Capitalisation : {funds['market_cap_m']:.0f}M€")
        if "analyst_buy" in funds:
            funds_lines.append(
                f"- Consensus analystes : {funds['analyst_buy']} Achat / "
                f"{funds['analyst_hold']} Neutre / {funds['analyst_sell']} Vente"
            )
        if funds.get("next_earnings"):
            funds_lines.append(f"- Prochains résultats : {funds['next_earnings']}")
        funds_block = ("\nFONDAMENTAUX\n" + "\n".join(funds_lines)) if funds_lines else ""

        news_block = ""
        if yf_news:
            news_lines = [f"- {n['title']} ({n['publisher']})" for n in yf_news]
            news_block = "\nACTUALITÉS RÉCENTES (Yahoo Finance)\n" + "\n".join(news_lines)

        if held:
            quote = prices.get_quote(held["ticker"])
            price = quote.get("price", "?")
            sym   = prices.currency_symbol(quote.get("currency", "EUR"))
            chg   = ((price - held["entry_price"]) / held["entry_price"]) * 100 if isinstance(price, float) else 0
            pnl   = (price - held["entry_price"]) * held["qty"] if isinstance(price, float) else 0
            pct_to_tp = ((held["target_high"] - price) / price * 100) if isinstance(price, float) else "?"
            pct_to_sl = ((price - held["target_low"]) / price * 100) if isinstance(price, float) else "?"

            social_block = f"\nSENTIMENT SOCIAL\n{social}" if social and "aucune donnée" not in social else ""
            question_block = f"\nQUESTION SPÉCIFIQUE : {question}" if question else ""
            prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
JE DÉTIENS {ticker} — ANALYSE MA POSITION, PAS UNE NOUVELLE ENTRÉE.

MA POSITION
- Cours actuel : {sym}{price} ({chg:+.2f}%)
- PRU : {sym}{held['entry_price']} | {held['qty']} titres | P&L : {sym}{pnl:+.0f}
- SL actuel : {sym}{held['target_low']} (marge : {pct_to_sl:.1f}%)
- TP actuel : {sym}{held['target_high']} (potentiel restant : {pct_to_tp:.1f}%)
{tech_block}{funds_block}{news_block}{social_block}

RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{catalysts}
{question_block}
INTERDICTIONS ABSOLUES : ne mentionne pas de point d'entrée, pas de "si tu n'as pas encore la position", pas de signal ACHAT, pas de niveau d'achat recommandé. Je suis déjà en position — tout conseil d'entrée est hors sujet.

RÉPONDS EN 3 BLOCS UNIQUEMENT :

POTENTIEL RESTANT
- L'action a-t-elle encore du chemin vers le TP ? Pourquoi ?
- Quels catalyseurs peuvent débloquer la hausse ?
- Quels risques peuvent faire plonger le cours ?

MON SL / TP SONT-ILS ENCORE BONS ?
- Faut-il remonter le SL pour protéger des gains, ou le laisser ?
- Le TP est-il toujours réaliste au vu des news ?

DÉCISION
- Réponds directement à la question spécifique si elle est posée.
- CONSERVER / VENDRE MAINTENANT / ALLÉGER / AJUSTER SL ou TP — aucune autre option.
- Une phrase de justification. Pas de bla-bla."""

        else:
            social_block = f"\nSENTIMENT SOCIAL\n{social}" if social and "aucune donnée" not in social else ""
            question_block = f"\nQUESTION SPÉCIFIQUE : {question}" if question else ""
            prompt = f"""{TRADER_SYSTEM}
{ANALYSIS_RULES}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_block}
TICKER ANALYSÉ : {ticker} — JE NE DÉTIENS PAS CETTE ACTION.
{tech_block}{funds_block}{news_block}{social_block}

RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{catalysts}
{question_block}
Y a-t-il une opportunité d'entrée sur {ticker} ?
Réponds directement à la question spécifique si elle est posée.
Format : SIGNAL (ACHAT / NEUTRE / ÉVITER), prix d'entrée, SL (-{_SL}%), TP (+{_TP}% minimum — plus haut si le potentiel le justifie, % exact obligatoire), catalyseur principal, risque (LOW/MEDIUM/HIGH).
Si NEUTRE ou ÉVITER : explique pourquoi en 2 lignes max."""

        result = _strip_markdown(ai.complete(prompt, max_tokens=600))
        if not held:
            result = _validate_tickers(result)
        label = f"{held_name} ({real_ticker})" if held else real_ticker
        send_fn(f"🔍 ANALYSE {label}\n\n{result}")

    except Exception as e:
        send_fn(f"Erreur analyse {ticker}: {e}")


def _extract_tickers(text: str) -> list[str]:
    """Extrait les tickers format Yahoo Finance d'un texte (ex: GET.PA, MSFT, BP.L)."""
    import re
    return list(dict.fromkeys(re.findall(
        r'\b([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\b', text
    )))


def scan_opportunities(send_fn, ticker: str = None) -> None:
    """
    Scan en 2 passes pour éviter l'incohérence avec /research :
    - Passe 1 : l'IA identifie des tickers candidats depuis les news/catalyseurs
    - Passe 2 : on fetch les vrais technicals + fondamentaux pour chaque candidat,
      puis l'IA valide avec les mêmes données que /research (peut dire NEUTRE → exclu)
    """
    try:
        ai = get_provider()
        cash = portfolio.get_cash()
        snapshot = _portfolio_snapshot()
        ctx = _trading_context()
        ctx_block = f"\n{ctx}\n" if ctx else ""

        if ticker:  # backward compat
            return research_ticker(send_fn, ticker)

        today_str = datetime.now(PARIS).strftime("%d/%m/%Y")
        macro     = research.market_context()
        catalysts = research.market_catalysts()

        # News yfinance sur les positions en portefeuille
        positions_news = []
        for name, cfg in portfolio.load().get("positions", {}).items():
            pos_news = prices.get_yf_news(cfg["ticker"], max_items=2)
            for n in pos_news:
                positions_news.append(f"- {name} : {n['title']}")
        news_block = ("\nNEWS RÉCENTES POSITIONS\n" + "\n".join(positions_news)) if positions_news else ""

        # ── Passe 1 : positions + candidats (tickers uniquement) ─────────────
        pass1_prompt = f"""{TRADER_SYSTEM}
{TICKER_RULES}
{FORMAT_TELEGRAM}

AUJOURD'HUI : {today_str}
{ctx_block}
{snapshot}
{news_block}

CONTEXTE MARCHÉ
{macro}

CATALYSEURS IMMINENTS — TOUS MARCHÉS
{catalysts}

TÂCHE EN 2 PARTIES :

1. Pour chaque position en portefeuille : 1 ligne — MAINTENIR / SURVEILLER / VENDRE + raison.

2. Identifie 3 à 6 tickers CANDIDATS de deux types :
   A) CATALYSEUR : événement futur daté APRÈS le {today_str} (résultats, OPA, FDA, contrat).
   B) MOMENTUM : tendance haussière établie (hausse sur 3 mois, volumes sains)
      rendant +10% atteignable SANS événement particulier.
   IMPÉRATIF : chaque candidat doit respecter TOUTES les règles et contraintes
   du contexte personnel ci-dessus (secteurs exclus, critères sur le ticker, etc.).
Réponds pour la partie 2 UNIQUEMENT avec les tickers, format Yahoo Finance, un par ligne.
Exemple : GET.PA
          DSY.PA
          MSFT
Ne donne PAS de prix, pas d'analyse — juste les tickers."""

        pass1 = _strip_markdown(ai.complete(pass1_prompt, max_tokens=400))

        # Sépare l'analyse positions de la liste de tickers
        lines = pass1.strip().splitlines()
        portfolio_lines = []
        candidate_lines = []
        in_candidates = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Heuristique : ligne courte sans ponctuation = ticker candidat
            if len(stripped) <= 12 and stripped.replace(".", "").replace("-", "").isupper():
                in_candidates = True
            if in_candidates:
                candidate_lines.append(stripped)
            else:
                portfolio_lines.append(stripped)

        portfolio_summary = "\n".join(portfolio_lines)
        raw_tickers = _extract_tickers(" ".join(candidate_lines))

        # Filtre : uniquement les tickers Yahoo Finance valides (prix disponible)
        held_tickers = {cfg["ticker"].upper() for cfg in portfolio.load().get("positions", {}).values()}
        valid_candidates = []
        for t in raw_tickers[:6]:
            if t.upper() in held_tickers:
                continue
            q = prices.get_quote(t)
            if q.get("price"):
                valid_candidates.append((t, q["price"]))

        # ── Passe 2 : validation avec vrais données techniques ───────────────
        if not valid_candidates:
            send_fn(
                f"🔍 SCAN OPPORTUNITÉS\n\n"
                f"POSITIONS\n{portfolio_summary}\n\n"
                f"Aucun candidat avec catalyseur futur vérifiable identifié aujourd'hui.\n\n"
                f"💰 Cash: {cash}€"
            )
            return

        opportunities = []
        for t, current_price in valid_candidates[:4]:
            tech   = prices.get_technicals(t)
            funds  = prices.get_fundamentals(t)
            pctx   = prices.get_price_context(t)
            yf_news = prices.get_yf_news(t, max_items=4)
            web    = research.research_stock(t)
            cats   = research.search_catalysts(t)
            social = research.get_social_sentiment(t)

            pctx_block = ""
            if pctx:
                pctx_block = (
                    f"\nCONTEXTE 52 SEMAINES\n"
                    f"- Performance 1 an : {pctx['perf_1y']:+}%"
                    + (f" | 3 mois : {pctx['perf_3m']:+}%" if "perf_3m" in pctx else "") + "\n"
                    f"- Cours actuel : +{pctx['from_52w_low']}% au-dessus du plus bas 52s, "
                    f"{pctx['from_52w_high']}% vs plus haut 52s\n"
                )

            tech_block = ""
            if tech:
                tech_block = (
                    f"\nINDICATEURS TECHNIQUES\n"
                    f"- RSI 14j : {tech.get('rsi', 'N/A')}\n"
                    f"- Momentum 1 mois : {tech.get('momentum_1m', 'N/A'):+}%\n"
                    f"- Volume ratio : {tech.get('vol_ratio', 'N/A')}x moyenne 20j\n"
                )

            funds_lines = []
            if funds.get("analyst_target"):
                funds_lines.append(f"- Objectif analyste : {funds['analyst_target']}")
            if funds.get("next_earnings"):
                funds_lines.append(f"- Prochains résultats : {funds['next_earnings']}")
            if "analyst_buy" in funds:
                funds_lines.append(
                    f"- Consensus : {funds['analyst_buy']} Achat / "
                    f"{funds['analyst_hold']} Neutre / {funds['analyst_sell']} Vente"
                )
            funds_block = ("\nFONDAMENTAUX\n" + "\n".join(funds_lines)) if funds_lines else ""

            news_lines = [f"- {n['title']} ({n['publisher']})" for n in yf_news]
            news_b = ("\nACTUALITÉS\n" + "\n".join(news_lines)) if news_lines else ""

            social_b = f"\nSENTIMENT SOCIAL\n{social}" if social and "aucune donnée" not in social else ""
            ctx_v = (f"\nCONTEXTE PERSONNEL — règles et contraintes à respecter "
                     f"IMPÉRATIVEMENT (toute violation → EXCLUS) :\n{ctx}\n") if ctx else ""
            validate_prompt = f"""{TRADER_SYSTEM}
{ANALYSIS_RULES}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_v}
AUJOURD'HUI : {today_str}
TICKER ANALYSÉ : {t} — JE NE DÉTIENS PAS CETTE ACTION. CASH DISPONIBLE : {cash}€.
Cours actuel : {current_price}
{pctx_block}{tech_block}{funds_block}{news_b}{social_b}

RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{cats}

Signal ACHAT ou NEUTRE/ÉVITER ?
RÈGLE : si tu donnerais NEUTRE ou ÉVITER dans un /research → réponds EXCLUS en 1 mot.
RÈGLE : si le ticker viole une contrainte du contexte personnel → réponds EXCLUS en 1 mot.
Si ACHAT : donne format exact :
NOM SOCIETE ({t})
- Marché : ...
- Cours actuel : {current_price} | Entrée : X  SL : X (-{_SL}%)  TP : X (+X% — minimum +{_TP}%, plus si le potentiel le justifie)
- Thèse : [CATALYSEUR : événement précis + date après {today_str}]
  OU [MOMENTUM : tendance + niveau technique qui invalide la thèse — cf. règles]
- Raison : 1 phrase
- Risque : LOW / MEDIUM / HIGH"""

            val = _strip_markdown(ai.complete(validate_prompt, max_tokens=250))
            if val.strip().upper().startswith("EXCLU"):
                continue
            val = _validate_tickers(val)

            # Feature scan→ordre : sizing affiché + commande prête à l'emploi.
            # Budget configurable via .env : POSITION_BUDGET_PCT / POSITION_BUDGET_MAX
            try:
                budget = min(cash * POSITION_BUDGET_PCT / 100, POSITION_BUDGET_MAX)
                qty_sugg = max(1, int(budget / current_price)) if current_price else 1
                cost = qty_sugg * current_price
                val += (
                    f"\n→ Taille suggérée : {qty_sugg} titres ≈ {cost:.0f}€ "
                    f"({cost / cash * 100:.0f}% du cash)\n"
                    f"→ Passer l'ordre (mode Playwright) :\n"
                    f"   /ordre acheter {t} {qty_sugg} limite {current_price}"
                )
                # Reprend le SL/TP conseillés par l'IA (TP stretch inclus) pour
                # que l'ordre de protection reflète exactement le conseil donné.
                sl_m = re.search(r"\bSL\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
                tp_m = re.search(r"\bTP\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
                if sl_m and tp_m:
                    sl_v = float(sl_m.group(1).replace(",", "."))
                    tp_v = float(tp_m.group(1).replace(",", "."))
                    if sl_v < current_price < tp_v:
                        # Plafond mécanique : un TP au-delà de 2x l'objectif
                        # minimum est hors horizon court terme (objectif 12 mois
                        # pris pour un TP) — on le ramène au plafond.
                        tp_cap = round(current_price * (1 + 2 * DEFAULT_TP_PCT / 100), 2)
                        capped = ""
                        if tp_v > tp_cap:
                            capped = f" — TP IA {tp_v}€ hors horizon, plafonné"
                            tp_v = tp_cap
                        tp_pct = (tp_v / current_price - 1) * 100
                        stretch = f" (TP +{tp_pct:.0f}%{capped})" if tp_pct >= 11 or capped else ""
                        val += (
                            f"\n   puis protection : /ordre vendre {t} {qty_sugg} "
                            f"expert {sl_v} {tp_v}{stretch}"
                        )
            except Exception:
                pass

            opportunities.append(val)

        # ── Assemblage final ──────────────────────────────────────────────────
        result_parts = [f"POSITIONS\n{portfolio_summary}"]
        if opportunities:
            result_parts.append("OPPORTUNITÉS VALIDÉES\n" + "\n\n".join(opportunities))
        else:
            result_parts.append(
                "Aucun candidat ne passe le filtre technique aujourd'hui.\n"
                "Attendre un meilleur point d'entrée ou de nouveaux catalyseurs."
            )

        send_fn(f"🔍 SCAN OPPORTUNITÉS\n\n" + "\n\n".join(result_parts) + f"\n\n💰 Cash: {cash}€")

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
            sl  = round(pru * (1 - DEFAULT_SL_PCT / 100), 2)
            tp  = round(pru * (1 + DEFAULT_TP_PCT / 100), 2)
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
            sl  = round(pru * (1 - DEFAULT_SL_PCT / 100), 2)
            tp  = round(pru * (1 + DEFAULT_TP_PCT / 100), 2)
            lines.append(f"  + {p['name']} ({p['ticker']}) {p['qty']}t @ {pru}€ | SL {sl}€ | TP {tp}€")
        lines.append(f"SL -{DEFAULT_SL_PCT:.0f}% et TP +{DEFAULT_TP_PCT:.0f}% appliques. Ajuste avec /sl ou /tp si besoin.")
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
