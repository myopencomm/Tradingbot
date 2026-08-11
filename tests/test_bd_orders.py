"""Construction des ordres BD : validité et pas de cotation.

`parse_validity` encode une contrainte découverte à l'usage : BD refuse une
révocation SANS date de fin de mois (`validityDate` n'est jamais null), et
`end_of_year` n'existe que sur Euronext. Une erreur ici est renvoyée par BD
sous forme de message opaque — d'où l'intérêt de la figer.
"""
import calendar
from datetime import datetime

import bourse_direct_orders as bd


def _fin_de_mois() -> str:
    now = datetime.now()
    last = calendar.monthrange(now.year, now.month)[1]
    return f"{now.year}-{now.month:02d}-{last:02d}T00:00:00.000Z"


class TestValidity:
    def test_seance(self):
        v, d = bd.parse_validity("seance", "XPAR")
        assert v == "day"
        assert d == datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")

    def test_max_sur_euronext_va_a_la_fin_d_annee(self):
        v, d = bd.parse_validity("max", "XPAR")
        assert v == "end_of_year"
        assert d == f"{datetime.now().year}-12-31T00:00:00.000Z"

    def test_max_hors_euronext_devient_revocation_fin_de_mois(self):
        """C'est LA contrainte qui bloquait les ordres US : une révocation sans
        validityDate de fin de mois est refusée par BD."""
        for mic in ("XNGS", "XNYS", "XNAS"):
            v, d = bd.parse_validity("max", mic)
            assert v == "revocation"
            assert d == _fin_de_mois()

    def test_revocation_explicite_porte_toujours_une_date(self):
        v, d = bd.parse_validity("revocation", "XNGS")
        assert (v, d) == ("revocation", _fin_de_mois())

    def test_date_precise(self):
        assert bd.parse_validity("31/12/2026", "XPAR") == (
            "date", "2026-12-31T00:00:00.000Z")

    def test_end_of_year_impossible_hors_euronext(self):
        assert bd.parse_validity("end_of_year", "XNGS")[0] == "revocation"

    def test_defaut_vide_equivaut_a_max(self):
        assert bd.parse_validity("", "XPAR") == bd.parse_validity("max", "XPAR")

    def test_tous_les_euronext_traites_pareil(self):
        for mic in ("XPAR", "XAMS", "XBRU", "XLIS"):
            assert bd.parse_validity("max", mic)[0] == "end_of_year"


class TestPasDeCotation:
    def test_arrondi_dans_les_trois_sens(self):
        assert bd._round_to_tick(209.673, 0.02, "up") == 209.68
        assert bd._round_to_tick(209.673, 0.02, "down") == 209.66
        assert bd._round_to_tick(209.673, 0.02, "nearest") == 209.68

    def test_prix_deja_sur_le_pas_reste_intact(self):
        """Sans la tolérance epsilon, un prix déjà valide serait poussé d'un
        cran — et le trailing annulerait/reposerait la protection en boucle."""
        assert bd._round_to_tick(209.70, 0.02, "up") == 209.70
        assert bd._round_to_tick(209.70, 0.02, "down") == 209.70
