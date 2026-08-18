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

import config  # noqa: E402  (après l'ajout du chemin)
import tg      # noqa: E402


class SortieReseauInterdite(RuntimeError):
    """Levée quand un test tente de joindre le monde extérieur."""


class EcritureEtatInterdite(RuntimeError):
    """Levée quand un test tente d'écrire un fichier d'état RÉEL."""


@pytest.fixture(autouse=True)
def _etat_en_bac_a_sable(tmp_path, monkeypatch):
    """Aucun test n'écrit dans les fichiers d'état réels.

    INCIDENT DU 18/08/2026 : un test remplaçait `portfolio.load()` par un
    portefeuille fictif — mais pas `portfolio.save()`. `monitor.check_positions`
    écrit (il réarme les drapeaux d'alerte) : il a donc sauvegardé le faux
    portefeuille PAR-DESSUS le vrai. `positions.json` s'est retrouvé réduit à
    une position avec les seuils du test.

    Remplacer `load` sans remplacer `save` est une erreur trop facile à faire
    pour qu'on compte sur la vigilance. Les CHEMINS sont donc redirigés vers un
    dossier temporaire : même un `save()` non simulé n'atteint plus rien de
    réel. Un test qui veut lire l'état le simule, comme avant.

    Frère du coupe-circuit réseau : la même leçon, appliquée au disque.
    """
    bac = tmp_path / "etat"
    bac.mkdir()
    reels = {
        "POSITIONS_PATH": bac / "positions.json",
        "HISTORY_PATH":   bac / "trades_history.json",
    }
    for nom, chemin in reels.items():
        monkeypatch.setattr(config, nom, chemin, raising=False)
    # Les modules ont capturé le chemin à l'import : on les redirige aussi.
    import history
    import portfolio
    monkeypatch.setattr(portfolio, "POSITIONS_PATH", reels["POSITIONS_PATH"])
    monkeypatch.setattr(history, "HISTORY_PATH", reels["HISTORY_PATH"])
    try:
        import api_costs
        monkeypatch.setattr(api_costs, "COSTS_PATH", bac / "api_costs.json")
    except Exception:
        pass
    return bac


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
