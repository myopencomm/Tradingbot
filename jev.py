"""
Client Jev (TypeSafe System One) — jugements typés rapides, pas de texte.

Jev répond à des questions étroites sur un état fourni : probabilité (noul),
choix dans une liste, score sur une échelle ordonnée. Fiable sur du texte
(titres de news, thèses), pas sur des calculs (cf. revue du 23/09/2026).

Utilisé par :
  - news_alert : tri des news des positions détenues
  - analysis   : contre-avis indépendant sur la thèse d'un ACHAT (veto seul)
  - research   : score de sentiment des forums

Sans clé TYPESAFE_API_KEY, ou si l'appel échoue : `ask` renvoie None. Les
appelants décident quoi faire d'un avis absent — jamais une erreur bloquante.
"""
import os
import time

import requests

JEV_URL = "https://api.typesafe.ai/v1/systemone"


def available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY", ""))


def ask(state: dict, questions: dict, label: str = "jev") -> dict | None:
    """Pose `questions` sur `state`. Renvoie le dict `answers` brut, ou None."""
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key or not questions:
        return None
    body = {"model": "jev-latest", "state": state, "questions": questions}
    for essai in range(3):
        try:
            r = requests.post(JEV_URL, json=body, timeout=30,
                              headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (429, 529):
                time.sleep(2 ** essai * 2)
                continue
            if r.status_code != 200:
                print(f"[{label}] Jev HTTP {r.status_code} : {r.text[:200]}")
                return None
            return r.json().get("answers", {})
        except Exception as e:
            print(f"[{label}] Jev : {e}")
            return None
    print(f"[{label}] Jev saturé, avis abandonné")
    return None


def noul(answers: dict | None, qid: str) -> float | None:
    """Probabilité d'une question oui/non, None si absente."""
    try:
        return float(answers[qid]["noul"])
    except Exception:
        return None


def score(answers: dict | None, qid: str) -> float | None:
    """Position pondérée (0 … N-1) d'une question score, None si absente."""
    try:
        return float(answers[qid]["score"])
    except Exception:
        return None
