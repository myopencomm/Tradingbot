import json
from enum import Enum
from config import BASE_DIR

STATE_PATH = BASE_DIR / "bot_state.json"


class BotMode(Enum):
    CLASSIC = "classic"
    PLAYWRIGHT = "playwright"


def get_mode() -> BotMode:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return BotMode(data.get("mode", "classic"))
    except Exception:
        return BotMode.CLASSIC


def set_mode(mode: BotMode):
    STATE_PATH.write_text(
        json.dumps({"mode": mode.value}, indent=2), encoding="utf-8"
    )


def is_playwright() -> bool:
    return get_mode() == BotMode.PLAYWRIGHT
