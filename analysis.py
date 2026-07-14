import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pytz
import portfolio
import prices
import research
from ai_provider import get_provider, VISION_PROMPT
from config import (TRADING_CONTEXT_PATH, MACRO_ANALYSIS_PATH,
                    DEFAULT_SL_PCT, DEFAULT_TP_PCT,
                    POSITION_BUDGET_PCT, POSITION_BUDGET_MAX,
                    BROKERAGE_FEE, MIN_NET_GAIN_FEE_RATIO,
                    FALLBACK_TP_MIN_PCT, FALLBACK_TP_MAX_PCT,
                    RSI_ENTRY_MIN, RSI_ENTRY_MAX, RSI_HARD_MAX,
                    ATR_SL_MULT, MIN_SL_PCT, MAX_SL_PCT, MIN_RR,
                    SMALL_GAIN_MODE)

# ── Univers de scan (~100 actions Bourse Direct) ──────────────────────────────
# Le filtre quantitatif (RSI/momentum/volume) élimine les tickers invalides
# ou sans données — inutile de maintenir cette liste à la main.
SCAN_UNIVERSE = [
    # CAC 40
    "AI.PA", "AIR.PA", "ALO.PA", "BN.PA", "BNP.PA", "CA.PA", "CAP.PA",
    "CS.PA", "DG.PA", "DSY.PA", "EL.PA", "EN.PA", "ENGI.PA", "ERF.PA",
    "TTE.PA", "GLE.PA", "HO.PA", "KER.PA", "LR.PA", "MC.PA", "ML.PA",
    "MT.AS", "ORA.PA", "PUB.PA", "RI.PA", "RMS.PA", "RNO.PA", "SAF.PA",
    "SAN.PA", "SGO.PA", "STM.PA", "SU.PA", "TEP.PA", "VIE.PA", "AC.PA",
    "ACA.PA", "STLAM.PA",
    # Euronext Paris — Midcap / SBF 120
    "AF.PA", "AMUN.PA", "BIM.PA", "DASSAV.PA", "FDJ.PA", "FR.PA",
    "GET.PA", "GTT.PA", "RXL.PA", "SEB.PA", "SFCA.PA", "UBI.PA",
    "VK.PA", "COFA.PA", "SCOR.PA", "SPIE.PA", "TFI.PA", "LI.PA",
    "TKTT.PA", "FNAC.PA", "WLN.PA", "BOL.PA", "EDEN.PA", "FLO.PA",
    "OREP.PA", "LANC.PA", "TNG.PA", "SESL.PA", "CAPP.PA",
    # Euronext Amsterdam
    "ASML.AS", "INGA.AS", "PHIA.AS", "UNA.AS", "ADYEN.AS", "HEIA.AS",
    "NN.AS", "AKZA.AS", "WKL.AS",
    # Euronext Bruxelles
    "UCB.BR", "ABI.BR", "SOLB.BR", "GBLB.BR",
    # US — Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AMD", "QCOM", "NFLX", "CRM", "ORCL", "INTC", "MU",
    # US — Finance
    "JPM", "GS", "V", "MA", "BAC",
    # US — Pharma / Santé
    "JNJ", "PFE", "ABBV", "LLY", "MRK", "AMGN",
    # US — Énergie / Défense / Industrie
    "XOM", "CVX", "RTX", "LMT", "BA", "NOC",
    # US — Conso / Autre
    "COST", "WMT", "DIS", "PYPL", "SBUX",
]

PARIS = pytz.timezone("Europe/Paris")


def _lessons_block() -> str:
    """Bloc de leçons (brique 3) à injecter dans les prompts de validation.
    Vide tant qu'il n'y a pas assez de trades tagués — jamais bloquant."""
    try:
        import lessons
        b = lessons.build_lessons_block()
        return f"\n{b}\n" if b else ""
    except Exception:
        return ""


def _entry_ctx(tech: dict, pctx: dict, thesis: str, source: str,
               regime: str = "") -> dict:
    """Assemble le contexte d'entrée mémorisé pour la boucle d'apprentissage
    (brique 1) : indicateurs au moment de la décision + thèse + régime."""
    tech = tech or {}
    pctx = pctx or {}
    return {
        "source":      source,
        "regime":      regime,
        "rsi":         tech.get("rsi"),
        "momentum_1m": tech.get("momentum_1m"),
        "mom_12_1":    tech.get("mom_12_1"),
        "above_ma200": tech.get("above_ma200"),
        "atr_pct":     tech.get("atr_pct"),
        "vol_ratio":   tech.get("vol_ratio"),
        "perf_1y":     pctx.get("perf_1y"),
        "from_52w_low": pctx.get("from_52w_low"),
        "thesis":      (thesis or "").strip()[:200],
    }


def _trigger_autonomous(send_fn) -> None:
    """Si le mode autonome est actif + Playwright connecté, entre immédiatement
    sur les opportunités validées à l'instant, sans attendre le check planifié."""
    try:
        import autonomous_engine, bot_mode, playwright_session as pw_sess
        if (autonomous_engine.is_enabled()
                and bot_mode.is_playwright()
                and pw_sess.is_connected()):
            import threading
            threading.Thread(
                target=autonomous_engine.run_entry_cycle,
                args=(send_fn,),
                daemon=True,
            ).start()
    except Exception as e:
        print(f"[Auto trigger] {e}")


_SL = f"{DEFAULT_SL_PCT:.0f}"
_TP = f"{DEFAULT_TP_PCT:.0f}"

# Empêche deux scans simultanés (le scan est lourd : web + vision IA × 8 candidats).
# Deux scans concurrents doublent la charge réseau et peuvent se figer mutuellement.
_scan_lock = threading.Lock()

TRADER_SYSTEM = f"""Tu es un expert trader actif sur tous les marchés accessibles via Bourse Direct (France).
Compte-titres ordinaire (CTO), horizon swing/momentum : semaines à quelques mois —
on laisse courir les gagnants, on coupe vite les perdants. Pas de levier.
Règles strictes :
- STOP-LOSS technique : sous le dernier support, ≈ 2×ATR sous l'entrée
  (l'ATR 14j est fourni), jamais plus de {MAX_SL_PCT:.0f}% ni moins de {MIN_SL_PCT:.0f}%.
- TAKE-PROFIT : minimum {MIN_RR:.1f}× la distance du SL (ratio risque/rendement),
  objectif type +{_TP}%. +{_TP}% est un MINIMUM, pas un plafond : si le potentiel
  le justifie, vise plus haut — indique TOUJOURS le TP exact en prix ET en %.
Univers : Euronext Paris/Growth, Euronext Amsterdam/Bruxelles, NYSE, NASDAQ, LSE, Xetra — tout ce qu'on peut acheter sur Bourse Direct.
Priorité au meilleur rapport risque/rendement, peu importe le marché."""

ANALYSIS_RULES = f"""
RÈGLES D'ANALYSE CRITIQUE — à appliquer AVANT tout signal ACHAT :
- STRATÉGIE DE FOND (validée par la recherche académique) : on achète la FORCE
  ÉTABLIE (momentum 12 mois hors dernier mois positif, cours > MM200) au moment
  d'un REPLI SAIN — jamais la surchauffe du mois en cours. Le momentum 1 mois
  seul S'INVERSE statistiquement : un titre qui vient de faire +15% sur le mois
  avec RSI > {RSI_HARD_MAX:.0f} est un MAUVAIS point d'entrée, pas un signal d'achat.
- RSI : survente = RSI < 30. Zone d'ENTRÉE saine : {RSI_ENTRY_MIN:.0f}-{RSI_ENTRY_MAX:.0f}
  (pullback dans une tendance haussière au-dessus de la MM200).
  RSI > {RSI_HARD_MAX:.0f} : PAS de nouvelle entrée — attendre le repli (réversion
  court terme documentée). Ce seuil est appliqué aussi par un garde-fou
  quantitatif indépendant de ton verdict.
  Ne jamais parler de « survente relative » pour un RSI à 40-50.
- MM200 : cours sous la MM200 = tendance long terme non confirmée → EXCLUS
  (sauf mission CORRECTION explicite avec force relative).
- COUTEAU QUI TOMBE : si perf 1 an < -30% OU cours à moins de +15% du plus bas
  52 semaines → risque HIGH obligatoire, et ACHAT uniquement avec un catalyseur
  de RETOURNEMENT précis et daté. Des résultats trimestriels ordinaires ne
  suffisent PAS à retourner un titre en chute.
- OBJECTIFS ANALYSTES : cibles à 12 mois, souvent EN RETARD après une forte
  baisse (les analystes abaissent progressivement). Ne JAMAIS utiliser un
  objectif analyste comme TP ni comme preuve d'upside court terme — mention
  indicative uniquement.
- SL : technique, sous le dernier support, ≈ {ATR_SL_MULT:.0f}×ATR sous l'entrée,
  borné {MIN_SL_PCT:.0f}-{MAX_SL_PCT:.0f}%. Si {ATR_SL_MULT:.0f}×ATR dépasse {MAX_SL_PCT:.0f}%,
  le titre est trop volatil pour la taille de compte → EXCLUS.
- TP : minimum {MIN_RR:.1f}× la distance du SL, atteignable dans l'horizon du
  trade (semaines à quelques mois). Plafond +{2 * DEFAULT_TP_PCT:.0f}% sauf événement
  binaire daté (OPA en cours, décision FDA). Une mégacap ne fait pas +50% sur
  des résultats trimestriels.
- OPA / OFFRE DE RACHAT — RÈGLE ABSOLUE : si une OPA, offre de rachat ou fusion
  est en cours à un prix P, le cours ne peut PHYSIQUEMENT pas dépasser P (sauf
  surenchère). Le TP DOIT être ≤ P. Si le spread (P − cours_actuel) / cours_actuel
  est inférieur au TP minimum requis (+{_TP}%) → EXCLUS OBLIGATOIRE, sans exception.
  Exemple : OPA à 15.60€, cours 15.35€ → spread +1.6% → EXCLUS (min +{_TP}% requis).
  Une OPA n'est PAS un catalyseur haussier au-delà du prix d'offre — c'est un plafond dur.
  Si la recherche web mentionne une OPA, une acquisition, un rachat ou un "takeover bid"
  sur ce titre → vérifier immédiatement le prix d'offre avant tout autre raisonnement.
- CATALYSEUR : l'absence de catalyseur daté n'est PAS un motif d'exclusion —
  le momentum 12 mois + tendance MM200 EST la thèse. Mais un événement binaire
  IMMINENT (résultats < 5 jours, décision réglementaire) sur une position
  swing = risque HIGH, à signaler.
- SENTIMENT SOCIAL : signal d'appoint — jamais un argument principal d'achat.
"""

# Directive injectée dans les prompts de validation. Cadre la mission : le
# candidat a passé un filtre quantitatif VALIDÉ (momentum 12-1 + MM200 + zone
# RSI saine). Le rôle de l'IA : décision SYMÉTRIQUE — chercher les défauts
# disqualifiants que les chiffres ne voient pas (news, OPA, illiquidité,
# événement binaire), sans exiger de catalyseur ni forcer l'achat.
SCREEN_DIRECTIVE = f"""
CADRE DE DÉCISION — ce candidat a passé le filtre quantitatif validé par la
recherche : momentum 12 mois (hors dernier mois) positif, cours > MM200, RSI
en zone d'entrée {RSI_ENTRY_MIN:.0f}-{RSI_ENTRY_MAX:.0f} (pullback, pas surchauffe).
La thèse quantitative est donc SOLIDE a priori. Ton rôle est le contrôle
QUALITATIF que les chiffres ne voient pas. Décide de façon SYMÉTRIQUE :
ne force ni l'achat ni la prudence.

NE SONT PAS des motifs d'exclusion :
- « pas de catalyseur daté » (le momentum 12 mois + tendance EST la thèse)
- « objectif analyste sous le cours » (cible 12 mois, en retard sur le prix)
- sentiment de marché « fear » général (non spécifique au titre)
- un repli récent du cours : c'est précisément le point d'entrée recherché,
  tant que le support tient et que la tendance MM200 est intacte

SONT des motifs d'EXCLUSION légitimes :
- une NEWS précise qui invalide la tendance (profit warning, scandale, perte
  de contrat, guidance abaissée)
- OPA plafonnée (spread < +{_TP}%)
- événement binaire imminent (résultats/décision < 5 jours) — trop de risque de gap
- illiquidité réelle / société en difficulté financière
- structure technique cassée : support majeur perdu, cours repassé sous MM200
- couteau qui tombe (perf 1 an < -30% ou cours < +15% du plus bas 52s)
Si tu hésites entre ACHAT et EXCLUS sans défaut concret identifié, dis ACHAT
avec risque MEDIUM et un SL rigoureux ; si tu as identifié un défaut de la
liste, dis EXCLUS et cite-le précisément.
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


def _analyze_chart(ticker: str, ai) -> str:
    """Génère un graphique chandeliers et l'envoie au modèle vision. Retourne l'analyse en texte."""
    try:
        image_bytes = prices.get_chart_image(ticker)
        if not image_bytes:
            return ""
        prompt = (
            "Analyse ce graphique en chandeliers japonais (3 mois, MM20 bleue, MM50 orange, volume).\n"
            "Réponds en 4 lignes MAX, texte brut, sans markdown :\n"
            "- Tendance : haussière / baissière / range (court terme 1-2 semaines et moyen terme 1-3 mois)\n"
            "- Patterns de chandeliers récents significatifs (si aucun : sans pattern clair)\n"
            "- Niveaux clés visibles : support principal et résistance principale en prix\n"
            "- Signal technique global : POSITIF / NEUTRE / NÉGATIF — justification en 5 mots"
        )
        result = ai.complete_with_image(prompt, image_bytes)
        return result.strip()
    except Exception as e:
        print(f"[chart vision] {ticker}: {e}")
        return ""


def _macro_context() -> str:
    """Charge l'analyse macro sectorielle si macro_analysis.md existe (document daté, mis à jour par l'utilisateur)."""
    try:
        if MACRO_ANALYSIS_PATH.exists():
            content = MACRO_ANALYSIS_PATH.read_text(encoding="utf-8")
            mtime = datetime.fromtimestamp(MACRO_ANALYSIS_PATH.stat().st_mtime).strftime("%d/%m/%Y")
            return (
                f"\n--- ANALYSE MACRO SECTORIELLE (rédigée/mise à jour le {mtime}) ---\n"
                f"{content}\n"
                f"--- FIN ANALYSE MACRO (point dans le temps — peut être obsolète) ---\n"
            )
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
    # Les HOLD long terme sortent du périmètre de gestion : listés à part avec
    # interdiction explicite pour l'IA de proposer vente, swap ou protection.
    holds = {k: v for k, v in positions.items() if v.get("hold")}
    positions = {k: v for k, v in positions.items() if not v.get("hold")}
    for name, cfg in positions.items():
        quote = prices.get_quote(cfg["ticker"])
        price = quote.get("price")
        if price and not math.isnan(price):
            chg  = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
            pnl  = (price - cfg["entry_price"]) * cfg["qty"]
            sym  = prices.currency_symbol(quote.get("currency", "EUR"))
            cur_tag = (" | ⚠️ perf aberrante, PRU probablement dans la mauvaise devise — ignorer ce P&L"
                       if quote.get("currency", "EUR") != "EUR" and abs(chg) > 80 else "")
            lines.append(
                f"  {name} ({cfg['ticker']}): {sym}{price} ({chg:+.2f}%) | "
                f"PRU {sym}{cfg['entry_price']} | {cfg['qty']}t | P&L {sym}{pnl:+.0f} | "
                f"SL {sym}{cfg['target_low']} | TP {sym}{cfg['target_high']}{cur_tag}"
            )
        elif quote.get("status") in ("suspended", "error"):
            # Sans suffixe de place (.PA, .DE…), c'est plus probablement un
            # ticker invalide qu'une vraie suspension (ex: LVMH au lieu de MC.PA)
            if "." not in cfg["ticker"]:
                lines.append(
                    f"  {name} ({cfg['ticker']}): ❓ TICKER INTROUVABLE sur Yahoo — "
                    f"format à vérifier (ex: LVMH → MC.PA) | PRU {cfg['entry_price']}€ | {cfg['qty']}t"
                )
            else:
                lines.append(
                    f"  {name} ({cfg['ticker']}): ⛔ COURS SUSPENDU — non vendable (liquidation judiciaire ?) | "
                    f"PRU {cfg['entry_price']}€ | {cfg['qty']}t"
                )
        else:
            lines.append(f"  {name}: prix indisponible | PRU {cfg['entry_price']}€ | {cfg['qty']}t")

    if holds:
        lines.append("🔒 HOLD LONG TERME — HORS GESTION (ne JAMAIS proposer de vente, "
                     "swap, SL/TP ou analyse pour ces titres) :")
        for name, cfg in holds.items():
            lines.append(f"  {name} ({cfg['ticker']}): {cfg['qty']}t | "
                         f"{cfg.get('hold_note', 'hold long terme')}")

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


def _parse_verdict(val: str) -> tuple[str, str]:
    """
    Source UNIQUE de lecture d'un verdict IA (ACHAT / EXCLUS).
    L'IA peut écrire un en-tête société avant de dire EXCLU : on cherche sur les
    premières lignes, pas seulement en début de texte. Retourne (verdict, raison).
    Utilisé par TOUS les chemins de décision — garantit un jugement cohérent.
    """
    lines = val.strip().splitlines()
    head = "\n".join(lines[:5]).upper()
    if "EXCLU" in head or "ÉVITER" in head or "EVITER" in head:
        reason = "écarté"
        for line in lines:
            u = line.upper()
            if "EXCLU" in u or "ÉVITER" in u or "EVITER" in u:
                reason = line.split("—", 1)[1].strip()[:70] if "—" in line else line.strip()[:70]
                break
        return "EXCLUS", reason
    return "ACHAT", ""


def _regime_instructions(regime: str, regime_summary: str,
                         rel: float, index_mom: float) -> str:
    """Bloc d'instructions spécifique au régime — inchangé, factorisé ici pour
    être partagé par le scan et le briefing."""
    if regime == "CORRECTION":
        return f"""
RÉGIME : CORRECTION ({regime_summary})
Ce titre est sélectionné pour sa force relative ({rel:+.1f}% vs indice à {index_mom:+.1f}%).

MISSION CORRECTION — critères ACHAT valides dans ce contexte :
1. FORCE RELATIVE : l'action résiste ou monte pendant que l'indice baisse → thèse valide.
2. BÉNÉFICIAIRE MACRO : la cause probable de la correction (BCE hawkish → banques ;
   tensions géo → défense/énergie ; récession → pharma/utilities/consommation de base ;
   correction tech → value/industrielles) bénéficie directement à ce secteur.
3. REBOND TECHNIQUE QUALITÉ : RSI < 35, titre de qualité, tendance LT intacte,
   catalyseur de rebond identifiable.

Signal EXCLUS si : momentum positif MAIS corrélé à l'indice (force relative nulle),
ou si secteur cyclique sans thèse macro claire en contexte de correction."""
    if regime == "NEUTRAL":
        return f"""
RÉGIME : NEUTRE ({regime_summary})
Marché sans tendance d'indice claire. Les titres en momentum 12 mois propre
au-dessus de leur MM200 restent tradeables, mais EXIGE une force relative
positive vs l'indice (le filtre quantitatif l'a déjà vérifiée — confirme
qu'aucune news ne l'explique par un facteur non répétable). Gestion du SL serrée."""
    return f"""
RÉGIME : HAUSSIER ({regime_summary})
Conditions favorables. Scan momentum standard (12 mois hors dernier mois,
entrée sur repli sain)."""


def validate_candidate(ticker: str, *, mode: str = "standard",
                       regime: str = "BULL", regime_summary: str = "",
                       index_mom: float = 0.0, item: dict | None = None,
                       cash: float = 0.0, ai=None) -> dict:
    """
    SOURCE DE DÉCISION UNIQUE — tous les chemins (scan, briefing, gain réduit,
    gate pré-achat autonome) appellent cette fonction. Elle rassemble les
    données, construit le prompt canonique (règles + leçons + directive selon
    le mode) et parse le verdict de façon cohérente.

    La stratégie d'analyse IA est IDENTIQUE à celle du scan actuel — seul le
    `mode` fait varier l'objectif de TP et le cadrage :
      - "standard"    : TP ≥ +{_TP}% (briefing, scan)
      - "confirm"     : dernier contrôle pré-achat (défaut disqualifiant only)
      - "gain_reduit" : TP +{FALLBACK_TP_MIN_PCT:.0f} à +{FALLBACK_TP_MAX_PCT:.0f}%, SL ≤ TP

    Retourne un dict : verdict, entry, sl, tp, tp_pct, risk, reason, raw,
    context (pour la boucle d'apprentissage), + infos société/devise.
    """
    ai = ai or get_provider()
    ctx = _trading_context()
    today_str = datetime.now(PARIS).strftime("%d/%m/%Y")
    item = item or {}

    q = prices.get_quote(ticker)
    price = q.get("price")
    out = {"ticker": ticker, "verdict": "EXCLUS", "reason": "cours indisponible",
           "price": price, "raw": ""}
    if not price:
        return out
    cur = q.get("currency") or "EUR"
    sym = prices.currency_symbol(cur)
    fx  = prices.fx_to_eur(cur)

    tech   = prices.get_technicals(ticker) or {}
    funds  = prices.get_fundamentals(ticker) or {}
    pctx   = prices.get_price_context(ticker) or {}
    yf_news = prices.get_yf_news(ticker, max_items=4)
    web    = research.research_stock(ticker)
    cats   = research.search_catalysts(ticker)
    social = research.get_social_sentiment(ticker)

    company_name = funds.get("name", ticker)
    company_sector = funds.get("sector", "")
    company_label = f"{company_name} ({ticker})" + (f" — {company_sector}" if company_sector else "")

    # Blocs de données — format identique au scan actuel
    pctx_block = ""
    if pctx:
        pctx_block = (f"\nCONTEXTE 52 SEMAINES\n"
                      f"- Performance 1 an : {pctx['perf_1y']:+}%"
                      + (f" | 3 mois : {pctx['perf_3m']:+}%" if "perf_3m" in pctx else "") + "\n"
                      f"- Cours actuel : +{pctx['from_52w_low']}% au-dessus du plus bas 52s, "
                      f"{pctx['from_52w_high']}% vs plus haut 52s\n")
    tech_block = ""
    if tech:
        rel = item.get("rel_strength")
        rel_line = (f"- Force relative vs indice : {rel:+.1f}% (indice : {index_mom:+.1f}%)\n"
                    if rel is not None else "")
        score_line = f"- Score quant : {item['score']:+.1f}\n" if item.get("score") is not None else ""
        m121 = tech.get("mom_12_1")
        m121_line = (f"- Momentum 12 mois (hors dernier mois) : {m121:+}% — signal de formation\n"
                     if m121 is not None else "")
        ma_dist = tech.get("ma200_dist_pct")
        ma_line = (f"- Cours vs MM200 : {ma_dist:+}% ({'AU-DESSUS' if tech.get('above_ma200') else 'SOUS la MM200 ⚠️'})\n"
                   if ma_dist is not None else "")
        atr = tech.get("atr_pct")
        atr_line = (f"- ATR 14j : {atr}% du cours → SL technique ≈ -{min(max(ATR_SL_MULT * atr, MIN_SL_PCT), MAX_SL_PCT):.1f}%\n"
                    if atr else "")
        tech_block = (f"\nINDICATEURS TECHNIQUES\n"
                      f"- RSI 14j : {tech.get('rsi', 'N/A')}\n"
                      f"- Momentum 1 mois : {tech.get('momentum_1m', 'N/A'):+}%\n"
                      f"{m121_line}{ma_line}{atr_line}"
                      f"- Volume ratio : {tech.get('vol_ratio', 'N/A')}x moyenne 20j\n"
                      f"{rel_line}{score_line}")
    funds_lines = []
    if funds.get("analyst_target"):
        funds_lines.append(f"- Objectif analyste : {funds['analyst_target']}")
    if funds.get("next_earnings"):
        funds_lines.append(f"- Prochains résultats : {funds['next_earnings']}")
    if "analyst_buy" in funds:
        funds_lines.append(f"- Consensus : {funds['analyst_buy']} Achat / "
                           f"{funds['analyst_hold']} Neutre / {funds['analyst_sell']} Vente")
    funds_block = ("\nFONDAMENTAUX\n" + "\n".join(funds_lines)) if funds_lines else ""
    news_lines = [f"- {n['title']} ({n['publisher']})" for n in yf_news]
    news_block = ("\nACTUALITÉS\n" + "\n".join(news_lines)) if news_lines else ""
    social_block = f"\nSENTIMENT SOCIAL\n{social}" if social and "aucune donnée" not in social else ""
    chart_txt = _analyze_chart(ticker, ai)
    chart_block = f"\nANALYSE GRAPHIQUE (vision IA)\n{chart_txt}\n" if chart_txt else ""
    ctx_v = (f"\nCONTEXTE PERSONNEL — règles et contraintes à respecter "
             f"IMPÉRATIVEMENT (toute violation → EXCLUS) :\n{ctx}\n") if ctx else ""

    data_blocks = f"{pctx_block}{tech_block}{funds_block}{news_block}{social_block}{chart_block}"

    # ── Prompt selon le mode — la stratégie d'analyse reste celle du scan ────
    if mode == "gain_reduit":
        directive = f"""⚡ MODE GAIN RÉDUIT — TRADE COURT TERME (1 à 5 jours)
Aucune opportunité à +{_TP}% n'a passé la validation aujourd'hui. Ta mission :
un trade COURT à objectif RÉDUIT mais très atteignable, plutôt que rien.
Ce candidat est dans le top du filtre quantitatif momentum du jour.
RÈGLES DU TRADE COURT :
- TP : +{FALLBACK_TP_MIN_PCT:.0f}% à +{FALLBACK_TP_MAX_PCT:.0f}% — cale-le SOUS la première résistance.
  Ici une résistance proche est une CIBLE à exploiter, PAS un motif d'exclusion.
- SL : serré, sous le dernier support — en %, jamais plus de la moitié du TP visé.
- Momentum sain exigé : tendance 1 mois positive, RSI < {RSI_HARD_MAX:.0f}, pas de couteau qui tombe.
- EXCLUS si résultats ou événement binaire dans les 5 prochains jours."""
        tp_line = (f"{company_name} ({ticker}) — Entrée : {price}{sym}  "
                   f"SL : X{sym} (-X%)  TP : X{sym} (+X%)")
        rules_head = f"{ANALYSIS_RULES}\n{_lessons_block()}{TICKER_RULES}"
    elif mode == "confirm":
        directive = f"""{SCREEN_DIRECTIVE}
⚡ MODE CONFIRMATION PRÉ-ACHAT :
Ce titre a été validé ACHAT aujourd'hui par l'analyse complète. Ton rôle est un
DERNIER contrôle avant l'ordre réel : vérifie qu'aucun défaut disqualifiant de
la liste ci-dessus n'est apparu ou n'a été manqué (news invalidante, OPA
plafonnée, événement binaire imminent, illiquidité, structure cassée, RSI
repassé > {RSI_HARD_MAX:.0f}). Ne re-juge pas l'attractivité générale de
l'opportunité — mais si un défaut CONCRET est présent, EXCLUS sans hésiter :
mieux vaut un trade raté qu'une perte évitable."""
        tp_line = (f"{company_name} ({ticker}){(' — ' + company_sector) if company_sector else ''}\n"
                   f"- Entrée : {price}{sym}  SL : X{sym} (-{_SL}%)  "
                   f"TP : X{sym} (+X% — minimum +{_TP}%)")
        rules_head = f"{ANALYSIS_RULES}\n{_lessons_block()}{directive}\n{TICKER_RULES}"
        directive = ""  # déjà inclus dans rules_head
    else:  # standard
        rel = item.get("rel_strength", 0.0)
        directive = _regime_instructions(regime, regime_summary or regime, rel, index_mom)
        tp_line = (f"{company_name} ({ticker}){(' — ' + company_sector) if company_sector else ''}\n"
                   f"- Cours actuel : {price}{sym} | Entrée : X  SL : X (-{_SL}%)  "
                   f"TP : X (+X% — minimum +{_TP}%, plus si le potentiel le justifie)")
        rules_head = f"{ANALYSIS_RULES}\n{_lessons_block()}{SCREEN_DIRECTIVE}\n{TICKER_RULES}"

    prompt = f"""{TRADER_SYSTEM}
{rules_head}
{FORMAT_TELEGRAM}
{ctx_v}
AUJOURD'HUI : {today_str}
{directive}
SOCIÉTÉ ANALYSÉE : {company_label} — JE NE DÉTIENS PAS. CASH DISPONIBLE : {cash}€.
Cours actuel : {price}{sym} (devise {cur})
{data_blocks}
RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{cats}

Signal ACHAT ou EXCLUS ?
RÈGLE : si le titre ne répond pas aux critères → EXCLUS — [raison 5 mots]
RÈGLE : si le ticker viole une contrainte du contexte personnel → EXCLUS — [raison]
Si ACHAT : format exact (symbole {sym}, le titre cote en {cur}) :
{tp_line}
- Société : [1 phrase]
- Secteur maintenant : [1 phrase — pourquoi porteur EN CE MOMENT]
- Thèse : [CATALYSEUR daté] OU [FORCE RELATIVE] OU [MOMENTUM + niveau invalidation]
- Raison : 1 phrase
- Risque : LOW / MEDIUM / HIGH"""

    val = _strip_markdown(ai.complete(prompt, max_tokens=400))
    verdict, reason = _parse_verdict(val)

    entry_m = re.search(r"Entr[ée]e?\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
    sl_m    = re.search(r"\bSL\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
    tp_m    = re.search(r"\bTP\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
    entry = float(entry_m.group(1).replace(",", ".")) if entry_m else price
    sl_v  = float(sl_m.group(1).replace(",", ".")) if sl_m else None
    tp_v  = float(tp_m.group(1).replace(",", ".")) if tp_m else None
    risk_m = re.search(r"Risque\s*:?\s*(LOW|MEDIUM|HIGH)", val, re.I)

    # Garde-fou commun : une opportunité valide DOIT avoir entrée+SL+TP cohérents
    if verdict == "ACHAT" and not (sl_v and tp_v and sl_v < entry < tp_v):
        verdict, reason = "EXCLUS", reason or "niveaux entrée/SL/TP incohérents"

    # ── Garde-fous QUANTITATIFS — indépendants du verdict IA ─────────────────
    # La recherche est sans ambiguïté sur ces points ; aucun prompt ne doit
    # pouvoir les contourner (les achats en surchauffe de 06-07/2026 venaient
    # d'une directive qui écrasait la prudence de l'IA).
    if verdict == "ACHAT":
        rsi_now  = tech.get("rsi")
        above_ma = tech.get("above_ma200")
        atr      = tech.get("atr_pct")
        if rsi_now is not None and rsi_now > RSI_HARD_MAX:
            # Réversion court terme (Jegadeesh 1990) : pas d'entrée en surchauffe
            verdict, reason = "EXCLUS", (f"surchauffe court terme (RSI {rsi_now} > "
                                         f"{RSI_HARD_MAX:.0f}) — attendre le repli")
        elif above_ma is False:
            verdict, reason = "EXCLUS", "cours sous la MM200 — tendance long terme non confirmée"
        elif atr and ATR_SL_MULT * atr > MAX_SL_PCT:
            verdict, reason = "EXCLUS", (f"trop volatil ({ATR_SL_MULT:.0f}×ATR = "
                                         f"{ATR_SL_MULT * atr:.1f}% > SL max {MAX_SL_PCT:.0f}%)")
    if verdict == "ACHAT" and sl_v and tp_v:
        sl_pct = (entry - sl_v) / entry * 100
        # SL dans le bruit du titre → élargi à ATR_SL_MULT×ATR (borné) : un stop
        # plus serré que la volatilité normale se fait toucher sans signal.
        atr = tech.get("atr_pct")
        if atr:
            tech_sl = min(max(ATR_SL_MULT * atr, MIN_SL_PCT), MAX_SL_PCT)
            if sl_pct < tech_sl * 0.75:
                sl_v   = round(entry * (1 - tech_sl / 100), 4)
                sl_pct = tech_sl
                val   += (f"\n(SL élargi à -{tech_sl:.1f}% = {ATR_SL_MULT:.0f}×ATR — "
                          f"le SL proposé était dans le bruit du titre)")
        if sl_pct > MAX_SL_PCT:
            verdict, reason = "EXCLUS", f"SL requis -{sl_pct:.1f}% > max {MAX_SL_PCT:.0f}% — trop volatil"
        else:
            rr = ((tp_v - entry) / entry * 100) / sl_pct if sl_pct else 0
            if rr < MIN_RR:
                verdict, reason = "EXCLUS", (f"ratio risque/rendement {rr:.1f} < {MIN_RR:.1f} "
                                             f"(TP +{(tp_v - entry) / entry * 100:.1f}% vs SL -{sl_pct:.1f}%)")

    # Cohérence décision ↔ ordres réels : si un ordre d'entrée AUTONOME est
    # encore en attente sur BD pour ce titre et que la décision du jour est
    # EXCLUS, la thèse qui a motivé l'ordre est contredite → annulation
    # immédiate (sinon l'ordre attend une exécution par cassure baissière —
    # cas AF.PA 07/2026).
    if verdict == "EXCLUS":
        try:
            import autonomous_engine
            autonomous_engine.cancel_auto_order_if_rejected(ticker, reason or "EXCLUS")
        except Exception as _ce:
            print(f"[validate] cancel auto order {ticker}: {_ce}")

    out.update({
        "verdict": verdict, "reason": reason, "raw": _validate_tickers(val),
        "entry": round(entry, 4), "sl": sl_v, "tp": tp_v,
        "tp_pct": round((tp_v / entry - 1) * 100, 1) if (tp_v and entry) else None,
        "risk": risk_m.group(1).upper() if risk_m else "MEDIUM",
        "currency": cur, "sym": sym, "fx": fx,
        "company_name": company_name, "company_sector": company_sector,
        "company_label": company_label, "tech": tech, "pctx": pctx, "funds": funds,
        "context": _entry_ctx(tech, pctx, val.splitlines()[0] if val else "", mode, regime),
    })
    return out


def _small_gain_pass(ai, candidates: list[dict], cash: float,
                     ctx: str, today_str: str) -> tuple[list[str], list[str]]:
    """
    Passe GAIN RÉDUIT : appelée quand AUCUNE opportunité à +DEFAULT_TP_PCT% n'a
    été validée. Re-teste les meilleurs candidats quant avec un objectif court
    terme réduit (+FALLBACK_TP_MIN_PCT à +FALLBACK_TP_MAX_PCT%, 1-5 jours).
    Philosophie : mieux vaut un petit gain net que rien — tant que les frais
    restent une part faible du gain (MIN_NET_GAIN_FEE_RATIO).
    Les opportunités validées sont stockées en pending (source="court_terme")
    pour le moteur autonome. Retourne (opportunités, écartés).
    """
    opps, rejected = [], []
    roundtrip = 2 * BROKERAGE_FEE

    for c in candidates[:3]:
        t = c["ticker"]
        try:
            # SOURCE DE DÉCISION UNIQUE — même moteur que le scan, mode gain_reduit
            res = validate_candidate(t, mode="gain_reduit", cash=cash, ai=ai, item=c)
            company_label = res.get("company_label", t)
            if res.get("verdict") != "ACHAT":
                rejected.append(f"- {company_label} : {res.get('reason', 'écarté')}")
                continue

            price = res["price"]
            g_cur, g_sym, g_fx = res["currency"], res["sym"], res["fx"]
            tech, pctx = res.get("tech", {}), res.get("pctx", {})
            val   = res["raw"]
            entry = res["entry"]
            sl_v  = res["sl"]
            tp_v  = res["tp"]
            # Plafonne le TP dans la fourchette gain réduit
            tp_max = round(entry * (1 + FALLBACK_TP_MAX_PCT / 100), 2)
            if tp_v > tp_max:
                tp_v = tp_max

            # ── Ratio risque/gain : le SL ne doit JAMAIS risquer plus que le
            # TP ne vise (l'IA ignore souvent cette consigne — on l'impose).
            # Ex: TP +2.9% avec SL -6.9% = ratio 2.4:1 → SL resserré à -2.9%.
            tp_pct = (tp_v / entry - 1) * 100
            sl_pct = (1 - sl_v / entry) * 100
            sl_note = ""
            if sl_pct > tp_pct:
                new_sl = round(entry * (1 - tp_pct / 100), 2)
                sl_note = (f"\n⚠️ SL resserré : {sl_v}{g_sym} (-{sl_pct:.1f}%) → "
                           f"{new_sl}{g_sym} (-{tp_pct:.1f}%) — règle gain réduit : "
                           f"risque ≤ gain visé")
                sl_v = new_sl

            # ── Rentabilité nette : mêmes règles que le moteur autonome ──────
            # Budget en EUR, cours en devise du titre → conversion FX
            budget   = min(cash * POSITION_BUDGET_PCT / 100, POSITION_BUDGET_MAX)
            qty      = max(1, int(budget / (entry * g_fx)))
            gross_tp = qty * (tp_v - entry) * g_fx
            net_tp   = gross_tp - roundtrip
            if gross_tp < roundtrip * MIN_NET_GAIN_FEE_RATIO:
                rejected.append(
                    f"- {company_label} : gain net {net_tp:.0f}€ trop faible vs frais "
                    f"({MIN_NET_GAIN_FEE_RATIO:.0f}× {roundtrip:.2f}€ requis)"
                )
                continue

            val = _validate_tickers(val)
            fx_note = f" ≈ {qty * entry * g_fx:.0f}€" if g_cur != "EUR" else ""
            val += sl_note
            val += (f"\n→ {qty} titres ≈ {qty * entry:.0f}{g_sym}{fx_note} | "
                    f"Gain net au TP ≈ +{net_tp:.0f}€ (frais {roundtrip:.2f}€ inclus)")
            opps.append(val)
            portfolio.add_pending_opportunity(
                t, entry, sl_v, tp_v,
                reason=val.splitlines()[0][:150],
                source="court_terme",
                context=_entry_ctx(tech, pctx, val.splitlines()[0], "court_terme"),
            )
        except Exception as e:
            print(f"[gain réduit] {t}: {e}")

    return opps, rejected


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
2. Risque global portefeuille : LOW / MEDIUM / HIGH

Puis écris EXACTEMENT cette ligne séparatrice seule : ===CANDIDATS===
Et APRÈS cette ligne uniquement, identifie 6 à 10 tickers CANDIDATS de deux types
(tous marchés Bourse Direct) :
   A) CATALYSEUR : événement futur daté après le {today_str} (résultats, OPA, FDA, contrat).
   B) MOMENTUM : tendance haussière sur 3+ mois rendant +{_TP}% atteignable.
   Propose LARGEMENT — ne pré-filtre pas, la validation technique (RSI, couteau qui tombe,
   tendance) sera faite ensuite avec les données réelles. Seules exclusions à ce stade :
   les secteurs ou valeurs explicitement bannis dans le contexte personnel.
   Après ===CANDIDATS===, réponds UNIQUEMENT avec les tickers Yahoo Finance, un par
   ligne (ex: ALFRE.PA puis MSFT). AUCUN en-tête, AUCUN prix, AUCUNE explication, AUCUN
   numéro — juste les tickers bruts un par ligne."""
        else:
            catalysts = ""
            opps_mission = f"\n2. Risque global : LOW / MEDIUM / HIGH\n(Cash {cash}€ insuffisant pour nouvelles positions)"

        macro_ctx = _macro_context() if cash >= 1000 else ""
        prompt1 = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{macro_ctx}
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
        rejected_morning = []
        # Sépare proprement l'analyse portefeuille des tickers candidats via le
        # délimiteur ===CANDIDATS=== (évite que des en-têtes/tickers fuitent dans
        # l'analyse affichée).
        if "===CANDIDATS===" in pass1:
            pass1_analysis, _, pass1_candidates = pass1.partition("===CANDIDATS===")
        else:
            pass1_analysis, pass1_candidates = pass1, pass1

        if cash >= 1000:
            held_tickers = {cfg["ticker"].upper()
                            for cfg in portfolio.load().get("positions", {}).values()}
            ai_tickers = _extract_tickers(pass1_candidates)

            # Amorce avec les meilleurs candidats RÉELS du filtre quantitatif
            # (liquides, en tendance) — évite de dépendre de l'imagination de l'IA
            # et garantit des candidats momentum chaque matin.
            quant_tickers = []
            quant = []
            try:
                regime_data = prices.get_market_regime()
                quant = _quant_screen(
                    SCAN_UNIVERSE, held_tickers,
                    regime_data["label"], regime_data.get("index_mom_avg", 0.0) or 0.0,
                )
                quant_tickers = [c["ticker"] for c in quant[:6]]
                print(f"[briefing] quant screen ({regime_data['label']}): "
                      f"{len(quant)} candidats, top6 {quant_tickers}")
            except Exception as _qe:
                print(f"[briefing] quant screen error: {_qe}")

            # Quant d'abord (fiable), puis suggestions IA (catalyseurs), dédupliqué
            raw_tickers = list(dict.fromkeys(quant_tickers + ai_tickers))

            for t in raw_tickers[:10]:
                if t.upper() in held_tickers:
                    continue
                # SOURCE DE DÉCISION UNIQUE — même moteur que scan/gate, mode standard
                _reg = (regime_data or {}).get("label", "BULL")
                _idx = (regime_data or {}).get("index_mom_avg", 0.0) or 0.0
                res = validate_candidate(t, mode="standard", regime=_reg,
                                         regime_summary=_reg, index_mom=_idx,
                                         cash=cash, ai=ai)
                current_price = res.get("price")
                if not current_price:
                    continue
                company_name = res.get("company_name", t)
                if res.get("verdict") != "ACHAT":
                    label = f"{company_name} ({t})" if company_name != t else t
                    rejected_morning.append(f"- {label} : {res.get('reason', 'écarté')}")
                    continue
                val = res["raw"]
                opportunities.append(val)
                # Stocke pour le moteur autonome
                try:
                    if res.get("sl") and res.get("tp"):
                        portfolio.add_pending_opportunity(
                            t, res["entry"], res["sl"], res["tp"],
                            reason=val.splitlines()[0][:150],
                            source="briefing",
                            context=res.get("context"),
                        )
                except Exception as _pe:
                    print(f"[briefing] pending_opp store error {t}: {_pe}")

        # ── Passe 3 : GAIN RÉDUIT si rien ne passe à +TP% ────────────────────
        # DÉSACTIVÉE par défaut (SMALL_GAIN_MODE) : forcer un trade quand rien
        # ne passe est le schéma « overtrading » documenté (Barber & Odean
        # 2000) — c'est cette passe qui proposait AF.PA à résistance en 07/2026.
        # Zéro trade est un résultat acceptable.
        small_opps, small_rejected = [], []
        if SMALL_GAIN_MODE and cash >= 1000 and not opportunities and quant:
            print(f"[briefing] 0 opportunité à +{_TP}% — passe gain réduit sur "
                  f"{[c['ticker'] for c in quant[:3]]}")
            small_opps, small_rejected = _small_gain_pass(ai, quant, cash, ctx, today_str)

        # ── Assemblage final ─────────────────────────────────────────────────
        # L'analyse portefeuille = tout ce qui précède ===CANDIDATS===.
        # Filet de sécurité : retire toute ligne candidate résiduelle (ticker nu
        # ou en-tête type "CANDIDAT A — ...") au cas où le délimiteur manque.
        portfolio_analysis = "\n".join(
            l for l in pass1_analysis.splitlines()
            if not re.match(r"^\s*CANDIDAT\b", l, re.I)
            and not re.match(r"^\s*[A-Za-z]{1,8}(?:\.[A-Za-z]{1,3})?\s*$", l.strip())
        ).strip()

        date = datetime.now(PARIS).strftime("%d/%m/%Y")
        msg  = f"🌅 BRIEFING — {date}\n\n{snapshot}\n\n{portfolio_analysis}"
        if opportunities:
            msg += "\n\nOPPORTUNITÉS VALIDÉES\n" + "\n\n".join(opportunities)
            if rejected_morning:
                msg += "\n\nAnalysés et écartés :\n" + "\n".join(rejected_morning)
        elif cash >= 1000:
            no_opp = f"Aucun candidat validé à +{_TP}% aujourd'hui."
            if small_opps:
                no_opp += ("\n\n⚡ OPPORTUNITÉS COURT TERME (gain réduit, 1-5 jours)\n"
                           + "\n\n".join(small_opps))
            if rejected_morning:
                no_opp += f"\n\nAnalysés et écartés (+{_TP}%) :\n" + "\n".join(rejected_morning)
            if small_rejected:
                no_opp += "\n\nÉcartés en gain réduit :\n" + "\n".join(small_rejected)
            if not small_opps:
                no_opp += "\n\n→ /scan pour relancer | /research TICKER pour un avis ciblé."
            msg += "\n\n" + no_opp
        send_fn(msg)

        # Mode autonome : si actif + Playwright connecté + opportunités trouvées
        # → entre immédiatement après le briefing, sans attendre le check planifié
        if opportunities or small_opps:
            _trigger_autonomous(send_fn)

    except Exception as e:
        print(f"Erreur briefing: {e}")
        send_fn(f"⚠️ Erreur briefing matinal: {e}")


def monthly_breach_review(send_fn) -> None:
    """Revue mensuelle (1er du mois) des positions dont le SL est dépassé."""
    data = portfolio.load()
    positions = data.get("positions", {})
    breach = {k: v for k, v in positions.items()
              if v.get("sl_breach_notified") and not v.get("hold")}

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


def _fetch_candidate_prices(tickers: list[str]) -> str:
    """Récupère cours réel + techniques pour une liste de tickers candidats au swap."""
    lines = []
    for t in tickers:
        quote = prices.get_quote(t)
        price = quote.get("price")
        if not price:
            lines.append(f"- {t} : cours indisponible (ticker incorrect ?)")
            continue
        sym = prices.currency_symbol(quote.get("currency", "EUR"))
        chg = quote.get("change_pct", 0)
        tech = prices.get_technicals(t)
        tech_str = ""
        if tech:
            rsi = tech.get("rsi")
            mom = tech.get("momentum_1m")
            vol = tech.get("vol_ratio")
            parts = []
            if rsi is not None:
                parts.append(f"RSI {rsi}")
            if mom is not None:
                parts.append(f"mom1m {mom:+.1f}%")
            if vol is not None:
                parts.append(f"vol {vol:.2f}x")
            if parts:
                tech_str = f" | {', '.join(parts)}"
        lines.append(f"- {t} : {sym}{price} ({chg:+.2f}% J-1){tech_str}")
    return "\n".join(lines) if lines else "Aucun cours récupéré."


def weekly_swap_analysis(send_fn) -> None:
    """Analyse hebdomadaire en 2 étapes : d'abord identification des tickers candidats,
    puis fetch des cours réels avant de générer l'analyse complète avec les vrais prix."""
    print(f"[{datetime.now(PARIS).strftime('%Y-%m-%d %H:%M:%S')}] Analyse swap hebdo...")
    try:
        ai = get_provider()
        snapshot = _portfolio_snapshot()
        macro = research.market_context()
        cash = portfolio.get_cash()

        ctx = _trading_context()
        ctx_block = f"\n--- CONTEXTE PERSONNEL ---\n{ctx}\n" if ctx else ""

        # ÉTAPE 1 — identifier les tickers candidats (sans prix, sans SL/TP)
        step1_prompt = f"""{TRADER_SYSTEM}
{ctx_block}
PORTEFEUILLE
{snapshot}

CONTEXTE MARCHÉ
{macro}

MISSION ÉTAPE 1 — IDENTIFICATION UNIQUEMENT
Cash disponible : {cash}€

Un swap n'est PAS obligatoire. Ne swappe QUE si une position remplit un de ces critères :
- thèse clairement invalidée (cassure de support, catalyseur raté, news négative dure), OU
- momentum durablement négatif SANS potentiel restant vers le TP, OU
- SL réellement menacé ET pas de catalyseur proche pour rebondir.

NE PAS swapper une position qui :
- conserve un potentiel raisonnable vers son TP (objectif analyste au-dessus du cours), OU
- a un catalyseur daté proche (résultats, etc.), OU
- est trop petite pour que la rotation vaille les frais (valeur < ~600€ → friction > gain marginal).

Si AUCUNE position ne mérite un swap, réponds EXACTEMENT :
NE_RIEN_FAIRE: [raison en 1 ligne]

Sinon, réponds UNIQUEMENT dans ce format, sans analyse ni commentaire :
VENDRE: TICKER_A
ACHETER: TICKER_B
ACHETER: TICKER_C (optionnel)
RAISON_VENTE: [1 ligne — pourquoi la thèse est cassée, pas juste "momentum faible"]
RAISON_ACHAT_B: [1 ligne]"""

        step1 = ai.complete(step1_prompt, max_tokens=250).strip()
        print(f"[swap step1] {step1}")

        date = datetime.now(PARIS).strftime("%d/%m/%Y")

        # Décision explicite de ne rien faire → on respecte et on s'arrête là
        if "NE_RIEN_FAIRE" in step1.upper():
            reason = step1.split(":", 1)[1].strip() if ":" in step1 else "aucune rotation justifiée"
            send_fn(
                f"🔄 ANALYSE SWAP — {date}\n\n"
                f"✋ AUCUN SWAP CETTE SEMAINE\n{reason}\n\n"
                f"Les positions actuelles gardent leur potentiel — la rotation "
                f"générerait des frais sans gain net. On conserve."
            )
            return

        # Extraction des tickers candidats à l'achat
        buy_tickers = re.findall(r'ACHETER\s*:\s*([A-Z][A-Z0-9]{0,7}(?:\.[A-Z]{1,3})?)', step1)
        if not buy_tickers:
            # Fallback : pas de candidat identifié
            send_fn(f"🔄 ANALYSE SWAP — {date}\n\n{step1}\n\n⚠️ Aucun ticker candidat extrait — relance manuelle si besoin.")
            return

        # ÉTAPE 2 — fetch cours réels pour les candidats
        candidates_data = _fetch_candidate_prices(buy_tickers)
        print(f"[swap step2 prices] {candidates_data}")

        # ÉTAPE 3 — analyse complète avec cours réels injectés
        step2_prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{ctx_block}
PORTEFEUILLE
{snapshot}

CONTEXTE MARCHÉ
{macro}

COURS RÉELS EN TEMPS RÉEL — CANDIDATS AU SWAP (source Yahoo Finance, prix du jour) :
{candidates_data}

MISSION — ANALYSE DE ROTATION HEBDOMADAIRE
Cash disponible : {cash}€

Pré-sélection issue de l'analyse : {step1}

Maintenant rédige l'analyse complète :
1. Position(s) à vendre — raison en 1 ligne
2. Pour chaque candidat achat (en te basant UNIQUEMENT sur les cours réels ci-dessus) :
   - Entrée : utilise le cours réel fourni ± 0.5% max (jamais un prix de mémoire)
   - SL : -{_SL}% du cours réel
   - TP : +{_TP}% minimum du cours réel (plus si catalyseur fort)
   - Ratio risque/rendement chiffré
   - Raison du choix
3. ARBITRAGE HONNÊTE — compare le candidat à la position à vendre :
   - La position à vendre a-t-elle ENCORE du potentiel vers son TP (objectif analyste,
     catalyseur proche) ? Si oui, le swap doit être nettement supérieur pour se justifier.
   - Friction : combien de titres, quelle valeur ? Un swap sur < ~600€ ne vaut souvent
     pas les frais — dis-le clairement si c'est le cas.
   - Le R/R du candidat bat-il VRAIMENT celui de garder la position actuelle ?
4. CONCLUSION — sois prêt à dire NON : "swapper maintenant" OU "conserver, ne pas swapper"
   (avec la raison). Ne recommande un swap QUE si l'avantage est net et chiffré. Dans le
   doute, conserver. Cette conclusion doit être cohérente avec ce qu'un /research dirait
   sur la position concernée."""

        result = _strip_markdown(ai.complete(step2_prompt, max_tokens=800))
        send_fn(f"🔄 ANALYSE SWAP — {date}\n\n{result}")

    except Exception as e:
        print(f"Erreur weekly swap: {e}")
        send_fn(f"⚠️ Erreur analyse swap: {e}")


def _validate_tickers(text: str) -> str:
    """Extrait les tickers du texte IA et avertit pour ceux non reconnus par yfinance."""
    import yfinance as yf
    found = re.findall(r'\(([A-Z][A-Z0-9]{1,7}(?:\.[A-Z]{1,3})?)\)', text)
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


def research_ticker(send_fn, ticker: str, question: str = "",
                    min_tp_pct: float | None = None,
                    confirm_mode: bool = False) -> None:
    """
    Analyse approfondie d'un ticker. Si question fournie, répond à cette question précise.
    min_tp_pct : remplace le TP minimum par défaut (+DEFAULT_TP_PCT%) — utilisé par le
    moteur autonome pour confirmer un trade court terme à objectif réduit sans que le
    critère +10% ne le fasse rejeter à tort.
    confirm_mode : confirmation pré-achat du moteur autonome. Le titre a DÉJÀ été
    validé par l'analyse complète du scan/briefing — le research ne re-juge pas
    l'opportunité, il cherche uniquement un défaut DISQUALIFIANT (même contrat que
    SCREEN_DIRECTIVE). Sans ce mode, le research re-analysait librement et vetoait
    sur des motifs interdits (volume faible, résistance proche) → aucun trade ne
    passait jamais les deux couches.
    """
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

        # Fail-safe : ticker inconnu et non détenu → suggestions Yahoo au lieu
        # d'une analyse complète sur un symbole qui ne cote pas
        if not held and not prices.get_quote(real_ticker).get("price"):
            sugg = prices.search_ticker(ticker, max_results=3)
            if sugg:
                lines = [f"❓ {ticker} ne cote pas sur Yahoo Finance. Tu cherchais peut-être :"]
                for s in sugg:
                    lines.append(f"  • {s['symbol']} — {s['name']} ({s['exchange']})")
                lines.append(f"\nRelance : /research {sugg[0]['symbol']}")
                send_fn("\n".join(lines))
                return
            send_fn(f"❓ {ticker} introuvable sur Yahoo Finance — vérifie le format "
                    f"(.PA pour Paris, .DE pour Xetra, rien pour NYSE/NASDAQ).")
            return

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

        chart_txt   = _analyze_chart(real_ticker, ai)
        chart_block = f"\nANALYSE GRAPHIQUE (vision IA)\n{chart_txt}\n" if chart_txt else ""

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
{tech_block}{funds_block}{news_block}{social_block}{chart_block}
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
            confirm_directive = ""
            if confirm_mode:
                confirm_directive = f"""{SCREEN_DIRECTIVE}
⚡ MODE CONFIRMATION PRÉ-ACHAT — PRIME SUR TON INSTINCT DE PRUDENCE :
Ce titre a DÉJÀ été validé ACHAT aujourd'hui par l'analyse complète (filtre
quantitatif + validation IA avec web, news, graphique). Ton rôle N'EST PAS de
re-juger l'opportunité ni d'exiger un meilleur point d'entrée : c'est un
DERNIER contrôle pour détecter un défaut DISQUALIFIANT apparu depuis ou manqué
(voir liste dans la directive ci-dessus : couteau qui tombe, RSI > 80, news
invalidante, OPA plafonnée, illiquidité réelle).
« volume faible », « résistance proche », « consolidation », « attendre un
repli » ne sont PAS des défauts disqualifiants — ne les utilise JAMAIS pour
rejeter. Si aucun défaut disqualifiant concret → SIGNAL : ACHAT.
"""
            short_directive = ""
            if min_tp_pct:
                short_directive = f"""
⚡ DIRECTIVE PRIORITAIRE — TRADE COURT TERME (GAIN RÉDUIT) — PRIME SUR TOUT :
Ce trade vise +{min_tp_pct}% en 1 à 5 jours. La règle « TP minimum +{_TP}% » est
SUSPENDUE pour cette analyse — y compris si le contexte personnel ou les règles
ci-dessus la mentionnent. INTERDIT de reformuler la question en trade à +{_TP}%,
INTERDIT de proposer un autre TP. Juge UNIQUEMENT la probabilité que le cours
atteigne +{min_tp_pct}% AVANT de toucher le SL dans les 1-5 prochains jours
(momentum, distance à la résistance, volume). SIGNAL : ACHAT si probable,
NEUTRE ou ÉVITER sinon — sur CE trade, pas un autre.
"""
            prompt = f"""{TRADER_SYSTEM}
{ANALYSIS_RULES}
{TICKER_RULES}
{FORMAT_TELEGRAM}
{ctx_block}{confirm_directive}{short_directive}
TICKER ANALYSÉ : {ticker} — JE NE DÉTIENS PAS CETTE ACTION.
{tech_block}{funds_block}{news_block}{social_block}{chart_block}
RECHERCHE WEB
{web}

CATALYSEURS IMMINENTS
{catalysts}
{question_block}
Y a-t-il une opportunité d'entrée sur {ticker} ?
Réponds directement à la question spécifique si elle est posée.
Format : SIGNAL (ACHAT / NEUTRE / ÉVITER), prix d'entrée, SL (-{_SL}%), TP (+{f"{min_tp_pct:.1f}" if min_tp_pct else _TP}% minimum — plus haut si le potentiel le justifie, % exact obligatoire), catalyseur principal, risque (LOW/MEDIUM/HIGH).
Si NEUTRE ou ÉVITER : explique pourquoi en 2 lignes max."""

        result = _strip_markdown(ai.complete(prompt, max_tokens=600))
        if not held:
            result = _validate_tickers(result)
        label = f"{held_name} ({real_ticker})" if held else real_ticker
        send_fn(f"🔍 ANALYSE {label}\n\n{result}")

    except Exception as e:
        send_fn(f"Erreur analyse {ticker}: {e}")


def _quant_screen(universe: list[str], held_tickers: set[str],
                  regime: str = "BULL", index_mom: float = 0.0) -> list[dict]:
    """
    Filtre quantitatif parallèle sur tout l'univers de scan.

    STRATÉGIE VALIDÉE PAR LA RECHERCHE (Phase 1, 07/2026) — on classe par
    momentum 12 mois HORS dernier mois (Jegadeesh & Titman 1993), PLUS JAMAIS
    par momentum 1 mois : à cet horizon les gagnants s'inversent (Jegadeesh
    1990, Lehmann 1990) — c'est ce qui a produit les achats de sommets
    (RSI 65-75, +11-18% sur le mois) et la série de pertes de 06-07/2026.

    Filtres communs BULL/NEUTRAL :
      - cours > MM200 (filtre de tendance — Moskowitz-Ooi-Pedersen 2012)
      - mom_12_1 > 0 (formation momentum positive)
      - RSI dans [RSI_ENTRY_MIN, RSI_ENTRY_MAX] : on achète le PULLBACK dans
        la tendance, pas la surchauffe
      - momentum 1 mois > -12% (le repli est OK, l'effondrement non)

    BULL       : score = mom_12_1 (plafonné à 80 pour écarter les loteries).
    NEUTRAL    : idem + force relative > 0 exigée. Score = 0.5×mom_12_1 + 0.5×rel.
    CORRECTION : défensif — force_relative > 0, RSI 28-70, cours > MM200.
                 Score = rel. Fallback si 0 candidats : force_relative > -3%.
    CRISIS     : retourne [] immédiatement, aucun trade.
    """
    from config import RSI_ENTRY_MIN, RSI_ENTRY_MAX

    if regime == "CRISIS":
        return []

    candidates = [t for t in universe if t.upper() not in held_tickers]

    def fetch_one(ticker):
        tech = prices.get_technicals(ticker)
        if not tech:
            return None
        rsi  = tech.get("rsi")
        mom  = tech.get("momentum_1m")
        vol  = tech.get("vol_ratio") or 1.0
        m121 = tech.get("mom_12_1")
        above_ma = tech.get("above_ma200")
        if rsi is None or mom is None:
            return None

        rel = round(mom - index_mom, 1)  # force relative vs indice
        base = {"ticker": ticker, "rsi": rsi, "mom_1m": mom, "mom_12_1": m121,
                "vol_ratio": vol, "rel_strength": rel,
                "atr_pct": tech.get("atr_pct"),
                "above_ma200": above_ma,
                "vol_ratio_20_250": tech.get("vol_ratio_20_250")}

        if regime == "CORRECTION":
            if rsi > 70 or rsi < 28 or above_ma is False:
                return None
            base["score"] = round(rel * (1 + vol * 0.2), 2)
            # Premier passage : rel > 0 (mieux que l'indice)
            return base

        # BULL / NEUTRAL — mêmes fondations, exigence de force relative en NEUTRAL
        if above_ma is not True:          # MM200 inconnue (IPO) = pas de thèse tendance
            return None
        if m121 is None or m121 <= 0:
            return None
        if not (RSI_ENTRY_MIN <= rsi <= RSI_ENTRY_MAX):
            return None
        if mom < -12:
            return None
        if regime == "NEUTRAL":
            if rel <= 0:
                return None
            base["score"] = round(0.5 * min(m121, 80) + 0.5 * rel, 2)
        else:  # BULL
            base["score"] = round(min(m121, 80), 2)
        return base

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_one, t): t for t in candidates}
        for future in as_completed(future_to_ticker, timeout=120):
            try:
                r = future.result(timeout=20)
                if r:
                    results.append(r)
            except Exception as e:
                print(f"[quant_screen] {future_to_ticker.get(future, '?')}: {e}")

    all_results = sorted(results, key=lambda x: x["score"], reverse=True)

    # En CORRECTION : filtre rel > 0, fallback rel > -3% si vide
    if regime == "CORRECTION":
        strong = [r for r in all_results if r["rel_strength"] > 0]
        if strong:
            return strong
        fallback = [r for r in all_results if r["rel_strength"] > -3]
        print(f"[quant_screen] CORRECTION fallback rel>-3% : {len(fallback)} candidats")
        return fallback

    return all_results


def _extract_tickers(text: str) -> list[str]:
    """Extrait les tickers format Yahoo Finance d'un texte (ex: GET.PA, MSFT, BP.L)."""
    return list(dict.fromkeys(re.findall(
        r'\b([A-Z]{2,8}(?:\.[A-Z]{1,3})?)\b', text
    )))


def scan_opportunities(send_fn, ticker: str = None, progress_fn=None, update_fn=None) -> None:
    """
    Scanner pro en 3 étapes :
    - Étape 0 : filtre quantitatif parallèle sur ~100 actions réelles (RSI/momentum/volume)
    - Étape 1 : analyse IA des positions + context marché
    - Étape 2 : validation IA complète des top 8 candidats filtrés

    update_fn(text) — édite un message de progression en place (optionnel).
    Si fourni, tous les messages intermédiaires + ticker passent par update_fn.
    Le send_fn est réservé à l'envoi du résultat final.
    """
    # Compat : progress_fn ancienne API → update_fn
    if progress_fn and not update_fn:
        _pf = progress_fn
        update_fn = lambda text: _pf(text.split("Analyse ")[-1].split("...")[0] if "Analyse " in text else text, 0, 0)
    _upd = update_fn if update_fn else send_fn

    # Recherche ciblée sur un ticker : pas de verrou (rapide, pas de conflit)
    if ticker:
        return research_ticker(send_fn, ticker)

    # Verrou : refuse un 2e scan tant qu'un scan complet tourne déjà
    if not _scan_lock.acquire(blocking=False):
        send_fn("⏳ Un scan est déjà en cours — patiente quelques instants, "
                "le résultat arrive. (Inutile de relancer /scan.)")
        return

    t_start = datetime.now(PARIS)
    try:
        ai = get_provider()
        cash = portfolio.get_cash()
        snapshot = _portfolio_snapshot()
        ctx = _trading_context()
        today_str = datetime.now(PARIS).strftime("%d/%m/%Y")

        held_tickers = {cfg["ticker"].upper() for cfg in portfolio.load().get("positions", {}).values()}

        # ── Détection du régime de marché ────────────────────────────────────
        regime_data  = prices.get_market_regime()
        regime       = regime_data["label"]
        index_mom    = regime_data.get("index_mom_avg", 0.0) or 0.0
        regime_summary = regime_data.get("summary", f"RÉGIME {regime}")

        REGIME_EMOJI = {"BULL": "🟢", "NEUTRAL": "🟡", "CORRECTION": "🔴", "CRISIS": "⛔"}
        emoji = REGIME_EMOJI.get(regime, "🟡")

        REGIME_SCAN_MODE = {
            "BULL":       "momentum standard",
            "NEUTRAL":    "momentum + qualité défensive",
            "CORRECTION": "force relative + bénéficiaires macro",
            "CRISIS":     "suspendu — préservation du capital",
        }
        scan_mode = REGIME_SCAN_MODE.get(regime, "standard")

        _upd(
            f"{emoji} {regime_summary}\n"
            f"Mode scan : {scan_mode}\n\n"
            f"Analyse quantitative de {len(SCAN_UNIVERSE)} actions... (~30 sec)"
        )

        # ── Étape 0 : filtre quantitatif parallèle ───────────────────────────
        screened = _quant_screen(SCAN_UNIVERSE, held_tickers, regime=regime, index_mom=index_mom)
        print(f"[scan] régime={regime} | {len(screened)}/{len(SCAN_UNIVERSE)} candidats")

        # En CRISIS : analyse positions uniquement, aucun nouveau trade
        if regime == "CRISIS":
            macro = research.market_context()
            macro_ctx = _macro_context()
            ctx_block = f"\n{ctx}\n" if ctx else ""
            portf_prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{macro_ctx}
{ctx_block}
{snapshot}

CONTEXTE MARCHÉ
{macro}

RÉGIME : CRISE (VIX > 40). Aucun nouveau trade.
TÂCHE : Pour chaque position, évalue le risque de poursuite de la baisse.
Donner : MAINTENIR / RÉDUIRE EXPOSITION / VENDRE — raison en 5 mots."""
            portf_summary = _strip_markdown(ai.complete(portf_prompt, max_tokens=300))
            send_fn(
                f"⛔ SCAN SUSPENDU — RÉGIME CRISE\n\n"
                f"Nouveau trade impossible en panique de marché (VIX > 40).\n"
                f"Priorité : préserver le capital.\n\n"
                f"POSITIONS\n{portf_summary}\n\n💰 Cash: {cash}€"
            )
            return

        if not screened:
            send_fn(
                f"{emoji} SCAN — {regime_summary}\n\n"
                f"Aucun titre ne passe les filtres ({scan_mode}).\n"
                f"→ /research TICKER pour une analyse ciblée sur un titre précis."
            )
            return

        rel_label = " · force relative vs indice" if regime == "CORRECTION" else ""
        _upd(
            f"✅ {len(screened)} titres passent les filtres.\n"
            f"Analyse IA des top {min(8, len(screened))}"
            f"{rel_label}..."
        )

        # ── Étape 1 : analyse IA des positions en portefeuille ───────────────
        macro = research.market_context()
        macro_ctx = _macro_context()
        ctx_block = f"\n{ctx}\n" if ctx else ""

        positions_news = []
        for name, cfg in portfolio.load().get("positions", {}).items():
            for n in prices.get_yf_news(cfg["ticker"], max_items=2):
                positions_news.append(f"- {name} : {n['title']}")
        news_block = ("\nNEWS POSITIONS\n" + "\n".join(positions_news)) if positions_news else ""

        portf_prompt = f"""{TRADER_SYSTEM}
{FORMAT_TELEGRAM}
{macro_ctx}
{ctx_block}
{snapshot}
{news_block}

CONTEXTE MARCHÉ
{macro}

TÂCHE : Pour chaque position en portefeuille, donne 1 ligne :
MAINTENIR / SURVEILLER / VENDRE + raison en 5 mots max."""
        portfolio_summary = _strip_markdown(ai.complete(portf_prompt, max_tokens=300))

        # ── Étape 2 : validation IA des top candidats filtrés ────────────────
        top_candidates = screened[:8]
        opportunities = []
        rejected = []
        catalysts_global = research.market_catalysts()

        for _scan_idx, item in enumerate(top_candidates):
            t = item["ticker"]
            print(f"[scan] validation {_scan_idx + 1}/{len(top_candidates)} : {t}")
            try:
                _upd(f"🔍 Analyse {t}... ({_scan_idx + 1}/{len(top_candidates)})")
            except Exception:
                pass
            # SOURCE DE DÉCISION UNIQUE — même moteur que briefing/gate, mode standard
            res = validate_candidate(t, mode="standard", regime=regime,
                                     regime_summary=regime_summary, index_mom=index_mom,
                                     item=item, cash=cash, ai=ai)
            current_price = res.get("price")
            if not current_price:
                continue
            q_cur, q_sym, q_fx = res["currency"], res["sym"], res["fx"]
            company_name = res.get("company_name", t)
            company_sector = res.get("company_sector", "")
            if res.get("verdict") != "ACHAT":
                label = f"{company_name} ({t})" if company_name != t else t
                rejected.append(f"- {label} : {res.get('reason', 'écarté')}")
                continue
            val = res["raw"]

            # Feature scan→ordre : sizing affiché + commande prête à l'emploi.
            # Budget configurable via .env : POSITION_BUDGET_PCT / POSITION_BUDGET_MAX
            try:
                budget = min(cash * POSITION_BUDGET_PCT / 100, POSITION_BUDGET_MAX)
                # Budget en EUR, cours dans la devise du titre → conversion FX
                price_eur = current_price * q_fx
                qty_sugg = max(1, int(budget / price_eur)) if price_eur else 1
                cost_eur = qty_sugg * price_eur
                fx_note = (f" ({qty_sugg * current_price:.0f}{q_sym}, taux {q_fx:.3f})"
                           if q_cur != "EUR" else "")
                val += (
                    f"\n→ Taille suggérée : {qty_sugg} titres ≈ {cost_eur:.0f}€{fx_note} "
                    f"({cost_eur / cash * 100:.0f}% du cash)\n"
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
                            capped = f" — TP IA {tp_v}{q_sym} hors horizon, plafonné"
                            tp_v = tp_cap
                        tp_pct = (tp_v / current_price - 1) * 100
                        stretch = f" (TP +{tp_pct:.0f}%{capped})" if tp_pct >= 11 or capped else ""
                        val += (
                            f"\n   puis protection : /ordre vendre {t} {qty_sugg} "
                            f"expert {sl_v} {tp_v}{stretch}"
                        )

                        # ── Rentabilité nette de frais (courtage A/R, en EUR) ──
                        roundtrip = 2 * BROKERAGE_FEE
                        gross_tp  = qty_sugg * (tp_v - current_price) * q_fx
                        net_tp    = gross_tp - roundtrip
                        loss_sl   = qty_sugg * (current_price - sl_v) * q_fx + roundtrip
                        fee_pct   = roundtrip / cost_eur * 100 if cost_eur else 0
                        val += (
                            f"\n💸 Frais A/R ≈ {roundtrip:.2f}€ ({fee_pct:.1f}% de la position)"
                            f"\n   Gain net au TP ≈ +{net_tp:.0f}€ | Perte au SL ≈ -{loss_sl:.0f}€"
                        )
                        # Garde : si les frais mangent une part trop grande du gain
                        if gross_tp > 0 and gross_tp < roundtrip * MIN_NET_GAIN_FEE_RATIO:
                            val += (
                                f"\n⚠️ Frais élevés vs gain visé : position trop petite "
                                f"pour ce trade (gain {gross_tp:.0f}€ < {MIN_NET_GAIN_FEE_RATIO:.0f}× frais). "
                                f"Augmente la taille ou passe ton tour."
                            )
            except Exception:
                pass

            opportunities.append(val)
            # Stocke pour le moteur autonome (Option B)
            try:
                entry_m = re.search(r"Entr[ée]e?\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
                sl_m    = re.search(r"\bSL\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
                tp_m    = re.search(r"\bTP\s*:?\s*[$€£]?\s*(\d+(?:[.,]\d+)?)", val)
                if entry_m and sl_m and tp_m:
                    portfolio.add_pending_opportunity(
                        t,
                        float(entry_m.group(1).replace(",", ".")),
                        float(sl_m.group(1).replace(",", ".")),
                        float(tp_m.group(1).replace(",", ".")),
                        reason=val.splitlines()[0][:150],
                        source="scan",
                        context=res.get("context"),
                    )
            except Exception as _pe:
                print(f"[scan] pending_opp store error {t}: {_pe}")

        # ── Passe GAIN RÉDUIT si rien ne passe à +TP% (opt-in SMALL_GAIN_MODE) ─
        small_opps, small_rejected = [], []
        if SMALL_GAIN_MODE and not opportunities and screened:
            print(f"[scan] 0 opportunité à +{_TP}% — passe gain réduit sur "
                  f"{[c['ticker'] for c in screened[:3]]}")
            _ctx = _trading_context()
            small_opps, small_rejected = _small_gain_pass(
                get_provider(), screened, cash, _ctx,
                datetime.now(PARIS).strftime("%d/%m/%Y"),
            )

        # ── Assemblage final ──────────────────────────────────────────────────
        result_parts = [f"POSITIONS\n{portfolio_summary}"]
        if opportunities:
            result_parts.append("OPPORTUNITÉS VALIDÉES\n" + "\n\n".join(opportunities))
        elif small_opps:
            result_parts.append(
                f"Aucun candidat à +{_TP}% — repli sur le court terme.\n\n"
                "⚡ OPPORTUNITÉS COURT TERME (gain réduit, 1-5 jours)\n"
                + "\n\n".join(small_opps)
            )
        else:
            no_opp = "Aucun candidat ne passe le filtre technique aujourd'hui."
            no_opp += "\n\n→ /research TICKER pour un avis ciblé sur un titre précis."
            result_parts.append(no_opp)
        # Les exclusions sont toujours affichées (même quand il y a des opportunités)
        if rejected:
            result_parts.append("EXCLUS\n" + "\n".join(rejected))
        if small_rejected:
            result_parts.append("EXCLUS (gain réduit)\n" + "\n".join(small_rejected))

        elapsed = (datetime.now(PARIS) - t_start).total_seconds()
        print(f"[scan] terminé en {elapsed:.0f}s — {len(opportunities)} opportunité(s), "
              f"{len(rejected)} écartée(s). Envoi du résultat final…")

        send_fn(
            f"{emoji} SCAN OPPORTUNITÉS — {regime_summary}\n"
            f"{len(SCAN_UNIVERSE)} actions scannées · {len(screened)} passent les filtres\n\n"
            + "\n\n".join(result_parts)
            + f"\n\n💰 Cash: {cash}€"
        )
        print("[scan] résultat final envoyé ✅")

        # Mode autonome : si actif + Playwright connecté + opportunités trouvées
        # → entre immédiatement, sans attendre le prochain check planifié
        if opportunities or small_opps:
            _trigger_autonomous(send_fn)

    except Exception as e:
        import traceback
        print(f"[scan] ERREUR : {e}\n{traceback.format_exc()}")
        try:
            send_fn(f"⚠️ Erreur scan: {e}")
        except Exception as send_err:
            print(f"[scan] impossible d'envoyer l'erreur (réseau ?) : {send_err}")
    finally:
        _scan_lock.release()


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
