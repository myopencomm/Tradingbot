"""Après une vente, le capital doit repartir — sur le marché OUVERT.

Constaté le 14/09/2026 : BAC vendu en séance US, 919€ de cash libérés,
2 emplacements sur 3 occupés. Réponse du moteur : « Aucune opportunité
exploitable ». Il n'avait rien à examiner.

La boucle était fermée sur elle-même :
  emplacements pleins → briefing et scan US SAUTÉS (`entry_capacity_block`,
  pour ne pas brûler 8 validations IA inachetables) → file d'opportunités vide
  → une vente rouvre une place → le cycle d'entrée consomme une file vide →
  rien, jusqu'au briefing du lendemain 9h05.

Et le scan de secours existant ne pouvait pas combler le trou : il tourne sur
SCAN_UNIVERSE (Euronext), dont TOUS les candidats sont écartés par
`market_open_for` à 21h. Scanner le mauvais marché revient à ne pas scanner.
"""
import time

import pytest

import autonomous_engine as ae


@pytest.fixture
def moteur(monkeypatch):
    """Mode autonome actif, session BD connectée, et tous les appels coûteux
    remplacés par des mouchards."""
    trace = []
    monkeypatch.setattr(ae, "is_enabled", lambda: True)
    monkeypatch.setattr(ae.bot_mode, "is_playwright", lambda: True)
    monkeypatch.setattr(ae.playwright_session, "is_connected", lambda: True)
    monkeypatch.setattr(ae, "run_entry_cycle",
                        lambda send_fn: trace.append("cycle"))
    ae._last_rescan_ts = 0.0

    import analysis
    monkeypatch.setattr(analysis, "scan_us_opportunities",
                        lambda send_fn: trace.append("scan_us"))
    monkeypatch.setattr(analysis, "scan_opportunities",
                        lambda send_fn, **k: trace.append("scan_euronext"))
    return trace


def _capacite(monkeypatch, bloque):
    import sizing
    monkeypatch.setattr(sizing, "entry_capacity_block",
                        lambda *a, **k: bloque)


def _marches(monkeypatch, us: bool, euronext: bool):
    def ouvert(ticker, now=None):
        return us if ticker.upper() == "NVDA" else euronext
    monkeypatch.setattr(ae.market, "is_open_now", ouvert)


class TestOuLeBotCherche:
    def test_seance_us_il_scanne_les_US(self, monkeypatch, moteur):
        """Le cas du 14/09 : 21h, seul Wall Street est ouvert."""
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=True, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur == ["cycle", "scan_us"]

    def test_seance_euronext_il_scanne_euronext(self, monkeypatch, moteur):
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=False, euronext=True)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur == ["cycle", "scan_euronext"]

    def test_tout_ferme_il_ne_scanne_rien(self, monkeypatch, moteur):
        """Des candidats dont le marché rouvre demain seraient écartés un par
        un par le cycle d'entrée — autant ne pas payer les validations IA."""
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=False, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur == ["cycle"]


class TestQuandIlSAbstient:
    def test_le_cycle_vient_d_acheter_plus_rien_a_chercher(self, monkeypatch, moteur):
        """C'est l'ÉTAT qui répond « a-t-il acheté ? », pas une valeur de
        retour : si la capacité est reprise, on n'enchaîne pas sur un scan."""
        _capacite(monkeypatch, "3/3 emplacements occupés")
        _marches(monkeypatch, us=True, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur == ["cycle"]

    def test_deux_ventes_rapprochees_ne_paient_pas_deux_scans(self, monkeypatch, moteur):
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=True, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur.count("scan_us") == 1

    def test_le_cooldown_expire_rouvre_la_recherche(self, monkeypatch, moteur):
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=True, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        ae._last_rescan_ts = time.time() - ae._RESCAN_COOLDOWN - 1
        ae._chercher_apres_vente(lambda m: None)
        assert moteur.count("scan_us") == 2

    def test_mode_autonome_off_aucun_thread(self, monkeypatch, moteur):
        monkeypatch.setattr(ae, "is_enabled", lambda: False)
        ae.relancer_apres_vente(lambda m: None)
        assert moteur == []

    def test_session_bd_deconnectee_aucun_thread(self, monkeypatch, moteur):
        monkeypatch.setattr(ae.playwright_session, "is_connected", lambda: False)
        ae.relancer_apres_vente(lambda m: None)
        assert moteur == []


class TestRobustesse:
    def test_un_cycle_qui_casse_n_empeche_pas_la_recherche(self, monkeypatch, moteur):
        def cycle_qui_casse(send_fn):
            trace_err = RuntimeError("BD indisponible")
            raise trace_err
        monkeypatch.setattr(ae, "run_entry_cycle", cycle_qui_casse)
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=True, euronext=False)
        ae._chercher_apres_vente(lambda m: None)
        assert moteur == ["scan_us"]

    def test_l_utilisateur_est_prevenu_de_la_recherche(self, monkeypatch, moteur):
        _capacite(monkeypatch, None)
        _marches(monkeypatch, us=True, euronext=False)
        msgs = []
        ae._chercher_apres_vente(msgs.append)
        assert msgs and "US" in msgs[0]


class TestCablage:
    """Le hook doit être réellement branché — sinon tout ce qui précède ne
    s'exécute jamais en production."""

    def test_le_hook_est_enregistre_a_l_import(self):
        import analysis
        import autonomous_engine  # noqa: F401 — son import pose le hook
        assert analysis._hook_after_sale is ae.relancer_apres_vente

    def test_le_sync_passe_par_le_hook_pas_par_un_import_direct(self):
        """`autonomous_engine` importe `sync_engine` : l'importer en retour
        recréerait le cycle que test_market interdit."""
        import inspect
        import sync_engine
        src = inspect.getsource(sync_engine.sync)
        assert "_trigger_after_sale" in src
        assert "import autonomous_engine" not in src

    def test_une_vente_detectee_declenche_le_hook(self, monkeypatch):
        import analysis
        appels = []
        monkeypatch.setattr(analysis, "_hook_after_sale",
                            lambda send_fn: appels.append("relance"))
        analysis._trigger_after_sale(lambda m: None)
        assert appels == ["relance"]
