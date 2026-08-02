import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Allowlist des expéditeurs autorisés à piloter le bot. Par défaut : CHAT_ID
# seul. Pour autoriser plusieurs comptes (ex: conjoint), CHAT_ID="111,222".
# SÉCURITÉ CRITIQUE : tout message d'un chat hors de cette liste est ignoré —
# sans ce filtre, quiconque connaît le @username du bot pourrait passer des
# ordres réels et relayer le code 2FA de connexion Bourse Direct.
AUTHORIZED_CHAT_IDS = {c.strip() for c in (CHAT_ID or "").split(",") if c.strip()}

# Dashboard — jeton optionnel exigé quand le serveur est exposé au réseau
# (DASHBOARD_BIND != 127.0.0.1). Vide = pas d'auth (OK en local uniquement).
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
DASHBOARD_BIND  = os.getenv("DASHBOARD_BIND", "127.0.0.1").strip() or "127.0.0.1"

# AI — plug any provider via .env
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic").lower()
AI_MODEL = os.getenv("AI_MODEL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Paths
POSITIONS_PATH = BASE_DIR / "positions.json"
TRADING_CONTEXT_PATH = BASE_DIR / "CLAUDE_TRADING_CONTEXT.md"
MACRO_ANALYSIS_PATH  = BASE_DIR / "macro_analysis.md"
HISTORY_PATH = BASE_DIR / "trades_history.json"

# Gmail IMAP — sync automatique ordres Bourse Direct (optionnel)
GMAIL_USER         = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# Trading rules (overridable via .env)
DEFAULT_SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "7"))
DEFAULT_TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "10"))

# ── Stratégie momentum validée par la recherche (Phase 1, 07/2026) ───────────
# Formation 12 mois HORS dernier mois (Jegadeesh & Titman 1993) : le momentum
# 1 mois seul TEND À S'INVERSER (Jegadeesh 1990, Lehmann 1990) — on ne chasse
# plus les hausses du mois. Entrée uniquement au-dessus de la MM200 (filtre de
# tendance, Moskowitz-Ooi-Pedersen 2012) et hors surchauffe court terme.
RSI_ENTRY_MIN = float(os.getenv("RSI_ENTRY_MIN", "35"))   # zone d'entrée saine
RSI_ENTRY_MAX = float(os.getenv("RSI_ENTRY_MAX", "65"))   # > 65 = on attend le repli
RSI_HARD_MAX  = float(os.getenv("RSI_HARD_MAX", "70"))    # veto dur à l'achat

# Stops adaptés à la volatilité du titre (Kaminski & Lo 2014 : les stops
# aident les stratégies momentum) : distance SL ≈ ATR_SL_MULT × ATR14,
# bornée [MIN_SL_PCT, MAX_SL_PCT]. TP ≥ MIN_RR × distance SL.
ATR_SL_MULT = float(os.getenv("ATR_SL_MULT", "2.0"))
MIN_SL_PCT  = float(os.getenv("MIN_SL_PCT", "3"))
MAX_SL_PCT  = float(os.getenv("MAX_SL_PCT", "10"))
MIN_RR      = float(os.getenv("MIN_RR", "1.5"))

# Veto « résultats imminents » : un SL ne protège PAS d'un gap de résultats
# (le titre ouvre au-delà du stop, pas dessus). On exclut donc à l'achat UNIQUE-
# MENT si les prochains résultats tombent dans moins de EARNINGS_VETO_DAYS jours.
# Au-delà, ce n'est PAS un motif d'exclusion (un swing momentum tient des semaines
# et croisera de toute façon des résultats — bloquer l'entrée à 3 semaines ampute
# le vivier sans protéger la position). Cadre l'IA pour l'empêcher d'improviser
# une fenêtre trop large.
EARNINGS_VETO_DAYS = int(os.getenv("EARNINGS_VETO_DAYS", "6"))

# Sizing par le RISQUE (fractional-Kelly conservateur) : la perte au SL vaut
# RISK_PER_TRADE_PCT % du budget autonome — plus jamais tout le budget sur un
# trade. Coût plafonné à MAX_POSITION_PCT % du budget. Réduction de moitié si
# la volatilité 20j du titre dépasse VOL_SCALE_TRIGGER × sa volatilité 1 an
# (volatility scaling, Barroso & Santa-Clara 2015).
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
MAX_POSITION_PCT   = float(os.getenv("MAX_POSITION_PCT", "30"))
VOL_SCALE_TRIGGER  = float(os.getenv("VOL_SCALE_TRIGGER", "1.5"))

# Corrélation avec les positions déjà détenues (07/2026) : un score quant élevé
# sur deux titres du même pari (ex: AIR + SAF, aéro) ne diversifie rien — ça
# double la même exposition. Corrélation de Pearson sur les rendements
# quotidiens des CORR_LOOKBACK_DAYS derniers jours face à chaque position
# gérée par le bot (hors HOLD long terme). Au-delà de CORR_VETO_THRESHOLD,
# entrée bloquée (même pari) ; entre CORR_DAMPEN_THRESHOLD et le seuil de veto,
# risque réduit de moitié.
# Tolérance du trailing breakeven : BD arrondit les prix au pas de cotation,
# donc un SL posé « au PRU » retombe quelques centimes en dessous (196.84 pour
# un PRU de 196.90 = 0.03%). Sans tolérance, le bot annulerait et reposerait la
# protection en boucle pour un gain nul — en exposant la position à une fenêtre
# SANS protection à chaque fois. En deçà de ce seuil, le SL est considéré comme
# déjà au breakeven.
BREAKEVEN_TOLERANCE_PCT = float(os.getenv("BREAKEVEN_TOLERANCE_PCT", "0.3"))

# Univers US découvert automatiquement (market_universe) : seuil de liquidité
# appliqué AU SCAN. Le cache est construit large (5 M$/jour) ; ce filtre-ci
# décide de ce qu'on classe réellement. Mesuré le 29/07/2026 :
#   5 M$/j  -> 2558 valeurs, top 8 = micro-caps biotech à +230/+785% (ingérable)
#  50 M$/j  -> 1340 valeurs, top 8 = biotechs mid-cap
# 200 M$/j  ->  559 valeurs, top 8 = JBHT, SCCO, CVS, HUM… (liquides, sûrement
#              traitables chez BD, cohérentes avec des positions de 500-1000€)
# Mettre 0 pour désactiver l'univers étendu et revenir à la liste manuelle.
SCAN_US_MIN_DOLLAR_VOLUME = float(os.getenv("SCAN_US_MIN_DOLLAR_VOLUME", "200000000"))

CORR_LOOKBACK_DAYS    = int(os.getenv("CORR_LOOKBACK_DAYS", "90"))
CORR_DAMPEN_THRESHOLD = float(os.getenv("CORR_DAMPEN_THRESHOLD", "0.6"))
CORR_VETO_THRESHOLD   = float(os.getenv("CORR_VETO_THRESHOLD", "0.85"))

# Mode GAIN RÉDUIT (trades courts forcés quand rien ne passe) : DÉSACTIVÉ par
# défaut — c'est le schéma « overtrading » documenté (Barber & Odean 2000) et
# il a produit les entrées en surchauffe de 07/2026. SMALL_GAIN_MODE=on pour
# le réactiver en connaissance de cause.
SMALL_GAIN_MODE = os.getenv("SMALL_GAIN_MODE", "off").strip().lower() in ("on", "true", "1", "yes")

# Sizing d'une nouvelle position : % du cash, plafonné en €
POSITION_BUDGET_PCT = float(os.getenv("POSITION_BUDGET_PCT", "50"))
POSITION_BUDGET_MAX = float(os.getenv("POSITION_BUDGET_MAX", "800"))

# ── Frais Bourse Direct — BARÈME RÉEL, PAR ORDRE ──────────────────────────────
# Tarifs publics BD (boursedirect.fr/fr/bourse/tarifs), VÉRIFIÉS au centime près
# sur nos propres ordres exécutés (le PRU affiché par BD inclut tous les frais) :
#
#   AIR  5 × 196.52€ = 982.60€  → PRU BD 984.50€  → 1.90€  = courtage seul
#                                 (Airbus SE, siège aux Pays-Bas : pas de TTF)
#   GLE 12 ×  75.55€ = 906.60€  → PRU BD 912.13€  → 5.53€  = 1.90 courtage
#                                 + 3.63 de TTF (0.4% — Société Générale, France)
#   BAC 12 ×  61.43$ = 737.16$  → PRU BD 656.79€  → 9.04€  = 8.50 courtage US
#                                 + 0.52 de commission de change (0.08%)
#
# Trois composantes, pas une : le forfait unique de 1.98€ qui servait jusqu'ici
# ignorait la TTF (plus chère que le courtage sur une grande valeur française)
# et la commission de change, et surestimait le courtage des petits ordres.

# Courtage Euronext (Paris / Amsterdam / Bruxelles) : barème par tranches sur le
# montant de l'ordre. (2 900€ d'ordre → 3.80€, pas 1.98€.)
EURONEXT_FEE_TIERS = ((500.0, 0.99), (1000.0, 1.90), (2000.0, 2.90), (4400.0, 3.80))
EURONEXT_FEE_RATE  = 0.0009            # au-delà de 4 400€

# Courtage US (NYSE / NASDAQ) : forfait jusqu'à 10 000€, puis 0.09%.
BROKERAGE_FEE_US   = float(os.getenv("BROKERAGE_FEE_US", "8.50"))
US_FEE_THRESHOLD   = 10000.0
US_FEE_RATE        = 0.0009

# Autres places étrangères : pourcentage AVEC MINIMUM — c'est le minimum qui
# s'applique à notre taille de position, et il est bien au-dessus du tarif US.
FOREIGN_FEE_TABLE = {
    ".L":  (0.0015, 15.00),   # Londres
    ".DE": (0.0015, 15.00),   # Xetra / Francfort
    ".MC": (0.0020, 18.00),   # Madrid
    ".SW": (0.0020, 18.00),   # Suisse
    ".LS": (0.0020, 18.00),   # Lisbonne
}
FOREIGN_FEE_DEFAULT = (0.0048, 41.90)  # « autres marchés » (dont Milan .MI)

# Commission de change : taux BD + 0.08% par opération, sur tout ordre libellé
# en devise étrangère (US, Londres, Suisse…). Invisible dans le courtage, bien
# réelle dans le PRU.
FX_COMMISSION_RATE = 0.0008

# TTF — taxe sur les transactions financières française. 0.4% depuis le
# 01/04/2025 (0.3% avant), à l'ACHAT uniquement, sur les titres des sociétés
# dont le SIÈGE SOCIAL est en France et la capitalisation > 1 Md€ au 1er
# décembre précédent. Ni la place ni le suffixe ne suffisent à trancher :
# Airbus (AIR.PA) est néerlandaise et exonérée, Genfit (GNFT.PA) est française
# mais sous le milliard — les deux le confirment sur nos ordres réels.
TTF_RATE           = float(os.getenv("TTF_RATE", "0.004"))
TTF_MIN_MARKET_CAP = 1_000_000_000.0

# Devise par suffixe : sert à savoir si la commission de change s'applique.
# Absent de la table = EUR (Euronext, Xetra, Milan, Madrid, Lisbonne).
CURRENCY_BY_SUFFIX = {"": "USD", ".L": "GBP", ".SW": "CHF"}

# Suffixes Yahoo des places au tarif Euronext.
EURONEXT_SUFFIXES = (".PA", ".AS", ".BR")


def _suffix(ticker: str) -> str:
    """Suffixe Yahoo du ticker ('' = US, convention du projet)."""
    t = (ticker or "").strip().upper()
    return t[t.rindex("."):] if "." in t else ""


def is_foreign_ticker(ticker: str) -> bool:
    """Vrai si le ticker se traite hors Euronext (donc au tarif majoré)."""
    if not (ticker or "").strip():
        return False
    return _suffix(ticker) not in EURONEXT_SUFFIXES


def is_foreign_currency(ticker: str) -> bool:
    """Vrai si l'ordre est libellé en devise → commission de change."""
    return CURRENCY_BY_SUFFIX.get(_suffix(ticker), "EUR") != "EUR"


def brokerage_fee(ticker: str = "", amount_eur: float = 0.0) -> float:
    """Courtage d'UN ordre, hors taxes et hors change. Sans ticker : Euronext."""
    amount = max(0.0, float(amount_eur or 0.0))
    sfx = _suffix(ticker) if (ticker or "").strip() else ".PA"

    if sfx in EURONEXT_SUFFIXES:
        for ceiling, fee in EURONEXT_FEE_TIERS:
            if amount <= ceiling:
                return fee
        return round(amount * EURONEXT_FEE_RATE, 2)

    if sfx == "":                                   # NYSE / NASDAQ
        if amount <= US_FEE_THRESHOLD:
            return BROKERAGE_FEE_US
        return round(amount * US_FEE_RATE, 2)

    rate, minimum = FOREIGN_FEE_TABLE.get(sfx, FOREIGN_FEE_DEFAULT)
    return round(max(minimum, amount * rate), 2)


def _ttf_liable(ticker: str) -> bool:
    """Ce titre supporte-t-il la TTF française à l'achat ?

    Critère officiel : siège social en France ET capitalisation > 1 Md€. Les
    deux données viennent de yfinance (`country`, `marketCap`), mises en cache
    par `prices`. Import différé — `config` ne doit dépendre de rien.

    Défaut en cas de donnée manquante : TAXÉ pour un titre `.PA`. Surestimer
    les frais fait renoncer à un trade marginal ; les sous-estimer fait entrer
    dans un trade qui ne couvre pas ses coûts.
    """
    if _suffix(ticker) != ".PA":
        return False
    try:
        import prices
        return prices.is_french_large_cap(ticker)
    except Exception:
        return True


def order_fees(ticker: str = "", amount_eur: float = 0.0, side: str = "buy",
               ttf_liable: bool | None = None) -> float:
    """Frais TOTAUX d'un ordre en euros : courtage + change + TTF (à l'achat).

    `ttf_liable` force la réponse (backtest, tests) et évite alors tout accès
    réseau.
    """
    amount = max(0.0, float(amount_eur or 0.0))
    fees = brokerage_fee(ticker, amount)
    if is_foreign_currency(ticker):
        fees += amount * FX_COMMISSION_RATE
    if side == "buy":
        if ttf_liable is None:
            ttf_liable = _ttf_liable(ticker)
        if ttf_liable:
            fees += amount * TTF_RATE
    return round(fees, 2)


def roundtrip_fee(ticker: str = "", amount_eur: float = 0.0,
                  ttf_liable: bool | None = None) -> float:
    """Frais aller-retour (achat + vente) pour une position de `amount_eur`."""
    return round(order_fees(ticker, amount_eur, "buy", ttf_liable)
                 + order_fees(ticker, amount_eur, "sell", ttf_liable), 2)


def min_viable_amount(ticker: str = "", tp_pct: float | None = None,
                      ttf_liable: bool | None = None) -> float:
    """Plus petite position (en €) dont le gain brut au TP vaut au moins
    `min_gain_fee_ratio` fois les frais aller-retour.

    Résolu par balayage, pas par formule : les frais mêlent un forfait par
    tranches et des composantes proportionnelles (TTF, change) — il n'y a pas
    de solution fermée propre. Renvoie 0 si aucune taille ne convient.
    """
    tp = (DEFAULT_TP_PCT if tp_pct is None else tp_pct) / 100.0
    ratio = min_gain_fee_ratio(ticker)
    # Résolu UNE fois : le balayage fait des milliers de tours, et un ticker que
    # yfinance ne sait pas classer relancerait la requête à chaque itération.
    if ttf_liable is None:
        ttf_liable = _ttf_liable(ticker)
    amount = 50.0
    while amount <= 50_000.0:
        if amount * tp >= ratio * roundtrip_fee(ticker, amount, ttf_liable):
            return round(amount, 0)
        amount += 10.0
    return 0.0


# Marge mini : le gain net au TP doit valoir au moins ce multiple des frais A/R,
# sinon le trade ne vaut pas le coup (frais qui mangent le gain).
MIN_NET_GAIN_FEE_RATIO = float(os.getenv("MIN_NET_GAIN_FEE_RATIO", "5"))
# Même exigence côté étranger. Laissée identique par défaut : la relâcher
# reviendrait à accepter des trades US moins rentables sans le décider
# explicitement. Attention, avec 5x et un TP à 10%, un trade US exige une
# position d'environ 920€ (voir min_viable_amount()).
MIN_NET_GAIN_FEE_RATIO_US = float(os.getenv("MIN_NET_GAIN_FEE_RATIO_US",
                                            str(MIN_NET_GAIN_FEE_RATIO)))


def min_gain_fee_ratio(ticker: str = "") -> float:
    """Multiple de frais exigé au TP, selon la place de cotation."""
    return MIN_NET_GAIN_FEE_RATIO_US if is_foreign_ticker(ticker) else MIN_NET_GAIN_FEE_RATIO


# Compat : ancien forfait unique. Conservé pour un override manuel via .env et
# pour les rares appels sans montant ; le barème réel passe par brokerage_fee().
BROKERAGE_FEE = float(os.getenv("BROKERAGE_FEE", "1.90"))

# Mode GAIN RÉDUIT (trades courts 1-5 jours) : quand AUCUNE opportunité à
# +DEFAULT_TP_PCT% ne passe la validation, les meilleurs candidats quant sont
# re-testés avec un TP réduit dans cette fourchette. Objectif : gagner un peu
# chaque jour plutôt que rien — la rentabilité nette de frais reste contrôlée
# par MIN_NET_GAIN_FEE_RATIO.
FALLBACK_TP_MIN_PCT = float(os.getenv("FALLBACK_TP_MIN_PCT", "3"))
FALLBACK_TP_MAX_PCT = float(os.getenv("FALLBACK_TP_MAX_PCT", "8"))

# Alertes TP automatiques : "on" (défaut) ou "off" pour les stratégies
# qui laissent courir les gagnants sur avis IA
TP_ALERTS = os.getenv("TP_ALERTS", "on").strip().lower() not in ("off", "false", "0", "no")

# Trailing stop : seuil % au-dessus du PRU pour déclencher le relevé du SL au PRU
BREAKEVEN_THRESHOLD = float(os.getenv("BREAKEVEN_THRESHOLD", "5"))

# Seuil breakeven des positions AUTONOMES. Backtest 2023-2026 (backtest.py) :
# à +3% le trail transformait les futurs gagnants en sorties à zéro (win rate
# 27% → 34%, P&L +34€ → +320€ en passant à +6%). Le SL remonte au PRU
# seulement une fois le trade réellement installé.
AUTO_BREAKEVEN_PCT = float(os.getenv("AUTO_BREAKEVEN_PCT", "6"))

# Scheduler (Paris time, 24h format)
CHECK_TIMES = ["09:00", "12:00", "15:00", "17:00"]
ANALYSIS_TIME = "09:05"

# ─── Séance US (heure de Paris) ──────────────────────────────────────────────
# Wall Street (NYSE/NASDAQ) est ouvert 15:30-22:00 Paris, bien après la clôture
# d'Euronext (17:30). Les 4 CHECK_TIMES s'arrêtant à 17:00, les positions US
# n'étaient plus surveillées ni scannées pendant leur séance la plus active.
# US_EXTENDED_HOURS=off pour revenir à l'ancien comportement (Europe seule).
US_EXTENDED_HOURS = os.getenv("US_EXTENDED_HOURS", "on").strip().lower() not in ("off", "false", "0", "no")
# Checks positions/ordres LIMITÉS aux valeurs US (alertes SL/TP), pendant la
# séance US, après la clôture Euronext. Silencieux si aucune position US.
US_CHECK_TIMES = [t.strip() for t in os.getenv("US_CHECK_TIMES", "18:00,20:00,21:40").split(",") if t.strip()]
# Scan d'opportunités limité aux valeurs US, peu après l'ouverture de Wall
# Street (laisse la liquidité se poser). Vide pour désactiver ce scan.
US_SCAN_TIME = os.getenv("US_SCAN_TIME", "16:00").strip()
