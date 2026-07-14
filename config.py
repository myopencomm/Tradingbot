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

# Sizing par le RISQUE (fractional-Kelly conservateur) : la perte au SL vaut
# RISK_PER_TRADE_PCT % du budget autonome — plus jamais tout le budget sur un
# trade. Coût plafonné à MAX_POSITION_PCT % du budget. Réduction de moitié si
# la volatilité 20j du titre dépasse VOL_SCALE_TRIGGER × sa volatilité 1 an
# (volatility scaling, Barroso & Santa-Clara 2015).
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
MAX_POSITION_PCT   = float(os.getenv("MAX_POSITION_PCT", "30"))
VOL_SCALE_TRIGGER  = float(os.getenv("VOL_SCALE_TRIGGER", "1.5"))

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

# Scheduler (Paris time, 24h format)
CHECK_TIMES = ["09:00", "12:00", "15:00", "17:00"]
ANALYSIS_TIME = "09:05"
