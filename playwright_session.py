"""
Singleton gérant l'instance Playwright et la session Bourse Direct.

IMPORTANT — threading :
Playwright sync API lie ses objets (browser, page) au thread qui les crée.
Les handlers Telegram tournent chacun dans leur propre thread, donc on ne peut
PAS partager la page entre /connect et /sync directement.

Solution : un thread worker unique possède Playwright. Les autres threads
soumettent des callables via run(fn) ; le worker les exécute séquentiellement
sur la page et renvoie le résultat. Une seule opération à la fois (suffisant ici).
"""
import threading
import queue
from datetime import datetime

_lock = threading.Lock()
_task_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_ready_event = threading.Event()
_start_error: str | None = None
_connected_at: datetime | None = None
_running = False


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
    """Arrête le worker et ferme le navigateur."""
    global _worker, _connected_at
    with _lock:
        _connected_at = None
        if _worker is not None and _worker.is_alive():
            _task_queue.put(None)  # sentinelle
            _worker.join(timeout=10)
        _worker = None
