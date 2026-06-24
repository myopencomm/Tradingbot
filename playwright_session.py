"""
Singleton gérant l'instance Playwright et la session Bourse Direct.

IMPORTANT — threading :
Playwright sync API lie ses objets (browser, page) au thread qui les crée.
Les handlers Telegram tournent chacun dans leur propre thread, donc on ne peut
PAS partager la page entre /connect et /sync directement.

Solution : un thread worker unique possède Playwright. Les autres threads
soumettent des callables via run(fn) ; le worker les exécute séquentiellement
sur la page et renvoie le résultat. Une seule opération à la fois (suffisant ici).

Keepalive : un callable _keepalive est soumis toutes les 20 min pour maintenir
la session BD côté serveur (les cookies BD expirent après ~30 min d'inactivité).
"""
import threading
import queue
import time
from datetime import datetime

_lock = threading.Lock()
_task_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_keepalive_thread: threading.Thread | None = None
_ready_event = threading.Event()
_start_error: str | None = None
_connected_at: datetime | None = None
_running = False

KEEPALIVE_INTERVAL = 20 * 60  # 20 minutes — BD expire après ~30 min d'inactivité

# URL légère : page d'accueil BD connecté (ne génère pas de log de trading)
_KEEPALIVE_URL = "https://www.boursedirect.fr/fr/page/portefeuille"


class _Task:
    __slots__ = ("fn", "result", "error", "done")

    def __init__(self, fn):
        self.fn = fn
        self.result = None
        self.error = None
        self.done = threading.Event()


def _worker_loop():
    """Boucle du thread worker : possède playwright/browser/page."""
    global _start_error, _running
    pw = browser = ctx = page = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        _running = True
        _ready_event.set()
    except Exception as e:
        _start_error = str(e)
        _ready_event.set()
        return

    # Boucle de traitement des tâches
    while True:
        task = _task_queue.get()
        if task is None:  # sentinelle d'arrêt
            break
        try:
            task.result = task.fn(page)
        except Exception as e:
            task.error = e
        finally:
            task.done.set()

    # Nettoyage (dans le thread worker, où les objets ont été créés)
    for closer in (lambda: page.close(), lambda: ctx.close(),
                   lambda: browser.close(), lambda: pw.stop()):
        try:
            closer()
        except Exception:
            pass
    _running = False


def start() -> bool:
    """Démarre le thread worker + Playwright. Retourne True si prêt."""
    global _worker, _start_error
    with _lock:
        if _worker is not None and _worker.is_alive():
            return True
        _ready_event.clear()
        _start_error = None
        _worker = threading.Thread(target=_worker_loop, daemon=True)
        _worker.start()

    if not _ready_event.wait(timeout=30):
        print("[Playwright] Timeout au démarrage du worker")
        return False
    if _start_error:
        print(f"[Playwright] Erreur lancement : {_start_error}")
        return False
    return True


def run(fn, timeout: float = 150):
    """
    Soumet fn(page) au thread worker et attend le résultat.
    fn reçoit la page Playwright en argument et s'exécute dans le bon thread.
    """
    if not _running:
        raise RuntimeError("Session Playwright non démarrée")
    task = _Task(fn)
    _task_queue.put(task)
    if not task.done.wait(timeout=timeout):
        raise TimeoutError("Opération Playwright expirée")
    if task.error:
        raise task.error
    return task.result


def is_connected() -> bool:
    with _lock:
        return _running and _connected_at is not None


def mark_connected():
    global _connected_at, _keepalive_thread
    with _lock:
        _connected_at = datetime.now()
    # Démarre le keepalive si ce n'est pas déjà le cas
    with _lock:
        if _keepalive_thread is None or not _keepalive_thread.is_alive():
            _keepalive_thread = threading.Thread(
                target=_keepalive_loop, daemon=True, name="playwright-keepalive"
            )
            _keepalive_thread.start()


def _keepalive_fn(page) -> bool:
    """
    Ping léger : visite la page portefeuille BD pour rafraîchir les cookies.
    Retourne True si la session semble encore active (pas redirigé vers login).
    """
    try:
        page.goto(_KEEPALIVE_URL, wait_until="domcontentloaded", timeout=15000)
        url = page.url
        alive = "login" not in url and "connexion" not in url
        print(f"[Playwright keepalive] {'✅ session active' if alive else '⚠️ session expirée — reconnexion requise'} ({url[:60]})")
        return alive
    except Exception as e:
        print(f"[Playwright keepalive] erreur : {e}")
        return False


def _keepalive_loop():
    """Thread daemon : soumet un ping toutes les KEEPALIVE_INTERVAL secondes."""
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        # Stoppe si plus connecté
        if not is_connected():
            print("[Playwright keepalive] session terminée — arrêt keepalive")
            break
        try:
            alive = run(_keepalive_fn, timeout=30)
            if not alive:
                # Session expirée : marquer comme déconnecté
                global _connected_at
                with _lock:
                    _connected_at = None
                print("[Playwright keepalive] session BD expirée — /connect requis")
                # Notification Telegram si possible
                try:
                    from telegram_bot import send
                    send("⚠️ Session Bourse Direct expirée.\nRelance /connect pour reprendre le mode Playwright.")
                except Exception:
                    pass
                break
        except Exception as e:
            print(f"[Playwright keepalive] run error : {e}")


def session_age_str() -> str:
    if _connected_at is None:
        return "non connecté"
    delta = datetime.now() - _connected_at
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m = rem // 60
    return f"{h}h{m:02d}" if h else f"{m}min"


def stop():
    """Arrête le worker et ferme le navigateur."""
    global _worker, _connected_at
    with _lock:
        _connected_at = None
        if _worker is not None and _worker.is_alive():
            _task_queue.put(None)  # sentinelle
            _worker.join(timeout=10)
        _worker = None
