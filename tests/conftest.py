"""Configuration pytest — et coupe-circuit réseau.

Les tests de ce dossier sont des tests de CARACTÉRISATION : ils figent le
comportement ACTUEL du bot, pas un comportement idéal. C'est ce qui permet de
refactoriser (sources uniques, découpe de modules) en prouvant que rien n'a
bougé. Un test qui casse pendant un refactoring = une régression, pas un test
à mettre à jour.

Règle : aucun test ne touche le réseau, Playwright, Telegram, ni
positions.json. Elle était écrite ici en commentaire — donc pas appliquée.

INCIDENT DU 13/08/2026 : `tests/test_fallback_ia.py` exerçait la bascule de
provider IA, laquelle notifie par Telegram. `tg.send` fait un VRAI POST HTTP :
chaque exécution de la suite a donc envoyé au propriétaire du bot un message
d'alerte parlant de providers nommés « mort » et « vivant » — les doublures de
test. Une fausse alerte crédible, sur le canal même qui sert aux vraies.

La règle est désormais APPLIQUÉE, pas déclarée : toute sortie réseau lève.
Un test qui a besoin d'un appel doit le simuler explicitement.
"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tg  # noqa: E402  (après l'ajout du chemin)


class SortieReseauInterdite(RuntimeError):
    """Levée quand un test tente de joindre le monde extérieur."""


@pytest.fixture(autouse=True)
def _coupe_circuit_reseau(monkeypatch):
    """Aucune sortie réseau depuis les tests — Telegram en premier.

    `autouse` : s'applique à TOUS les tests sans qu'ils aient à y penser. C'est
    le seul niveau qui tienne, puisque l'oubli vient précisément de ce qu'on
    n'y pense pas.
    """
    def refus(*args, **kwargs):
        cible = args[0] if args else kwargs.get("url", "?")
        raise SortieReseauInterdite(
            f"appel réseau interdit dans les tests (« {cible} »). "
            f"Simule-le avec monkeypatch plutôt que de le laisser sortir."
        )

    for nom in ("get", "post", "put", "patch", "delete", "head", "request"):
        monkeypatch.setattr(requests, nom, refus, raising=False)
    monkeypatch.setattr(requests.Session, "request", refus, raising=False)

    # Telegram : neutralisé À LA SOURCE, sans même tenter le POST. Un test qui
    # veut observer les messages remplace `telegram_bot.send` de son côté ;
    # ici on garantit seulement que rien ne PART.
    envoyes: list[str] = []
    monkeypatch.setattr(tg, "send", lambda text, chat_id=None: envoyes.append(text) or True)
    monkeypatch.setattr(tg, "send_photo", lambda *a, **k: True)
    monkeypatch.setattr(tg, "send_editable", lambda *a, **k: None)
    monkeypatch.setattr(tg, "edit_message", lambda *a, **k: True)
    monkeypatch.setattr(tg, "delete_message", lambda *a, **k: True)
    monkeypatch.setattr(tg, "get_updates", lambda *a, **k: None)
    return envoyes
