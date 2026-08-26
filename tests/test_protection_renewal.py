"""Repose des protections expirées — non-régression du trou BAC (31/07→05/08).

Ce jour-là, une position ouverte le 30/07 s'est retrouvée sans stop dès le 31/07
à 22h : l'Expert d'achat qui portait ses protections avait atteint son échéance,
et BD ne prolonge rien. Personne ne l'a vu pendant 5 séances.

Ces tests figent les quatre règles du correctif :
  1. l'échéance d'un ordre est LUE au carnet et mémorisée tant qu'elle est
     visible — une fois l'ordre expiré, la date n'existe plus nulle part ;
  2. on ne repose JAMAIS avant la bascule du mois : sur un titre US « max » rend
     la même fin de mois, la repose n'allongerait rien ;
  3. sans échéance mémorisée, pas de repose (on retombe sur l'alerte du sync) ;
  4. une protection encore présente au carnet interdit la repose — un doublon de
     vente sur des titres déjà engagés est pire que le trou qu'on répare.

Puis le 26/08 JNJ a perdu la sienne SIX JOURS avant l'échéance, autour de son
détachement de dividende du 25/08 : le bot n'attendait le trou que le 31 et
s'est contenté de le signaler. D'où le second déclencheur, qui ne regarde plus
la cause mais la DURÉE (`naked_since`), et sa règle propre :
  5. un trou doit TENIR NAKED_CONFIRM_MINUTES avant d'être réparé — un trou qui
     clignote est une lecture ratée, pas une position à nu.
"""
from datetime import date, datetime

import pytest

import bourse_direct_orders as bd_orders
import bourse_direct_reader as reader
import protection_renewal as pr


def _pos(**kw):
    base = {"ticker": "BAC", "qty": 12, "entry_price": 62.86,
            "target_low": 58.93, "target_high": 67.53,
            "bd_name": "Bank of America Corporation"}
    base.update(kw)
    return base


class TestEcheanceBD:
    """La borne de validité dépend du marché — c'est toute l'origine du bug."""

    def test_us_borne_a_la_fin_du_mois_courant(self):
        assert bd_orders.max_validity_deadline("BAC", datetime(2026, 8, 21)) \
            == date(2026, 8, 31)

    def test_euronext_borne_au_31_decembre(self):
        assert bd_orders.max_validity_deadline("CA.PA", datetime(2026, 8, 21)) \
            == date(2026, 12, 31)

    def test_reposer_un_us_en_cours_de_mois_ne_gagne_rien(self):
        """Le piège : « max » se recalcule depuis le jour de la pose."""
        assert bd_orders.max_validity_deadline("BAC", datetime(2026, 8, 21)) \
            == bd_orders.max_validity_deadline("BAC", datetime(2026, 8, 3))

    def test_apres_la_bascule_du_mois_la_repose_allonge(self):
        assert bd_orders.max_validity_deadline("BAC", datetime(2026, 9, 1)) \
            == date(2026, 9, 30)


class TestLectureEcheance:
    """L'échéance était affichée au carnet et lue par personne."""

    def test_protection_de_vente(self):
        o = reader._parse_order(
            "Bank of America Corporation(XNYS) | XNYS › BAC | 61.86 USD | -2.07 % | "
            "Vente(CPT) 0/12 - 31/08/2026 à 22:00:00 Take Profit "
            "Seuil58.93 $US En cours Profit67.53 $US En cours")
        assert o["validite_iso"] == "2026-08-31"

    def test_protection_portee_par_un_expert_d_achat(self):
        """Cas BAC/JNJ : la date est celle de l'ordre d'ACHAT exécuté."""
        o = reader._parse_order(
            "Johnson & Johnson(XNYS) | XNYS › JNJ | 267.37 USD | -2.21 % | "
            "Achat(CPT) Ordre exécuté 5/5 Lim. 262.29 $US 261.79 $US "
            "31/08/2026 à 22:00:00 Take Profit Seuil248.50 $US En cours "
            "Profit286.95 $US En cours")
        assert o["validite_iso"] == "2026-08-31"

    def test_euronext_expire_a_la_cloture_de_paris(self):
        o = reader._parse_order(
            "Carrefour | XPAR › CA | 15.85 EUR | +0.79 % | Achat(CPT) "
            "Ordre exécuté 75/75 Lim. 15.955 € 15.91 € 31/12/2026 à 17:35:00 "
            "Take Profit Seuil15.05 € En cours Profit17.30 € En cours")
        assert o["validite_iso"] == "2026-12-31"
        assert o["validite_heure"] == "17:35:00"


class TestDeclencheurEcheance:
    """`needs_renewal` est la moitié LOCALE de la décision — pure et testable."""

    def test_protection_encore_valide_on_ne_touche_a_rien(self):
        ok, why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31"), datetime(2026, 8, 21, 10, 0))
        assert not ok and "valide jusqu'au 31/08/2026" in why

    def test_le_jour_meme_de_l_echeance_on_attend_la_cloture(self):
        """L'ordre court jusqu'à 22h : le 31/08, il protège encore."""
        ok, _why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31"), datetime(2026, 8, 31, 10, 0))
        assert not ok

    def test_le_lendemain_on_repose(self):
        ok, why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31"), datetime(2026, 9, 1, 10, 0))
        assert ok and "30/09/2026" in why

    def test_sans_echeance_memorisee_on_s_abstient(self):
        """Rien ne prouve qu'une repose allongerait quoi que ce soit."""
        ok, why = pr.needs_renewal(_pos(), datetime(2026, 9, 1, 10, 0))
        assert not ok and "aucun trou constaté" in why

    def test_position_hold_hors_gestion_bot(self):
        ok, _why = pr.needs_renewal(
            _pos(hold=True, protection_expires_at="2026-08-31"),
            datetime(2026, 9, 1, 10, 0))
        assert not ok

    def test_sans_seuils_il_n_y_a_rien_a_reposer(self):
        ok, why = pr.needs_renewal(
            _pos(target_low=0, target_high=0,
                 protection_expires_at="2026-08-31"), datetime(2026, 9, 1, 10, 0))
        assert not ok and "seuils" in why


class TestDeclencheurTrouQuiDure:
    """Le cas JNJ : protection disparue AVANT l'échéance (dividende du 25/08)."""

    def test_un_trou_frais_ne_declenche_rien(self):
        """Une lecture ratée ne doit jamais faire reposer un ordre."""
        ok, why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31",
                 naked_since="2026-08-26T12:35:00"),
            datetime(2026, 8, 26, 12, 40))
        assert not ok and "confirmation attendue" in why

    def test_un_trou_qui_tient_est_repare(self):
        ok, why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31",
                 naked_since="2026-08-26T12:35:00"),
            datetime(2026, 8, 26, 13, 40))
        assert ok and "depuis" in why

    def test_l_echeance_encore_lointaine_n_empeche_pas_la_reparation(self):
        """C'est tout le bug : le bot attendait le 31 alors que le trou était là."""
        ok, _why = pr.needs_renewal(
            _pos(protection_expires_at="2026-12-31",
                 naked_since="2026-08-26T12:35:00"),
            datetime(2026, 8, 26, 14, 0))
        assert ok

    def test_une_protection_revue_efface_le_marqueur(self):
        """`sync_engine` retire `naked_since` dès qu'il revoit la protection :
        sans marqueur, plus de déclencheur."""
        ok, _why = pr.needs_renewal(
            _pos(protection_expires_at="2026-08-31"), datetime(2026, 8, 26, 14, 0))
        assert not ok

    def test_un_trou_sur_position_hold_reste_ignore(self):
        ok, _why = pr.needs_renewal(
            _pos(hold=True, naked_since="2026-08-26T12:35:00"),
            datetime(2026, 8, 26, 14, 0))
        assert not ok


class TestPreuveDeCarnet:
    """La seconde moitié : ce que BD montre réellement."""

    def test_un_ordre_a_seuil_actif_vaut_protection(self):
        ordres = [{"statut": "En cours", "bd_ticker": "BAC", "seuil": 58.93}]
        assert pr._has_live_protection(ordres, _pos())

    def test_reconnaissance_par_le_nom_bd_faute_de_ticker(self):
        ordres = [{"statut": "En cours", "bd_ticker": "",
                   "name": "Bank of America Corporation", "seuil": 58.93}]
        assert pr._has_live_protection(ordres, _pos())

    def test_un_ordre_sans_seuil_ne_protege_pas(self):
        """Un achat limite en attente n'est pas une protection."""
        ordres = [{"statut": "En cours", "bd_ticker": "BAC", "seuil": None}]
        assert not pr._has_live_protection(ordres, _pos())

    def test_un_ordre_expire_ne_protege_plus(self):
        ordres = [{"statut": "Exécuté", "bd_ticker": "BAC", "seuil": 58.93}]
        assert not pr._has_live_protection(ordres, _pos())


class TestCycleComplet:
    """Le cycle ne repose que si les DEUX preuves concordent."""

    @pytest.fixture
    def bot(self, monkeypatch, tmp_path):
        envois, poses = [], []
        data = {"positions": {"BAC": _pos(protection_expires_at="2026-08-31")}}

        monkeypatch.setattr(pr.portfolio, "load", lambda: data)
        monkeypatch.setattr(pr.portfolio, "save", lambda d: data.update(d))
        pr._notified_failure.clear()

        def _create(page, ticker, qty, sl, tp, validity):
            poses.append((ticker, qty, sl, tp, validity))
            return {"id": "neuf-1", "children": ["c1", "c2"]}

        monkeypatch.setattr(pr.bd_orders, "create_expert_order", _create)
        monkeypatch.setattr(pr.bd_orders, "confirm_order_auto",
                            lambda page, oid, is_buy: {"ok": True})
        return {"envois": envois, "poses": poses, "data": data}

    def _carnet(self, monkeypatch, orders, orders_read=True):
        monkeypatch.setattr(
            pr, "_lecture_carnet",
            lambda: {"orders": orders, "orders_read": orders_read})

    def _run(self, monkeypatch, bot, quand):
        # playwright_session.run exécute juste le lambda : les appels BD
        # eux-mêmes sont déjà remplacés.
        monkeypatch.setattr(pr.playwright_session, "run",
                            lambda fn, timeout=None: fn(None))
        if isinstance(quand, date) and not isinstance(quand, datetime):
            quand = datetime.combine(quand, datetime.min.time().replace(hour=10))
        pr.renew_cycle(bot["envois"].append, now=quand)

    def test_repose_apres_la_bascule_du_mois(self, monkeypatch, bot):
        self._carnet(monkeypatch, [])
        self._run(monkeypatch, bot, date(2026, 9, 1))

        assert bot["poses"] == [("BAC", 12, 58.93, 67.53, "max")]
        p = bot["data"]["positions"]["BAC"]
        assert p["protection_expires_at"] == "2026-09-30"
        assert p["protection_ids"] == ["c1", "c2"]
        assert p["protected"] is True
        assert any("PROTECTION REPOSÉE" in m for m in bot["envois"])

    def test_rien_avant_la_bascule(self, monkeypatch, bot):
        self._carnet(monkeypatch, [])
        self._run(monkeypatch, bot, date(2026, 8, 21))
        assert bot["poses"] == []
        assert bot["envois"] == []

    def test_protection_encore_au_carnet_aucune_repose(self, monkeypatch, bot):
        """Le doublon de vente est pire que le trou qu'on répare."""
        self._carnet(monkeypatch,
                     [{"statut": "En cours", "bd_ticker": "BAC", "seuil": 58.93}])
        self._run(monkeypatch, bot, date(2026, 9, 1))
        assert bot["poses"] == []

    def test_carnet_illisible_aucune_repose(self, monkeypatch, bot):
        """On ne repose jamais sur une absence non prouvée (11/08/2026)."""
        self._carnet(monkeypatch, [], orders_read=False)
        self._run(monkeypatch, bot, date(2026, 9, 1))
        assert bot["poses"] == []

    def test_echec_de_repose_alerte_une_seule_fois_par_jour(self, monkeypatch, bot):
        self._carnet(monkeypatch, [])
        monkeypatch.setattr(pr.bd_orders, "create_expert_order",
                            lambda *a, **k: None)
        self._run(monkeypatch, bot, date(2026, 9, 1))
        self._run(monkeypatch, bot, date(2026, 9, 1))
        alertes = [m for m in bot["envois"] if "ÉCHOUÉE" in m]
        assert len(alertes) == 1
        # L'échec ne dégrade rien : aucune protection n'a été annulée pour
        # tenter la repose.
        assert bot["data"]["positions"]["BAC"]["protection_expires_at"] == "2026-08-31"
