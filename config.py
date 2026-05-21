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

# Trading rules (overridable via .env)
DEFAULT_SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "10"))
DEFAULT_TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "15"))

# Scheduler (Paris time, 24h format)
CHECK_TIMES = ["09:00", "12:00", "15:00", "17:00"]
ANALYSIS_TIME = "09:05"
