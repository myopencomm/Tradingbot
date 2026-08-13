"""Watchdog du scheduler : distinguer « lent » de « bloqué ».

Le watchdog existe à cause de l'incident du 21-23/07/2026 — un scan bloqué à
21h40 a figé TOUT le scheduler pendant ~36 h sans un message d'erreur. Il libère
donc le scheduler au bout d'un délai.

Mais dépasser le délai ne prouve pas un blocage. Le 13/08/2026, le briefing a
été déclaré « bloqué » alors qu'il a terminé deux minutes plus tard : la sortie
de NVDA au TP avait libéré une place, donc le briefing a fait le chemin COMPLET
(screen quant + 6 validations avec vision) au lieu du chemin court — 24 appels
IA au lieu d'un. L'alerte affirmait « celui-ci n'a pas terminé » à propos d'un
job terminé, et le watchdog ne revenait jamais vérifier.
"""
import threading
import time

import pytest

import main


@pytest.fixture
def messages(monkeypatch):
    """Capture les messages Telegram au lieu de les envoyer.

    Renvoie un filtre PAR NOM DE JOB : les threads de surveillance survivent au
    test qui les a lancés (c'est tout leur intérêt — ils reviennent plus tard),
    donc leurs messages atterrissent dans la capture du test suivant. Chaque
    test emploie un nom de job distinct et ne lit que le sien.
    """
    recus = []
    monkeypatch.setattr(main.telegram_bot, "send", lambda t, *a, **k: recus.append(t))

    def pour(job: str) -> list[str]:
        return [m for m in recus if f"« {job} »" in m]

    pour.tous = recus
    return pour


def _attendre(predicat, limite=5.0):
    fin = time.time() + limite
    while time.time() < fin:
        if predicat():
            return True
        time.sleep(0.02)
    return False


class TestBudgetsParJob:
    def test_le_briefing_a_plus_de_temps_que_les_checks(self):
        """Le chemin complet du briefing (24 appels IA) ne tient pas dans le
        budget d'un check SL/TP, qui n'appelle aucune IA."""
        assert main.JOB_TIMEOUTS["briefing"] > main.JOB_TIMEOUT_DEFAUT

    def test_tous_les_jobs_a_ia_ont_un_budget_etendu(self):
        """briefing, us_scan et weekly_swap ont tous déjà dépassé 240 s."""
        for job in ("briefing", "us_scan", "weekly_swap"):
            assert main.JOB_TIMEOUTS[job] >= 900

    def test_le_defaut_reste_serre(self):
        """Un check ou un sync BD qui traîne 4 minutes EST une anomalie."""
        assert main.JOB_TIMEOUT_DEFAUT == 240


class TestJobNormal:
    def test_aucun_message_si_le_job_finit_a_temps(self, messages):
        fait = threading.Event()
        main._bounded(fait.set, "rapide", timeout=5)()
        assert _attendre(fait.is_set)
        time.sleep(0.1)
        assert messages("rapide") == []

    def test_une_exception_n_alerte_pas_le_watchdog(self, messages):
        """Une erreur applicative se trace dans le log ; ce n'est pas un
        blocage du scheduler."""
        def boum():
            raise ValueError("boum")
        main._bounded(boum, "casse", timeout=5)()
        time.sleep(0.2)
        assert messages("casse") == []


class TestJobLent:
    def test_le_scheduler_est_libere_sans_attendre_la_fin(self, messages):
        """Le point vital : la fonction rend la main au délai, pas à la fin du
        job — sinon un job bloqué gèlerait toute la surveillance (incident du
        21-23/07/2026, ~36 h de scheduler figé)."""
        debut = time.time()
        main._bounded(lambda: time.sleep(1.5), "libere", timeout=0.2)()
        assert time.time() - debut < 1.0

    def test_l_alerte_ne_pretend_plus_que_le_job_a_echoue(self, messages):
        main._bounded(lambda: time.sleep(0.4), "prudent", timeout=0.1)()
        assert _attendre(lambda: messages("prudent"))
        alerte = messages("prudent")[0]
        assert "dépasse" in alerte
        assert "n'a pas terminé" not in alerte, \
            "le watchdog ne peut pas savoir ça au moment où il crie"

    def test_le_watchdog_revient_annoncer_la_fin(self, messages):
        """Ce qui manquait : l'alerte restait un mensonge dans l'historique —
        le 13/08 elle affirmait « n'a pas terminé » sur un job terminé."""
        main._bounded(lambda: time.sleep(0.4), "revient", timeout=0.1)()
        assert _attendre(lambda: len(messages("revient")) >= 2)
        assert "finalement terminé" in messages("revient")[1]

    def test_un_vrai_blocage_est_annonce_autrement(self, monkeypatch, messages):
        """Au-delà du délai de grâce, là c'est vraiment anormal — et le message
        ne dit pas la même chose que pour un job simplement lent."""
        monkeypatch.setattr(main, "JOB_GRACE_S", 0.2)
        bloque = threading.Event()
        main._bounded(lambda: bloque.wait(30), "fige", timeout=0.1)()
        assert _attendre(lambda: len(messages("fige")) >= 2)
        suite = messages("fige")[1]
        assert "toujours en cours" in suite
        assert "finalement terminé" not in suite
        bloque.set()          # libère le thread de test
