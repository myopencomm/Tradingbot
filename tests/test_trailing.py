"""Trailing stop — les deux paliers.

`trailing_target` est la SOURCE UNIQUE partagée par le trailing réel sur BD et
par l'alerte en mode déconnecté : les deux doivent viser exactement le même SL.
Un écart ferait replacer sur BD un stop différent de celui annoncé.
"""
import autonomous_engine as ae
from config import BREAKEVEN_THRESHOLD

AUTO   = {"entry_price": 100.0, "autonomous": True}   # seuil = AUTO_BREAKEVEN_PCT
MANUEL = {"entry_price": 100.0}                       # seuil = BREAKEVEN_THRESHOLD

# Les seuils sont réglables par .env : les tests raisonnent sur les valeurs
# effectives, pas sur des littéraux qui casseraient au premier ajustement.
SEUIL_AUTO   = ae.BREAKEVEN_PCT
SEUIL_MANUEL = BREAKEVEN_THRESHOLD


class TestTpProgress:
    def test_chemin_parcouru_vers_le_tp(self):
        assert ae.tp_progress(100, 125, 100) == 0.0
        assert ae.tp_progress(100, 125, 115) == 0.6
        assert ae.tp_progress(100, 125, 125) == 1.0

    def test_sans_tp_pas_de_progression(self):
        assert ae.tp_progress(100, None, 115) is None


class TestPalier1Breakeven:
    def test_sous_le_seuil_aucun_stop(self):
        assert ae.trailing_target(AUTO, 100 + SEUIL_AUTO - 1, 125.0)[0] is None

    def test_au_seuil_sl_au_pru(self):
        target, step, label = ae.trailing_target(AUTO, 100 + SEUIL_AUTO, 125.0)
        assert (target, step) == (100.0, "breakeven")
        assert label == "SL au PRU"

    def test_seuil_autonome_plus_haut_que_le_manuel(self):
        """Le backtest a montré qu'à +3% le trail scratchait les futurs
        gagnants : le seuil autonome est délibérément au-dessus du manuel."""
        assert SEUIL_AUTO > SEUIL_MANUEL
        entre = 100 + (SEUIL_AUTO + SEUIL_MANUEL) / 2
        assert ae.trailing_target(AUTO,   entre, 125.0)[0] is None
        assert ae.trailing_target(MANUEL, entre, 125.0)[0] == 100.0


class TestPalier2Securisation:
    def test_avant_60pct_du_chemin_on_reste_au_pru(self):
        assert ae.trailing_target(AUTO, 110.0, 125.0)[:2] == (100.0, "breakeven")

    def test_apres_le_declenchement_le_sl_passe_au_dessus_du_pru(self):
        target, step, label = ae.trailing_target(AUTO, 118.0, 125.0)
        assert step == "lock"
        assert target > 100.0
        assert "verrouillé" in label

    def test_la_fraction_verrouillee_grandit_avec_la_progression(self):
        proche = ae.trailing_target(AUTO, 124.0, 125.0)[0]
        loin   = ae.trailing_target(AUTO, 118.0, 125.0)[0]
        assert proche > loin

    def test_le_sl_garde_toujours_une_marge_sous_le_cours(self):
        """Sans marge, le bruit ordinaire sortirait la position juste avant le
        TP — exactement ce que ce palier cherche à éviter."""
        price = 124.0
        target = ae.trailing_target(AUTO, price, 125.0)[0]
        assert target <= price * (1 - 2.0 / 100)     # TRAIL_MIN_BUFFER_PCT

    def test_l_atr_elargit_la_marge(self):
        serre = ae.trailing_target(AUTO, 124.0, 125.0, atr_pct=0)[0]
        large = ae.trailing_target(AUTO, 124.0, 125.0, atr_pct=8.0)[0]
        assert large < serre

    def test_jamais_au_niveau_du_tp(self):
        target = ae.trailing_target(AUTO, 124.9, 125.0)[0]
        assert target < 125.0

    def test_au_dela_du_tp_plus_de_palier2(self):
        """Cours au-dessus du TP : la sortie est l'affaire du TP, pas du trail."""
        assert ae.trailing_target(AUTO, 130.0, 125.0)[1] == "breakeven"


class TestGardeFous:
    def test_sans_pru_ni_cours_rien(self):
        assert ae.trailing_target({}, 110.0, 125.0)[0] is None
        assert ae.trailing_target(AUTO, 0, 125.0)[0] is None

    def test_sans_tp_le_breakeven_fonctionne_quand_meme(self):
        assert ae.trailing_target(AUTO, 110.0, None)[:2] == (100.0, "breakeven")

    def test_le_sl_ne_redescend_jamais_sous_le_pru(self):
        for price in (106, 110, 118, 124, 130):
            t = ae.trailing_target(AUTO, float(price), 125.0)[0]
            assert t is None or t >= 100.0
