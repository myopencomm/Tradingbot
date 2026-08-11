"""
Transport Telegram — envoyer, éditer, supprimer, télécharger.

Module FEUILLE : il ne connaît que l'API HTTP de Telegram et la configuration.
Il ne sait rien du trading, des commandes ni du portefeuille.

C'est ce qui casse trois cycles d'import. `ai_provider`, `playwright_session`
et le moteur autonome avaient besoin d'UNE fonction — `send` — et importaient
pour cela `telegram_bot` tout entier, c'est-à-dire l'ensemble des handlers,
qui eux-mêmes réimportaient ces modules. Les imports devaient donc être faits
à l'intérieur des fonctions pour retarder la résolution ; le graphe de
dépendances en devenait illisible.
"""
import threading

import requests

from config import TELEGRAM_TOKEN, CHAT_ID

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def _post(methode: str, quoi: str = "", **kw):
    """Appel POST à l'API, échec journalisé et non propagé.

    Un envoi Telegram qui échoue ne doit jamais interrompre une décision de
    trading en cours — mais il doit se voir dans le log.
    """
    if not TELEGRAM_TOKEN:
        return None
    try:
        return requests.post(f"{API}/{methode}", timeout=kw.pop("timeout", 10), **kw)
    except Exception as e:
        print(f"Telegram {quoi or methode} error: {e}")
        return None


def send(text: str, chat_id: str = None) -> bool:
    # Trace compacte de TOUT message sortant : indispensable pour diagnostiquer
    # a posteriori pourquoi le bot a pris (ou pas) une décision.
    print(f"[TG] {text.replace(chr(10), ' ⏎ ')[:200]}")
    r = _post("sendMessage", "send",
              json={"chat_id": chat_id or CHAT_ID, "text": text})
    return bool(r and r.status_code == 200)


def send_editable(text: str, chat_id: str = None) -> int | None:
    """Envoie un message et retourne son message_id (pour édition/suppression)."""
    r = _post("sendMessage", "send_editable",
              json={"chat_id": chat_id or CHAT_ID, "text": text})
    if r and r.status_code == 200:
        return r.json().get("result", {}).get("message_id")
    return None


def edit_message(msg_id: int, text: str, chat_id: str = None) -> bool:
    """Édite un message existant (max 4096 chars, Telegram ignore si identique)."""
    if not msg_id:
        return False
    r = _post("editMessageText", "edit",
              json={"chat_id": chat_id or CHAT_ID, "message_id": msg_id,
                    "text": text})
    return bool(r and r.status_code == 200)


def delete_message(msg_id: int, chat_id: str = None) -> bool:
    """Supprime un message Telegram (typiquement un message de progression)."""
    if not msg_id:
        return False
    r = _post("deleteMessage", "delete",
              json={"chat_id": chat_id or CHAT_ID, "message_id": msg_id})
    return bool(r and r.status_code == 200)


def send_photo(image_bytes: bytes, caption: str = "", chat_id: str = None) -> bool:
    """Envoie une image (PNG) sur Telegram."""
    r = _post("sendPhoto", "send_photo", timeout=30,
              data={"chat_id": chat_id or CHAT_ID, "caption": caption[:1024]},
              files={"photo": ("dashboard.png", image_bytes, "image/png")})
    return bool(r and r.status_code == 200)


def download_photo(photos: list) -> bytes | None:
    """Télécharge la meilleure résolution d'une photo reçue."""
    if not TELEGRAM_TOKEN:
        return None
    try:
        path = requests.get(f"{API}/getFile",
                            params={"file_id": photos[-1]["file_id"]},
                            timeout=10).json()["result"]["file_path"]
        return requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}",
            timeout=20).content
    except Exception as e:
        print(f"Photo download error: {e}")
        return None


def get_updates(offset: int | None, timeout: int = 30,
                allowed: tuple = ("message",)):
    """Long polling. Retourne la liste des updates, ou None si l'appel échoue.

    `None` et `[]` sont distincts : le premier dit « l'appel a raté », le second
    « rien de nouveau ». L'appelant ne doit pas avancer son offset sur un échec.
    """
    if not TELEGRAM_TOKEN:
        return None
    params = {"timeout": timeout, "allowed_updates": list(allowed)}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 5)
        if r.status_code == 200:
            return r.json().get("result", [])
        print(f"Polling HTTP {r.status_code}")
    except Exception as e:
        print(f"Polling error: {e}")
    return None


class typing:
    """Affiche « écrit… » dans Telegram tant que le bloc `with` est actif.

    Telegram efface l'indicateur après ~5 s ou dès qu'un message est envoyé :
    on le renvoie donc toutes les 4 s jusqu'à la fin du traitement.
    """

    def __init__(self, chat_id: str = None):
        self.chat_id = chat_id or CHAT_ID
        self._stop = threading.Event()

    def __enter__(self):
        if not TELEGRAM_TOKEN:
            return self

        def loop():
            while not self._stop.is_set():
                _post("sendChatAction", "typing", timeout=5,
                      json={"chat_id": self.chat_id, "action": "typing"})
                self._stop.wait(4)

        threading.Thread(target=loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False


def run_long(cid, fn, *args, **kwargs):
    """Exécute `fn` dans un thread, « écrit… » affiché jusqu'à la fin."""
    def worker():
        with typing(cid):
            fn(*args, **kwargs)
    threading.Thread(target=worker, daemon=True).start()
