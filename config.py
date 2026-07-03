import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

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
