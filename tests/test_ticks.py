"""Pas de cotation — un prix qui n'est pas un multiple du pas n'existe pas.

Deux incidents dans ce fichier :
  · 18/08/2026 — RTX envoyé à 224.431 $ (trois décimales). BD répond 200, le
    NYSE refuse. Ni motif, ni annulation possible.
  · 19/08/2026 — le même achat était annoncé « Entrée 224.4312 » sur Telegram,
    mémorisé autrement et envoyé encore autrement. Trois chiffres pour un seul
    achat : rien de cassé, mais un bot qui annonce un prix et en envoie un
    autre n'inspire pas confiance.

D'où l'arrondi À LA SOURCE, et ce module feuille partagé par l'analyse (qui
décide le prix) et l'envoi d'ordre (qui le transmet).
"""
import pytest

import ticks


class TestPas:
    def test_us_au_cent_au_dessus_du_dollar(self):
        """SEC Rule 612."""
        assert ticks.tick_for(224.43, "USD") == 0.01
        assert ticks.tick_for(1.00, "USD") == 0.01

    def test_us_plus_fin_sous_le_dollar(self):
        assert ticks.tick_for(0.87, "USD") == 0.0001

    def test_eur_se_resserre_quand_le_cours_baisse(self):
        """Arrondir au centime un titre à 0,15 € le déplacerait de plus de 3 %.
        Un SL déplacé de 3 %, ce sont des euros réels — on ne le fait jamais."""
        assert ticks.tick_for(0.15, "EUR") == 0.0001
        assert ticks.tick_for(8.45, "EUR") == 0.001
        assert ticks.tick_for(196.9, "EUR") == 0.01

    def test_le_pas_europeen_ne_deplace_jamais_le_prix_de_plus_de_0_1_pourcent(self):
        """LA propriété qui compte LÀ OÙ ON CHOISIT le pas : en Europe il n'est
        pas déductible du cours, donc on le devine — et on le devine trop FIN
        plutôt que trop GROSSIER. Trop fin coûte un aller-retour avec BD, qui
        le signale par un 400 et que le code retente. Trop grossier déplace le
        prix pour de bon.

        Aux États-Unis le pas est IMPOSÉ (SEC Rule 612 : 0,01 $ au-dessus d'un
        dollar) : à 3,21 $ il déplace mécaniquement le prix de 0,3 %, et il n'y
        a rien à arbitrer — d'où l'absence de l'USD ici."""
        for p in (0.0512, 0.1543, 0.87654, 3.2199, 8.4567, 24.601,
                  196.9012, 224.4312, 515.4999):
            r = ticks.round_price(p, "EUR", "down")
            assert abs(r - p) / p * 100 < 0.1, f"{p} EUR → {r}"


class TestSens:
    def test_le_sens_est_conservateur(self):
        """Entrée et TP vers le bas, SL vers le haut : on ne paie jamais plus
        cher que décidé, la protection ne s'éloigne jamais, le TP ne devient
        jamais plus dur à atteindre."""
        e, sl, tp = ticks.round_levels(224.4312, 215.004, 248.506, "USD")
        assert (e, sl, tp) == (224.43, 215.01, 248.5)
        assert e <= 224.4312 and sl >= 215.004 and tp <= 248.506

    def test_un_prix_deja_sur_le_pas_ne_bouge_pas(self):
        """Sans la tolérance epsilon, chaque passage pousserait le prix d'un
        cran — et le trailing annulerait puis reposerait la même protection en
        boucle."""
        for p in (262.29, 215.0, 248.5, 286.95):
            for sens in ("up", "down", "nearest"):
                assert ticks.round_price(p, "USD", sens) == p

    def test_arrondir_deux_fois_ne_change_rien(self):
        """L'analyse arrondit, l'envoi ré-arrondit : le second passage doit
        être neutre, sinon le prix dériverait d'un cran à chaque étape."""
        for cur in ("EUR", "USD"):
            for p in (224.4312, 0.87654, 8.4567, 196.9012):
                for sens in ("up", "down"):
                    une = ticks.round_price(p, cur, sens)
                    assert ticks.round_price(une, cur, sens) == une

    def test_none_passe_sans_bruit(self):
        assert ticks.round_price(None, "USD") is None
        assert ticks.round_levels(None, None, None, "EUR") == (None, None, None)


class TestSourceUnique:
    def test_l_envoi_et_l_analyse_arrondissent_pareil(self):
        """Deux implémentations de l'arrondi, c'est la garantie qu'elles
        divergeront un jour. L'envoi délègue au même module que l'analyse."""
        import bourse_direct_orders as bd
        for p in (224.4312, 0.87654, 209.673):
            for sens in ("up", "down", "nearest"):
                assert bd._round_to_tick(p, 0.01, sens) == \
                       ticks.round_to_tick(p, 0.01, sens)


class TestAnalyseArrondieALaSource:
    """Le prix DÉCIDÉ doit être le prix TRAITABLE — sinon l'écart réapparaît
    entre ce que l'utilisateur lit et ce qui part chez le courtier."""

    def _valider(self, monkeypatch, texte, cours, devise):
        import analysis
        import prices
        monkeypatch.setattr(prices, "get_quote", lambda t: {
            "price": cours, "currency": devise, "stale": False,
            "as_of": "2026-08-19", "status": "ok"})
        for nom, val in (("get_technicals", {"rsi": 50, "above_ma200": True,
                                             "atr_pct": 2.0}),
                         ("get_fundamentals", {}), ("get_price_context", {}),
                         ("get_yf_news", [])):
            monkeypatch.setattr(prices, nom, lambda *a, _v=val, **k: _v)
        class FauxIA:
            def complete(self, *a, **k): return texte
            def complete_cheap(self, *a, **k): return texte
        return analysis.validate_candidate("RTX", ai=FauxIA())

    def test_le_prix_annonce_est_un_prix_traitable(self, monkeypatch):
        """LE cas RTX : 224.4312 n'est pas un prix que le NYSE accepte."""
        r = self._valider(monkeypatch,
                          "ACHAT\nEntrée : 224.4312\nSL : 215.004\nTP : 248.506\n"
                          "Risque : MEDIUM", 224.4312, "USD")
        assert r["entry"] == 224.43
        assert r["sl"] == 215.01
        assert r["tp"] == 248.5

    def test_aucun_niveau_ne_garde_plus_de_deux_decimales_en_usd(self, monkeypatch):
        r = self._valider(monkeypatch,
                          "ACHAT\nEntrée : 262.2871\nSL : 249.1729\nTP : 301.6301\n"
                          "Risque : LOW", 262.2871, "USD")
        for niveau in ("entry", "sl", "tp"):
            v = r[niveau]
            assert round(v, 2) == v, f"{niveau} = {v}"

    def test_l_envoi_ne_modifie_plus_le_prix_de_l_analyse(self, monkeypatch):
        """Bout en bout : ce que l'analyse décide traverse l'envoi INTACT.
        C'est la propriété demandée — un seul chiffre, du message à BD."""
        import bourse_direct_orders as bd
        r = self._valider(monkeypatch,
                          "ACHAT\nEntrée : 224.4312\nSL : 215.004\nTP : 248.506\n"
                          "Risque : MEDIUM", 224.4312, "USD")
        tick = ticks.tick_for(r["entry"], "USD")
        assert bd._round_to_tick(r["entry"], tick, "down") == r["entry"]
        assert bd._round_to_tick(r["sl"], tick, "up") == r["sl"]
        assert bd._round_to_tick(r["tp"], tick, "down") == r["tp"]


class TestIndicateurManquant:
    """Un indicateur absent ne doit pas faire tomber la validation entière.

    Découvert le 19/08/2026 en écrivant les tests d'arrondi : `{'N/A':+}` lève
    `ValueError: Sign not allowed in string format specifier`. Un défaut
    textuel et un format signé ne peuvent pas cohabiter. Les lignes voisines
    (momentum 12-1, distance MM200) testaient `is not None` ; celle du momentum
    1 mois l'avait oublié — et faisait planter `validate_candidate` en entier
    pour un titre sans assez d'historique.
    """

    def test_un_titre_sans_momentum_1m_se_valide_quand_meme(self, monkeypatch):
        import analysis
        import prices
        monkeypatch.setattr(prices, "get_quote", lambda t: {
            "price": 100.0, "currency": "EUR", "stale": False,
            "as_of": "2026-08-19", "status": "ok"})
        # LE cas : pas de momentum_1m (titre récemment listé).
        monkeypatch.setattr(prices, "get_technicals", lambda *a, **k: {
            "rsi": 50, "above_ma200": True, "atr_pct": 2.0})
        for nom, val in (("get_fundamentals", {}), ("get_price_context", {}),
                         ("get_yf_news", [])):
            monkeypatch.setattr(prices, nom, lambda *a, _v=val, **k: _v)

        class FauxIA:
            def complete(self, *a, **k):
                return "ACHAT\nEntrée : 100\nSL : 95\nTP : 115\nRisque : LOW"

        r = analysis.validate_candidate("X.PA", ai=FauxIA())
        assert r["verdict"] in ("ACHAT", "EXCLUS")   # décidé, pas planté
        assert r["entry"] == 100.0
