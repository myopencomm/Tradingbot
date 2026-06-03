"""
Singleton gérant l'instance Playwright et la session Bourse Direct.
Le cycle de vie : lancé à l'activation du mode Playwright, fermé au retour Classic.
"""
import threading
from datetime import datetime

_lock = threading.Lock()

_browser = None
_page = None
_playwright = None
_connected_at: datetime | None = None


def start() -> bool:
    """Lance Playwright (Chromium headless). Retourne True si OK."""
    global _browser, _page, _playwright
    with _lock:
        if _browser is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(headless=True)
            _page = _browser.new_page()
            return True
        except Exception as e:
            print(f"[Playwright] Erreur lancement : {e}")
            _cleanup()
            return False


def get_page():
    """Retourne la page active ou None si session fermée."""
    with _lock:
        return _page


def is_connected() -> bool:
    with _lock:
        return _page is not None and _connected_at is not None


def mark_connected():
    global _connected_at
    with _lock:
        _connected_at = datetime.now()


def session_age_str() -> str:
    if _connected_at is None:
        return "non connecté"
    delta = datetime.now() - _connected_at
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m = rem // 60
    return f"{h}h{m:02d}" if h else f"{m}min"


def stop():
    """Ferme proprement la session et le navigateur."""
    global _connected_at
    with _lock:
        _connected_at = None
        _cleanup()


def _cleanup():
    global _browser, _page, _playwright
    try:
        if _page:
            _page.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _browser = None
    _page = None
    _playwright = None
