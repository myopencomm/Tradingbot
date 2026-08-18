"""Vérification qu'un ordre passé sur BD a bien survécu.

INCIDENT DU 18/08/2026 : `/order/create` a répondu 200 pour RTX, avec un id et
ses deux jambes de protection. Le bot a annoncé « ✅ ORDRE AUTONOME PLACÉ SUR
BD », crédité l'engagement au budget… puis BD a rejeté l'ordre. Personne n'est
revenu vérifier — le rejet a été découvert sur le téléphone, plusieurs heures
après.

Annoncer un succès sans en contrôler l'issue : la même faute que l'alerte du
watchdog qui ne revenait jamais sur son verdict.
"""
import pytest

import portfolio
import sync_engine


def _ordre(statut, oid="65e26f4b", ticker="RTX"):
    o = {"bd_ticker": ticker, "name": "RTX Corporation", "order_id": oid,
         "order_ids": [oid], "sens": "Achat"}
    if statut:
        o["statut"] = statut
    return o


@pytest.fixture
def verif(monkeypatch):
    """Lance la vérification SANS attendre le délai réel."""
    envoyes, liberes = [], []
    monkeypatch.setattr(portfolio, "clear_auto_pending_order", liberes.append)

    def run(orders, orders_read=True, oid="65e26f4b"):
        import playwright_session
        monkeypatch.setattr(playwright_session, "run",
                            lambda fn, timeout=None: {"orders": orders,
                                                      "orders_read": orders_read})
        import threading
        # Timer(0) plutôt qu'un vrai délai : on teste la logique, pas l'attente.
        vrai = threading.Timer
        monkeypatch.setattr(threading, "Timer",
                            lambda d, f: vrai(0, f))
        sync_engine.schedule_order_verification(oid, "RTX", envoyes.append)
        import time
        time.sleep(0.3)
        return envoyes, liberes

    return run


class TestRejet:
    def test_le_rejet_est_annonce(self, verif):
        envoyes, _ = verif([_ordre("Rejeté")])
        assert envoyes, "un rejet doit être annoncé"
        assert "REJETÉ" in envoyes[0]
        assert "aucun titre" in envoyes[0]

    def test_le_budget_est_libere(self, verif):
        """Sans ça, l'engagement gèlerait une place jusqu'à l'expiration."""
        _envoyes, liberes = verif([_ordre("Rejeté")])
        assert liberes == ["RTX"]

    def test_le_message_parle_des_jambes_orphelines(self, verif):
        """Les deux protections restent affichées « en cours » côté BD alors
        qu'elles n'ont aucun titre à vendre — c'est ce qui a dérouté."""
        envoyes, _ = verif([_ordre("Rejeté")])
        assert "annule l'ordre depuis l'app" in envoyes[0]


class TestPasDeFausseAlerte:
    def test_un_ordre_vivant_ne_declenche_rien(self, verif):
        envoyes, liberes = verif([_ordre("En cours")])
        assert envoyes == [] and liberes == []

    def test_un_ordre_execute_ne_declenche_rien(self, verif):
        envoyes, liberes = verif([_ordre("Exécuté")])
        assert envoyes == [] and liberes == []

    def test_ordres_non_lus_aucune_conclusion(self, verif):
        """Même règle que le contrôle de protection du 11/08 : une liste vide
        n'est pas une preuve d'absence."""
        envoyes, liberes = verif([], orders_read=False)
        assert envoyes == [] and liberes == []

    def test_ordre_introuvable_on_se_tait(self, verif):
        """Absent du carnet : peut-être exécuté et déjà soldé. Le sync
        tranchera — on n'invente pas un rejet."""
        envoyes, liberes = verif([], orders_read=True)
        assert envoyes == [] and liberes == []

    def test_un_autre_ticker_rejete_ne_nous_concerne_pas(self, verif):
        envoyes, _ = verif([_ordre("Rejeté", oid="autre", ticker="ZZZ")])
        assert envoyes == []
