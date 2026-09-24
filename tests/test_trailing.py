"""Trailing stop — les deux paliers.

`trailing_target` est la SOURCE UNIQUE partagée par le trailing réel sur BD et
par l'alerte en mode déconnecté : les deux doivent viser exactement le même SL.
Un écart ferait replacer sur BD un stop différent de celui annoncé.
"""
import pytest

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


class TestSeuilDeRemontee:
    """Une remontée du SL vaut-elle son risque ?

    Chaque remontée annule les DEUX ordres BD (le SL et le TP) avant d'en
    reposer un : entre les deux la position n'a AUCUNE protection, et cette
    fenêtre a déjà laissé une position à nu (UNA, 28/07/2026).

    Le coût a deux composantes, d'où deux seuils. En euros seuls, une ligne de
    5 000 € ratchetterait à chaque frémissement ; en pourcentage seul, une
    remontée qui vaut 40 € sur cette même ligne serait refusée parce qu'elle ne
    fait que 0,8 %.
    """

    PETITE = {"ticker": "XX.PA", "entry_price": 200.0, "qty": 5}      # 1 000 €
    GROSSE = {"ticker": "XX.PA", "entry_price": 200.0, "qty": 25}     # 5 000 €

    def test_le_seuil_euro_bloque_les_cacahuetes(self):
        """+1,25 € sur une petite ligne : on ne joue pas une avarie pour ça."""
        ok, why = ae.trailing.raise_worth_it(self.PETITE, 205.0, 205.25, 5)
        assert not ok and "€ de plus" in why

    def test_le_seuil_euro_laisse_passer_ce_qui_compte(self):
        ok, why = ae.trailing.raise_worth_it(self.PETITE, 205.0, 207.0, 5)
        assert ok and "verrouille" in why

    def test_le_seuil_pourcent_bloque_le_ratchet_permanent(self):
        """+6,25 € sur 5 000 € ne fait que 0,12 % : trop fin pour découvrir la
        position à chaque frémissement du cours."""
        ok, why = ae.trailing.raise_worth_it(self.GROSSE, 205.0, 205.25, 25)
        assert not ok and "% du PRU" in why

    def test_une_grosse_ligne_ratchete_plus_finement_qu_une_petite(self):
        """LE point du seuil proportionnel : 0,5 €/titre vaut 12,50 € sur la
        grosse ligne et seulement 2,50 € sur la petite."""
        assert ae.trailing.raise_worth_it(self.GROSSE, 205.0, 205.5, 25)[0]
        assert not ae.trailing.raise_worth_it(self.PETITE, 205.0, 205.5, 5)[0]

    def test_le_gain_est_converti_en_euros(self):
        """Le SL d'une position US est en dollars : le comparer tel quel au
        seuil surestimerait l'enjeu d'environ 15 %."""
        import prices
        us = {"ticker": "NVDA", "entry_price": 200.0, "qty": 5}
        eu = {"ticker": "XX.PA", "entry_price": 200.0, "qty": 5}
        fx = prices.fx_to_eur("USD")
        if fx >= 1.0:
            pytest.skip("taux indisponible — la conversion n'est pas testable")
        # Un delta qui passe tout juste en euros ne doit PAS passer en dollars
        delta = 5.0 / 5 / fx * 1.02
        assert ae.trailing.raise_worth_it(eu, 205.0, 205.0 + 5.0 / 5 * 1.02, 5)[0]
        assert ae.trailing.raise_worth_it(us, 205.0, 205.0 + delta, 5)[0]

    def test_une_baisse_n_est_jamais_une_remontee(self):
        """Le SL ne peut que MONTER — invariant du trailing."""
        assert not ae.trailing.raise_worth_it(self.GROSSE, 210.0, 205.0, 25)[0]
        assert not ae.trailing.raise_worth_it(self.GROSSE, 210.0, 210.0, 25)[0]

    def test_sans_sl_actuel_rien_ne_bloque(self):
        """Position sans stop : poser une protection n'a pas à franchir un
        seuil, il n'y a rien à annuler."""
        assert ae.trailing.raise_worth_it(self.PETITE, None, 205.0, 5)[0]

    def test_les_deux_seuils_sont_configurables(self):
        from config import TRAIL_MIN_STEP_EUR, TRAIL_MIN_STEP_PCT
        assert TRAIL_MIN_STEP_EUR > 0 and TRAIL_MIN_STEP_PCT > 0
