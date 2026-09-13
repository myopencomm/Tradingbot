"""Reconnexion après expiration — le premier /connect doit marcher.

Signalé le 13/09/2026, capture Telegram à l'appui : après « Session Bourse
Direct expirée », le premier /connect répondait « Locator.click: Timeout
30000ms exceeded — waiting for input[placeholder=Identifiant] ». Le second
passait. Un échec systématique sur deux, et un message d'erreur brut illisible
sur un téléphone.

Cause : à l'expiration, le keepalive ne faisait que poser `_connected_at =
None`. Chromium restait ouvert sur la page de redirection BD, cookies périmés
inclus ; `login()` réutilisait cette page, où le formulaire n'est pas rendu.

Deux verrous, à deux niveaux :
  1. le keepalive FERME le navigateur — le /connect suivant repart d'un
     contexte vierge ;
  2. `login()` se rattrape seul si le formulaire manque quand même (une session
     peut mourir entre deux pings du keepalive).
"""
import pytest

import bourse_direct_auth as auth
import playwright_session as ps


class FauxLocator:
    def __init__(self, visible: bool):
        self._visible = visible
        self.clicked = False
        self.typed = None

    def wait_for(self, state=None, timeout=None):
        if not self._visible:
            raise TimeoutError("locator not visible")

    def click(self, timeout=None):
        self.clicked = True

    def type(self, txt, delay=None):
        self.typed = txt

    def count(self):
        return 0


class FauxContext:
    def __init__(self):
        self.cookies_cleared = 0

    def clear_cookies(self):
        self.cookies_cleared += 1


class FauxPage:
    """Page dont le formulaire n'apparaît qu'après N navigations."""

    def __init__(self, formulaire_des_navigation: int = 1):
        self.navigations = 0
        self._seuil = formulaire_des_navigation
        self.context = FauxContext()
        self.url = "https://www.boursedirect.fr/"

    def goto(self, url, wait_until=None, timeout=None):
        self.navigations += 1

    def locator(self, sel):
        if "Identifiant" in sel:
            return FauxLocator(visible=self.navigations >= self._seuil)
        return FauxLocator(visible=False)


class TestFiletDeLogin:
    def test_formulaire_absent_purge_les_cookies_et_reessaie(self, monkeypatch):
        """C'est exactement le cas des cookies périmés : la 1re navigation ne
        rend rien, la 2e — cookies purgés — rend le vrai formulaire."""
        page = FauxPage(formulaire_des_navigation=2)
        monkeypatch.setattr(auth, "_dismiss_popups", lambda p: None)
        monkeypatch.setattr(auth, "BD_LOGIN", "x")
        monkeypatch.setattr(auth, "BD_PASSWORD", "y")
        msgs = []
        auth.login(page, msgs.append)
        # Le filet a bien joué : purge + seconde navigation, et le login n'a
        # PAS abandonné sur « formulaire absent ».
        assert page.context.cookies_cleared == 1
        assert page.navigations == 2
        assert not any("formulaire absent" in m for m in msgs)

    def test_le_champ_est_attendu_pas_clique_a_l_aveugle(self):
        """Un click() sur un locator absent lève au bout de 30 s et le message
        brut partait sur Telegram. On attend, on décide."""
        page = FauxPage(formulaire_des_navigation=99)
        assert auth._champ_identifiant(page, timeout_ms=1) is None

    def test_echec_persistant_rend_un_message_lisible(self, monkeypatch):
        page = FauxPage(formulaire_des_navigation=99)
        monkeypatch.setattr(auth, "_dismiss_popups", lambda p: None)
        monkeypatch.setattr(auth, "BD_LOGIN", "x")
        monkeypatch.setattr(auth, "BD_PASSWORD", "y")
        msgs = []
        assert auth.login(page, msgs.append) is False
        txt = " ".join(msgs)
        assert "Timeout" not in txt and "Locator" not in txt
        assert "formulaire absent" in txt and "/connect" in txt

    def test_les_cookies_sont_bien_purges_avant_le_2e_essai(self, monkeypatch):
        page = FauxPage(formulaire_des_navigation=99)
        monkeypatch.setattr(auth, "_dismiss_popups", lambda p: None)
        monkeypatch.setattr(auth, "BD_LOGIN", "x")
        monkeypatch.setattr(auth, "BD_PASSWORD", "y")
        auth.login(page, lambda m: None)
        assert page.context.cookies_cleared == 1
        assert page.navigations == 2      # 1 initiale + 1 après purge


class TestKeepaliveFermeLeNavigateur:
    def test_expiration_ferme_le_navigateur(self, monkeypatch):
        """Le drapeau ne suffit pas : c'est Chromium ouvert sur une page morte
        qui cassait le /connect suivant."""
        ferme = []
        monkeypatch.setattr(ps, "KEEPALIVE_INTERVAL", 0)
        monkeypatch.setattr(ps, "is_connected", lambda: True)
        monkeypatch.setattr(ps, "run", lambda fn, timeout=None: False)  # session morte
        monkeypatch.setattr(ps, "stop", lambda: ferme.append(True))
        monkeypatch.setattr("tg.send", lambda *a, **k: None)
        ps._keepalive_loop()
        assert ferme == [True]

    def test_session_vivante_ne_ferme_rien(self, monkeypatch):
        ferme, tours = [], {"n": 0}

        def faux_run(fn, timeout=None):
            tours["n"] += 1
            return tours["n"] < 2          # vivante au 1er tour, morte au 2e

        monkeypatch.setattr(ps, "KEEPALIVE_INTERVAL", 0)
        monkeypatch.setattr(ps, "is_connected", lambda: True)
        monkeypatch.setattr(ps, "run", faux_run)
        monkeypatch.setattr(ps, "stop", lambda: ferme.append(True))
        monkeypatch.setattr("tg.send", lambda *a, **k: None)
        ps._keepalive_loop()
        assert tours["n"] == 2 and ferme == [True]

    def test_une_fermeture_qui_echoue_n_empeche_pas_l_alerte(self, monkeypatch):
        """L'utilisateur doit être prévenu même si Chromium refuse de mourir."""
        envois = []

        def stop_qui_casse():
            raise RuntimeError("browser stuck")

        monkeypatch.setattr(ps, "KEEPALIVE_INTERVAL", 0)
        monkeypatch.setattr(ps, "is_connected", lambda: True)
        monkeypatch.setattr(ps, "run", lambda fn, timeout=None: False)
        monkeypatch.setattr(ps, "stop", stop_qui_casse)
        monkeypatch.setattr("tg.send", lambda m, *a, **k: envois.append(m))
        ps._keepalive_loop()
        assert envois and "expirée" in envois[0]
