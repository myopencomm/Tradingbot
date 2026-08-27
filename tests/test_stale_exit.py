"""Sortie sur stagnation — le capital doit tourner, jamais à perte.

Demandé le 27/08/2026, sur constat chiffré : la durée de détention était
mesurée partout (/stats, dashboard, €/jour) et n'entrait dans AUCUNE décision.
BAC dormait depuis 28 jours pour -2%, Carrefour depuis 8 jours pour -2.6%.

Deux règles à figer, et la seconde prime toujours sur la première :
  1. une position doit avoir parcouru X% du chemin PRU→TP à J+N, sinon elle
     stagne et son capital repart ;
  2. une vente sur stagnation ne matérialise JAMAIS une perte — le seuil n'est
     pas le PRU mais le PRU + les frais de SORTIE, sans quoi vendre « à
     l'équilibre » perdrait le courtage et la commission de change.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

import config
import stale_exit


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _ouvert_il_y_a(jours_bourse: int) -> str:
    """Date d'ouverture située `jours_bourse` séances avant NOW.

    Les jalons se comptent en JOURS DE BOURSE — les exprimer en calendaire
    dans les tests décalerait chaque seuil d'environ 40%.
    """
    d = np.busday_offset(np.datetime64(NOW.date()), -jours_bourse, roll="backward")
    return datetime.combine(d.astype("datetime64[D]").astype(object),
                            datetime.min.time(), tzinfo=timezone.utc).isoformat()


def _pos(jours=30, entry=100.0, tp=110.0, qty=10, ticker="CA.PA", **kw):
    """`jours` = jours de BOURSE de détention."""
    p = {"ticker": ticker, "qty": qty, "entry_price": entry,
         "target_low": entry * 0.95, "target_high": tp,
         "opened_at": _ouvert_il_y_a(jours)}
    p.update(kw)
    return p


class TestJalons:
    def test_le_jalon_par_defaut_s_applique_a_partir_de_25_seances(self):
        assert stale_exit.required_progress(30) == (config.STALE_DAYS_1,
                                                    config.STALE_PROGRESS_1)

    def test_avant_le_premier_jalon_aucune_exigence(self):
        assert stale_exit.required_progress(3) is None

    def test_un_jalon_a_zero_jour_est_desactive_pas_immediat(self):
        """Le second jalon vaut 0 par défaut : il ne doit PAS se déclencher dès
        l'ouverture, sinon toute position neuve serait jugée stagnante."""
        assert config.STALE_DAYS_2 == 0
        assert stale_exit.required_progress(0) is None

    def test_le_plus_exigeant_des_jalons_franchis_l_emporte(self, monkeypatch):
        monkeypatch.setattr(config, "STALE_DAYS_2", 40.0)
        monkeypatch.setattr(config, "STALE_PROGRESS_2", 60.0)
        assert stale_exit.required_progress(45) == (40.0, 60.0)

    def test_chemin_pru_vers_tp(self):
        assert stale_exit.tp_path_pct(_pos(entry=100, tp=110), 105) == 50.0

    def test_chemin_negatif_sous_le_pru(self):
        assert stale_exit.tp_path_pct(_pos(entry=100, tp=110), 98) == -20.0


class TestVerdict:
    def test_position_jeune_on_ne_touche_a_rien(self):
        action, why = stale_exit.verdict(_pos(jours=3), 100.5, NOW)
        assert action == "garder" and "premier jalon" in why

    def test_position_dans_les_temps(self):
        """30 séances mais 40% du chemin fait : elle avance, on la laisse courir."""
        action, why = stale_exit.verdict(_pos(jours=30), 104.0, NOW)
        assert action == "garder" and "dans les temps" in why

    def test_position_stagnante_et_verte_est_vendue(self):
        # +2% en 30 séances = 20% du chemin, sous les 33% exigés.
        action, why = stale_exit.verdict(_pos(jours=30), 102.0, NOW)
        assert action == "vendre" and "stagnante" in why

    def test_position_stagnante_mais_rouge_n_est_PAS_vendue(self):
        """La règle absolue : jamais de perte matérialisée."""
        action, why = stale_exit.verdict(_pos(jours=30), 98.0, NOW)
        assert action == "bloquee" and "point mort" in why

    def test_le_point_mort_est_au_dessus_du_pru_pas_au_pru(self):
        """Vendre au PRU pile perdrait les frais de sortie. Une position pile au
        PRU est donc bloquée, pas vendue."""
        action, _why = stale_exit.verdict(_pos(jours=30), 100.0, NOW)
        assert action == "bloquee"

    def test_sans_date_d_ouverture_aucune_vitesse_donc_aucune_vente(self):
        """Les positions antérieures au suivi de durée ne doivent pas être
        toutes jugées stagnantes le jour de la mise en service."""
        p = _pos(jours=30)
        del p["opened_at"]
        action, why = stale_exit.verdict(p, 102.0, NOW)
        assert action == "garder" and "inconnue" in why

    def test_position_hold_hors_perimetre(self):
        action, _ = stale_exit.verdict(_pos(jours=40, hold=True), 102.0, NOW)
        assert action == "garder"

    def test_sans_tp_rien_a_mesurer(self):
        p = _pos(jours=30); p["target_high"] = 0
        action, why = stale_exit.verdict(p, 102.0, NOW)
        assert action == "garder" and "TP manquant" in why

    def test_interrupteur_off(self, monkeypatch):
        monkeypatch.setattr(config, "STALE_EXIT", False)
        action, why = stale_exit.verdict(_pos(jours=40), 102.0, NOW)
        assert action == "garder" and "désactivée" in why


class TestPointMortReel:
    """Le seuil de vente se calcule sur le barème BD, pas sur une marge ronde."""

    def test_le_point_mort_couvre_le_courtage_euronext(self):
        pm = config.breakeven_price("CA.PA", 16.0123, 75, 1.0)
        assert pm > 16.0123
        # 75 titres à ~16 € = 1 200 € : le courtage de vente seul (pas de TTF à
        # la vente) reste sous 5 € — le point mort ne doit pas s'envoler.
        assert (pm / 16.0123 - 1) * 100 < 1.0

    def test_un_titre_en_devise_paie_aussi_le_change(self):
        """La commission de change (0.08%) s'ajoute au courtage US."""
        us = config.breakeven_price("BAC", 62.8603, 12, 0.865)
        assert us > 62.8603
        assert (us / 62.8603 - 1) * 100 > 1.0   # forfait US 8.50 € sur ~650 €

    def test_le_point_mort_ne_depend_pas_du_sens_du_marche(self):
        """Fonction pure du PRU, de la quantité et de la place."""
        a = config.breakeven_price("BAC", 62.8603, 12, 0.865)
        b = config.breakeven_price("BAC", 62.8603, 12, 0.865)
        assert a == b


class TestCasReels2708:
    """Les trois positions du jour, au 27/08/2026 — 20, 10 et 6 séances.

    Aucune n'est encore vendable au réglage calibré (jalon à 25 séances). La
    règle ne libère donc RIEN aujourd'hui : c'est le prix du réglage le moins
    coûteux, et il vaut mieux le figer que le découvrir en production.
    """

    def test_bac_pas_encore_au_jalon(self):
        bac = _pos(jours=20, entry=62.8603, tp=67.53, qty=12, ticker="BAC")
        action, why = stale_exit.verdict(bac, 61.62, NOW, fx=0.865)
        assert action == "garder" and "premier jalon" in why

    def test_bac_au_jalon_reste_bloquee_car_rouge(self):
        """À 25 séances BAC sera stagnante — mais sous son point mort, donc
        conservée. La règle « jamais à perte » ne la libérera pas."""
        bac = _pos(jours=25, entry=62.8603, tp=67.53, qty=12, ticker="BAC")
        action, _why = stale_exit.verdict(bac, 61.62, NOW, fx=0.865)
        assert action == "bloquee"

    def test_carrefour_trop_jeune(self):
        ca = _pos(jours=6, entry=16.0123, tp=17.3, qty=75, ticker="CA.PA")
        action, _why = stale_exit.verdict(ca, 15.60, NOW, fx=1.0)
        assert action == "garder"

    def test_jnj_lente_et_verte_sortira_au_jalon(self):
        """+1% en 10 séances = 12% du chemin. Arrivée à 25 séances sans avoir
        avancé, elle sort — au-dessus du point mort, donc sans perte."""
        jnj = _pos(jours=25, entry=264.0162, tp=286.95, qty=5, ticker="JNJ")
        action, why = stale_exit.verdict(jnj, 266.68, NOW, fx=0.865)
        assert action == "vendre", why
