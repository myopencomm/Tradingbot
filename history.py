"""
Historique des trades clôturés — lecture et écriture du fichier.

Module FEUILLE. `stats` et `lessons` s'importaient mutuellement : `lessons`
avait besoin de lire l'historique (chez `stats`), `stats` avait besoin de
tagger un trade clôturé (chez `lessons`). Chacun contournait par un import
différé au fond d'une fonction.

Le fichier n'appartient ni à l'un ni à l'autre : il est ici, et les deux le
lisent. `stats` calcule, `lessons` interprète — aucun des deux ne possède la
persistance.

L'écriture est atomique, comme celle de `positions.json` : un process tué au
mauvais moment laissait un JSON tronqué, donc tout l'historique de trading
perdu au redémarrage (`load()` retombe silencieusement sur son défaut).
"""
import json
import os
import threading

from config import HISTORY_PATH

_LOCK = threading.RLock()


def load() -> dict:
    with _LOCK:
        try:
            if HISTORY_PATH.exists():
                return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[history] lecture impossible : {e}")
        return {"closed_trades": []}


def save(data: dict):
    with _LOCK:
        tmp = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, HISTORY_PATH)


def closed_trades() -> list:
    return load().get("closed_trades", [])
