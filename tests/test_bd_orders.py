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


class TestOrdreRejete:
    """Un ordre accepté puis REJETÉ par BD doit se voir.

    `/order/create` répond 200 avec un id et ses deux jambes de protection —
    puis BD peut refuser l'ordre après coup. Le 18/08/2026, RTX : le bot a
    annoncé « ✅ ORDRE AUTONOME PLACÉ SUR BD », crédité l'engagement au budget,
    et n'est jamais revenu vérifier. Le rejet a été découvert sur le téléphone.

    Le parseur n'avait aucun cas « Rejeté » : le statut restait à None, donc
    invisible de bout en bout.
    """

    REJETE = ("RTX Corporation(XNYS) | XNYS › RTX | 224.090 USD | +1.10 % |  |  | \t | "
              "Achat(CPT)\tRejeté\t0/6\tLim. 224.431 $US\t-\t31/08/2026 à 22:00:00 | "
              "\tTake Profit\tSeuil215.00 $US\t\tProfit248.50 $US\t\t")

    def _row(self, s):
        return s.replace(" | ", "\n")

    def test_le_rejet_est_lu(self):
        import bourse_direct_reader as reader
        o = reader._parse_order(self._row(self.REJETE))
        assert o["statut"] == "Rejeté"
        assert (o["qty_exec"], o["qty_total"]) == (0, 6)

    def test_le_rejet_prime_sur_les_jambes_de_protection(self):
        """Le bloc contient « Take Profit », « Seuil » et « Profit » : sans
        priorité au rejet, un mot comme « en cours » sur une jambe suffirait à
        faire passer l'ordre pour vivant."""
        import bourse_direct_reader as reader
        avec_jambes = self.REJETE.replace(
            "Seuil215.00 $US\t\t", "Seuil215.00 $US\tEn cours\t")
        assert reader._parse_order(self._row(avec_jambes))["statut"] == "Rejeté"

    def test_un_ordre_vivant_reste_en_cours(self):
        import bourse_direct_reader as reader
        vivant = ("Bank of America Corporation(XNYS) | XNYS › BAC | 64.330 USD | +0.68 % |  |  | \t | "
                  "Vente(CPT)\t\t0/12\t\t-\t31/08/2026 à 22:00:00 | "
                  "\tTake Profit\tSeuil58.93 $US\tEn cours\tProfit67.53 $US\tEn cours\t")
        assert reader._parse_order(self._row(vivant))["statut"] == "En cours"

    def test_un_ordre_rejete_n_est_pas_compte_comme_actif(self):
        """Le sync ne retient que « En cours » : un rejet ne doit jamais passer
        pour une protection active."""
        import bourse_direct_reader as reader
        o = reader._parse_order(self._row(self.REJETE))
        assert o["statut"] != "En cours"


class TestPasDeCotationUS:
    """Les actions américaines se traitent au cent.

    INCIDENT DU 18/08/2026 : RTX envoyé à 224.431 $ — trois décimales. BD a
    répondu 200 (`create_order OK`), puis le NYSE a refusé. Le carnet légal le
    dit mot pour mot : « Achat rejeté marché ». Ni l'app ni l'API ne donnent le
    motif, et le bouton d'annulation renvoie « Une erreur s'est produite ».

    Le bot comptait sur la validation de BD, qui renvoie bien un 400 « Le pas de
    cotation pour cette limite est 0.01 »… mais pas toujours : JNJ l'avait eu la
    veille et s'était exécuté après arrondi, RTX ne l'a jamais eu. Une garantie
    qui ne se déclenche qu'une fois sur deux n'en est pas une.
    """

    def test_un_prix_us_a_trois_decimales_est_arrondi(self):
        """LE cas RTX : 224.431 n'est pas un prix traitable au NYSE."""
        assert bd._round_to_tick(224.431, 0.01, "down") == 224.43

    def test_l_achat_arrondit_vers_le_bas_la_vente_vers_le_haut(self):
        """Conservateur : on ne paie jamais plus cher que voulu à l'achat, on
        ne vend jamais moins cher que voulu."""
        assert bd._round_to_tick(224.431, 0.01, "down") == 224.43   # achat
        assert bd._round_to_tick(224.431, 0.01, "up") == 224.44     # vente

    def test_un_prix_deja_au_cent_ne_bouge_pas(self):
        """JNJ après arrondi valait 262.29 — un second passage doit le laisser
        intact, sinon chaque ordre dériverait d'un cent."""
        for p in (262.29, 215.0, 248.5, 286.95):
            assert bd._round_to_tick(p, 0.01, "down") == p
            assert bd._round_to_tick(p, 0.01, "up") == p

    def test_le_stop_monte_et_le_profit_descend(self):
        """Arrondir un SL vers le bas l'éloignerait de la protection voulue ;
        arrondir un TP vers le haut le rendrait plus dur à atteindre."""
        assert bd._round_to_tick(215.004, 0.01, "up") == 215.01
        assert bd._round_to_tick(248.506, 0.01, "down") == 248.5

    def test_les_actions_sous_un_dollar_ont_un_pas_plus_fin(self):
        """SEC Rule 612 : 0.0001 sous le dollar."""
        assert bd._round_to_tick(0.87654, 0.0001, "down") == 0.8765
