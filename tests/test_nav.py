"""Valeur liquidative par part — la performance sans les mouvements d'argent.

La question posée le 19/08/2026 : « combien mon investissement a-t-il grossi ? ».
Le P&L en euros n'y répond pas — verser 1 000 € le fait grimper sans qu'aucune
décision n'ait été prise. La part y répond, à condition qu'un versement achète
des parts au lieu de passer pour une performance. C'est CE point que ces tests
verrouillent : le reste n'est que de l'arithmétique.
"""
import pytest

import nav


@pytest.fixture(autouse=True)
def _nav_en_bac_a_sable(tmp_path, monkeypatch):
    """La série vit dans un fichier — jamais le vrai (cf. incident du 18/08)."""
    monkeypatch.setattr(nav, "NAV_PATH", tmp_path / "nav_history.json")


def _fonds(total, latent=0.0):
    return {"cash": 0.0, "positions": total, "total": total,
            "latent": latent, "lignes": ["X"]}


class TestParts:
    def test_sans_mouvement_la_part_suit_la_valeur(self, monkeypatch):
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        assert nav.relever()["part"] == 100.0
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1200.0))
        assert nav.relever()["part"] == 120.0          # +20 %

    def test_un_versement_n_est_PAS_une_performance(self, monkeypatch):
        """LE test. Fonds à 1 000 € (part 100), on verse 1 000 € : le fonds
        double, la part ne bouge pas d'un centime."""
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        nav.declarer_flux(1000.0, "virement")
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(2000.0))
        p = nav.relever()
        assert p["part"] == 100.0, "le versement a été compté comme un gain"
        assert p["parts"] == 20.0, "le versement doit acheter des parts"

    def test_un_retrait_non_plus(self, monkeypatch):
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        nav.declarer_flux(-400.0, "retrait")
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(600.0))
        assert nav.relever()["part"] == 100.0

    def test_un_flux_n_est_converti_qu_une_fois(self, monkeypatch):
        """Deux relevés d'affilée ne doivent pas racheter les mêmes parts."""
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        nav.declarer_flux(1000.0)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(2000.0))
        assert nav.relever()["parts"] == 20.0
        assert nav.relever()["parts"] == 20.0

    def test_la_performance_survit_au_versement(self, monkeypatch):
        """Verser puis gagner 10 % : la part doit dire 110, pas autre chose.
        C'est tout l'intérêt de la méthode — la mesure reste comparable."""
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        nav.declarer_flux(1000.0)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(2000.0))
        nav.relever()
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(2200.0))
        assert nav.relever()["part"] == 110.0


class TestSaut:
    def test_un_saut_sans_flux_declare_est_signale(self, monkeypatch):
        """Un versement non déclaré ferait mentir la courbe en silence."""
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        dits = []
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(2000.0))
        nav.relever(send_fn=dits.append)
        assert dits and "/nav depot" in dits[0]

    def test_une_variation_normale_ne_dit_rien(self, monkeypatch):
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        nav.relever()
        dits = []
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1030.0))
        nav.relever(send_fn=dits.append)
        assert dits == []


class TestPerimetre:
    def test_les_hold_sont_exclus(self, monkeypatch):
        """Choix du 19/08/2026 : la plus grosse ligne `hold` porte une lourde
        moins-value issue d'une décision ANTÉRIEURE au bot. L'inclure noierait
        la performance du bot dans un pari qu'il n'a jamais pris."""
        import position_view
        monkeypatch.setattr(position_view, "views", lambda p: [
            {"name": "GERE", "hold": False, "bd_value_eur": 500.0,
             "pnl_eur": 50.0, "price": 10.0, "qty": 50, "currency": "EUR"},
            {"name": "HOLD", "hold": True, "bd_value_eur": 5000.0,
             "pnl_eur": -4000.0, "price": 10.0, "qty": 500, "currency": "EUR"},
        ])
        import portfolio
        monkeypatch.setattr(portfolio, "load",
                            lambda: {"positions": {}, "cash_available": 100.0})
        p = nav.perimetre()
        assert p["total"] == 600.0
        assert p["lignes"] == ["GERE"]
        assert p["latent"] == 50.0


class TestSerie:
    def test_le_mesure_remplace_le_reconstitue_a_partir_du_1er_releve(self, monkeypatch):
        """Les deux régimes ne se chevauchent jamais : sur une date donnée il
        n'y a qu'une vérité, et c'est la mesurée."""
        recon = [{"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
                  "part": 100.0, "source": "reconstitué"},
                 {"date": "2026-06-01", "valeur": 1100.0, "parts": 10.0,
                  "part": 110.0, "source": "reconstitué"},
                 {"date": "2026-08-19", "valeur": 1200.0, "parts": 10.0,
                  "part": 120.0, "source": "reconstitué"}]
        monkeypatch.setattr(nav, "reconstituer", lambda: recon)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1200.0))
        nav.relever()
        s = nav.serie()
        dates = [x["date"] for x in s]
        assert len(dates) == len(set(dates)), f"dates en double : {dates}"
        assert s[-1]["source"] == "mesuré"
        assert [x["source"] for x in s].count("mesuré") == 1

    def test_la_bascule_ne_cree_pas_de_marche(self, monkeypatch):
        """Le premier relevé reprend le nombre de parts de la reconstitution :
        sinon la courbe sauterait à la bascule, et le saut serait pris pour
        une performance."""
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-08-18", "valeur": 3285.51, "parts": 28.2841,
             "part": 116.16, "source": "reconstitué"}])
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(3285.51))
        assert nav.relever()["part"] == pytest.approx(116.16, abs=0.01)


class TestCapitalInitial:
    def test_valeur_moins_ce_que_le_bot_a_produit(self, monkeypatch):
        import history
        monkeypatch.setattr(history, "closed_trades",
                            lambda: [{"pnl_eur": 300.0}, {"pnl_eur": -50.0}])
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1300.0, latent=50.0))
        assert nav.capital_initial() == 1000.0


class TestCoherence:
    """La tuile « valeur du fonds » et le bout de la courbe doivent dire le
    MÊME chiffre. Sinon le dashboard affiche deux vérités pour une même chose,
    et c'est toujours celle qu'on ne regarde pas qui a raison (19/08/2026 : la
    tuile était calculée à l'affichage, la courbe figée au relevé de la veille).
    """

    def _amorcer(self, monkeypatch):
        monkeypatch.setattr(nav, "reconstituer", lambda: [
            {"date": "2026-01-01", "valeur": 1000.0, "parts": 10.0,
             "part": 100.0, "source": "reconstitué"}])
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1000.0))
        nav.relever()

    def test_la_courbe_suit_le_fonds_sans_attendre_le_releve_du_soir(self, monkeypatch):
        self._amorcer(monkeypatch)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1100.0))
        assert nav.serie()[-1]["valeur"] == 1100.0
        assert nav.serie()[-1]["part"] == 110.0
        assert nav.resume()["valeur"] == 1100.0

    def test_le_rafraichissement_live_n_ecrit_rien(self, monkeypatch):
        """Consulter le dashboard ne doit pas modifier l'historique — sinon
        chaque visite créerait un point, et le relevé du soir n'aurait plus
        aucun sens."""
        self._amorcer(monkeypatch)
        avant = nav.NAV_PATH.read_text(encoding="utf-8")
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1100.0))
        nav.serie(); nav.serie(); nav.resume()
        assert nav.NAV_PATH.read_text(encoding="utf-8") == avant

    def test_le_live_ne_cree_jamais_de_part_supplementaire(self, monkeypatch):
        """Le nombre de parts ne bouge QUE sur un versement ou un retrait."""
        self._amorcer(monkeypatch)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1500.0))
        assert nav.parts_courantes() == 10.0
        assert nav.serie()[-1]["parts"] == 10.0

    def test_sans_live_la_courbe_reste_celle_des_releves(self, monkeypatch):
        """`live=False` doit rendre l'historique brut — c'est ce qui permet de
        vérifier ce qui a réellement été enregistré."""
        self._amorcer(monkeypatch)
        monkeypatch.setattr(nav, "perimetre", lambda: _fonds(1100.0))
        assert nav.serie(live=False)[-1]["valeur"] == 1000.0
