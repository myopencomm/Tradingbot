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

# Frais de courtage Bourse Direct par ordre (confirmé via capture réseau : ~1.98€).
# Un aller-retour (achat + vente) = 2 × ce montant. Sert au calcul de rentabilité nette.
BROKERAGE_FEE = float(os.getenv("BROKERAGE_FEE", "1.98"))
# Marge mini : le gain net au TP doit valoir au moins ce multiple des frais A/R,
# sinon le trade ne vaut pas le coup (frais qui mangent le gain).
MIN_NET_GAIN_FEE_RATIO = float(os.getenv("MIN_NET_GAIN_FEE_RATIO", "5"))

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
