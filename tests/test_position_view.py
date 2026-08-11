"""position_view — un calcul, N rendus.

Le contrat : les cinq vues (/status, STATUS planifié, snapshot IA, dashboard,
/stats) lisent des champs déjà calculés et ne recalculent plus rien. Ce qui
change ici change partout — c'est exactement le but.
"""
import analysis
import position_view

AIR = {"ticker": "AIR.PA", "qty": 5, "entry_price": 196.9,
       "target_low": 209.7, "target_high": 217.1, "protected": True}


def q(price=211.95, currency="EUR", stale=False, as_of="2026-08-11", status="ok"):
    return {"price": price, "currency": currency, "stale": stale,
            "as_of": as_of, "status": status}


class TestCalcul:
    def test_chiffres_de_base(self):
        v = position_view.view("AIR", AIR, q())
        assert v["price"] == 211.95
        assert v["chg_pct"] == 7.64
        assert v["pnl"] == round((211.95 - 196.9) * 5, 2)
        assert v["sym"] == "€"
        assert v["source"] == "yf"

    def test_conversion_en_euros_sans_double_arrondi(self):
        """Le P&L euro se calcule sur la valeur BRUTE : arrondir avant de
        convertir décalait le dashboard d'un centime."""
        cfg = {"ticker": "BAC", "qty": 12, "entry_price": 62.8603,
               "bd_pru_raw": 54.7325}
        v = position_view.view("BAC", cfg, q(price=63.86, currency="USD"))
        brut = (63.86 - 62.8603) * 12
        assert v["pnl"] == round(brut, 2)
        assert v["entry_eur"] == 54.7325        # PRU BRUT de BD, frais inclus
        assert v["pru_bd"] is True

    def test_pnl_euro_du_releve_bd_prefere_a_une_conversion(self):
        """Titre que yfinance ne cote plus : BD chiffre déjà en euros, ce qui
        vaut mieux qu'une conversion sur un cours mort."""
        cfg = {"ticker": "GVN.PA", "qty": 142, "entry_price": 0.937,
               "bd_price": 0.0018, "bd_price_at": "2026-08-11T11:35",
               "bd_pnl_eur": -132.79, "bd_pru_raw": 0.937}
        v = position_view.view("GVN", cfg, q(price=None, as_of=None))
        assert v["source"] == "bd"
        assert v["pnl_eur"] == -132.79

    def test_titre_sans_valeur_chiffre_sans_aucun_cours(self):
        cfg = {"ticker": "X.PA", "qty": 100, "entry_price": 2.0, "worthless": True}
        v = position_view.view("X", cfg, q(price=None, status="suspended"))
        assert v["estimated"] is True
        assert v["pnl_eur"] == -200.0
        assert v["chg_eur"] == -100.0


class TestDrapeaux:
    def test_protection_absente(self):
        v = position_view.view("AIR", dict(AIR, protected=False), q())
        assert v["protected"] is False
        assert "AUCUN ordre SL/TP actif" in position_view.alerte_protection(v)

    def test_jamais_verifie_n_est_pas_non_protege(self):
        """None = aucun sync n'a encore tranché. Ce n'est PAS « vérifié, aucune
        protection » : afficher l'alerte dans ce cas serait un cri à tort."""
        v = position_view.view("AIR", dict(AIR, protected=None), q())
        assert v["protected"] is None
        assert position_view.alerte_protection(v) == ""

    def test_stop_calcule_mais_non_pose(self):
        v = position_view.view("AIR", dict(AIR, pending_sl=212.0), q())
        assert v["pending_sl"] == 212.0
        assert "PAS posé sur BD" in position_view.alerte_stop_en_attente(v)

    def test_stop_en_attente_deja_depasse_par_le_stop_actif(self):
        v = position_view.view("AIR", dict(AIR, pending_sl=200.0), q())
        assert v["pending_sl"] is None
        assert position_view.alerte_stop_en_attente(v) == ""

    def test_perf_aberrante_seulement_hors_euro(self):
        """Une perf énorme sur un titre en euros est une vraie perf ; sur un
        titre en devise, c'est presque toujours un PRU mal saisi."""
        cfg = {"ticker": "NVDA", "qty": 1, "entry_price": 10.0}
        assert position_view.view("N", cfg, q(price=100.0, currency="USD"))["aberrant"]
        cfg_eur = {"ticker": "X.PA", "qty": 1, "entry_price": 10.0}
        assert not position_view.view("X", cfg_eur, q(price=100.0))["aberrant"]


class TestSnapshotIA:
    """Le trou corrigé par cette phase.

    Le bloc annoncé à l'IA comme « SOURCE DE VÉRITÉ » présentait les SL/TP
    comme des faits sans jamais dire qu'aucun ordre ne les portait. Du 31/07 au
    05/08, l'IA a raisonné chaque matin comme si BAC était protégé.
    """

    def _snapshot(self, monkeypatch, cfg):
        import portfolio
        import prices
        monkeypatch.setattr(portfolio, "load", lambda: {
            "cash_available": 244.13, "positions": {"AIR": cfg}})
        monkeypatch.setattr(prices, "get_quote", lambda t: q())
        return analysis._portfolio_snapshot()

    def test_l_ia_est_prevenue_qu_un_seuil_ne_protege_rien(self, monkeypatch):
        txt = self._snapshot(monkeypatch, dict(AIR, protected=False))
        assert "NON PROTECTEURS" in txt
        assert "AUCUN stop réel" in txt

    def test_l_ia_est_prevenue_d_un_stop_calcule_mais_non_pose(self, monkeypatch):
        txt = self._snapshot(monkeypatch, dict(AIR, pending_sl=212.0))
        assert "PAS posé sur BD" in txt

    def test_position_protegee_aucun_bruit(self, monkeypatch):
        txt = self._snapshot(monkeypatch, AIR)
        assert "NON PROTECTEURS" not in txt and "PAS posé" not in txt
        assert "SL €209.7" in txt and "TP €217.1" in txt


class TestCoherenceDesVues:
    def test_les_vues_partagent_le_meme_cours(self, monkeypatch):
        """Le point de la phase : plus aucune vue ne peut afficher un chiffre
        différent d'une autre pour la même position."""
        import prices
        monkeypatch.setattr(prices, "get_quote", lambda t: q())
        v1 = position_view.view("AIR", AIR)
        v2 = position_view.view("AIR", AIR)
        assert v1 == v2

    def test_views_conserve_l_ordre(self):
        pos = {"A": dict(AIR), "B": dict(AIR), "C": dict(AIR)}
        assert [v["name"] for v in position_view.views(
            pos, quotes={n: q() for n in pos})] == ["A", "B", "C"]
