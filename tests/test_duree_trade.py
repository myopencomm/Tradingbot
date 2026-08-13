"""Durée de détention — le KPI « combien de temps pour faire ce gain ».

Demandé le 13/08/2026 : trouver les trades RAPIDES. Un gain de 100 € en trois
jours et le même en trois mois n'ont pas la même valeur — entre les deux, le
capital n'a pas travaillé.

Le bot connaissait la date de SORTIE de chaque trade et jamais celle d'entrée :
les positions naissaient à quatre endroits, chacun écrivant son propre
dictionnaire, et aucun ne datait l'ouverture.
"""
import copy

import pytest

import history
import portfolio
import stats


class TestHeldDays:
    def test_duree_simple(self):
        assert stats.held_days("2026-08-01T10:00:00+02:00",
                               "2026-08-11T10:00:00+02:00") == 10.0

    def test_fraction_de_journee(self):
        """Décimale et non entière : un aller-retour dans la journée est le
        trade le plus rapide qui soit, l'arrondir à 0 le rendrait
        indistinguable d'une donnée manquante."""
        d = stats.held_days("2026-08-01T09:00:00+02:00", "2026-08-01T15:00:00+02:00")
        assert d == 0.25

    def test_entree_inconnue(self):
        assert stats.held_days(None, "2026-08-11T10:00:00+02:00") is None
        assert stats.held_days("", "2026-08-11T10:00:00+02:00") is None

    def test_date_illisible_ne_leve_pas(self):
        assert stats.held_days("pas une date", "2026-08-11T10:00:00+02:00") is None

    def test_dates_sans_fuseau_acceptees(self):
        """Les vieux enregistrements n'ont pas de fuseau ; les mélanger à des
        dates qui en ont lèverait un TypeError au milieu d'une clôture."""
        assert stats.held_days("2026-08-01T10:00:00", "2026-08-06T10:00:00") == 5.0
        assert stats.held_days("2026-08-01T10:00:00",
                               "2026-08-06T10:00:00+02:00") is not None

    def test_jamais_negatif(self):
        """Une horloge qui recule ne doit pas produire une durée négative."""
        assert stats.held_days("2026-08-11T10:00:00+02:00",
                               "2026-08-01T10:00:00+02:00") == 0.0


class TestPositionDatee:
    def test_une_nouvelle_position_porte_sa_date(self):
        p = portfolio.new_position("AIR.PA", 5, 196.9, 190.0, 210.0)
        assert p["opened_at"]
        assert p["ticker"] == "AIR.PA" and p["qty"] == 5

    def test_les_champs_supplementaires_passent(self):
        p = portfolio.new_position("JNJ", 5, 262.29, 248.5, 286.95, bd_name="J&J")
        assert p["bd_name"] == "J&J"

    def test_tous_les_points_de_creation_passent_par_la(self):
        """Le champ manquait parce que quatre endroits écrivaient leur propre
        dictionnaire. Ce test refuse le retour d'un cinquième."""
        import re
        from pathlib import Path
        racine = Path(__file__).resolve().parent.parent
        for f in ("sync_engine.py", "telegram_bot.py", "portfolio.py"):
            src = (racine / f).read_text(encoding="utf-8")
            for m in re.finditer(r'positions", \{\}\)\[[^\]]+\] = \{', src):
                bloc = src[m.start():m.start() + 400]
                assert "entry_price" not in bloc, (
                    f"{f} construit une position à la main — "
                    f"utilise portfolio.new_position() pour ne pas oublier opened_at")


class TestRecordClose:
    @pytest.fixture(autouse=True)
    def _isole(self, tmp_path, monkeypatch):
        h = tmp_path / "h.json"
        h.write_text('{"closed_trades": []}')
        monkeypatch.setattr(history, "HISTORY_PATH", h)
        monkeypatch.setattr(portfolio, "get_entry_context", lambda t: {})
        monkeypatch.setattr(portfolio, "clear_entry_context", lambda t: None)
        monkeypatch.setattr(stats.prices, "_ticker_currency", lambda t: "EUR")
        monkeypatch.setattr(stats.prices, "fx_to_eur", lambda c: 1.0)

    def test_la_duree_est_enregistree(self):
        stats.record_close("AIR", "AIR.PA", 5, 100.0, 110.0,
                           opened_at="2026-08-01T10:00:00+02:00")
        t = history.closed_trades()[-1]
        assert t["opened_at"] == "2026-08-01T10:00:00+02:00"
        assert t["closed_at"]
        assert t["held_days"] > 0
        assert t["held_source"] == "exact"

    def test_sans_date_d_entree_on_n_invente_pas(self):
        stats.record_close("AIR", "AIR.PA", 5, 100.0, 110.0)
        t = history.closed_trades()[-1]
        assert t["held_days"] is None
        assert t["held_source"] is None


class TestAgregats:
    def _stats(self, monkeypatch, trades):
        monkeypatch.setattr(history, "closed_trades", lambda: trades)
        monkeypatch.setattr(portfolio, "get_managed_positions", lambda: {})
        return stats.get_stats()

    def _t(self, nom, pnl, jours):
        return {"name": nom, "pnl": pnl, "pnl_eur": pnl, "qty": 1,
                "result": "win" if pnl > 0 else "loss", "held_days": jours,
                "date": "2026-08-01", "entry_price": 100, "ticker": "X.PA"}

    def test_mediane_et_non_moyenne(self, monkeypatch):
        """Un seul trade gardé très longtemps déplacerait la moyenne sans rien
        dire du rythme habituel."""
        s = self._stats(monkeypatch, [self._t("A", 10, 2), self._t("B", 10, 3),
                                      self._t("C", 10, 100)])
        assert s["hold"]["median"] == 3
        assert s["hold"]["avg"] > 30

    def test_gagnants_et_perdants_separes(self, monkeypatch):
        s = self._stats(monkeypatch, [self._t("A", 10, 20), self._t("B", -5, 2)])
        assert s["hold_wins"]["median"] == 20
        assert s["hold_losses"]["median"] == 2

    def test_les_trades_sans_duree_sont_comptes_a_part(self, monkeypatch):
        """À dire, pas à masquer : sinon la médiane porte sur un échantillon
        dont on ignore la taille réelle."""
        s = self._stats(monkeypatch, [self._t("A", 10, 5), self._t("B", 10, None)])
        assert s["hold"]["n"] == 1
        assert s["hold_unknown"] == 1

    def test_aucun_trade_chronometre(self, monkeypatch):
        s = self._stats(monkeypatch, [self._t("A", 10, None)])
        assert s["hold"] is None and s["hold_unknown"] == 1
        assert s["fastest_wins"] == []

    def test_classement_par_gain_quotidien(self, monkeypatch):
        """La question posée est « quels trades vont vite » : +8 % en 2 jours
        passe devant +25 % en 40 jours."""
        s = self._stats(monkeypatch, [self._t("LENT", 250, 40), self._t("RAPIDE", 80, 2)])
        assert [t["name"] for t in s["fastest_wins"]] == ["RAPIDE", "LENT"]

    def test_les_perdants_ne_sont_pas_dans_le_classement(self, monkeypatch):
        s = self._stats(monkeypatch, [self._t("A", -50, 1)])
        assert s["fastest_wins"] == []

    def test_un_trade_eclair_ne_divise_pas_par_zero(self, monkeypatch):
        s = self._stats(monkeypatch, [self._t("FLASH", 100, 0.0)])
        assert s["fastest_wins"][0]["eur_per_day"] == 400.0   # plancher de 0.25 j


class TestDashboard:
    def test_les_lignes_portent_la_duree(self, monkeypatch):
        import dashboard
        monkeypatch.setattr(stats, "load_history", lambda: {"closed_trades": [{
            "name": "NVDA", "ticker": "NVDA", "qty": 7, "entry_price": 205.0,
            "exit_price": 225.0, "pnl": 140.0, "pnl_eur": 120.56, "result": "win",
            "date": "2026-08-12", "held_days": 9.08, "held_source": "approx"}]})
        monkeypatch.setattr(portfolio, "load", lambda: {"positions": {}, "cash_available": 0})
        t = dashboard.build_data()["trades"][0]
        assert t["held"] == 9.08
        assert t["per_day"] == round(120.56 / 9.08, 2)

    def test_duree_inconnue_reste_vide(self, monkeypatch):
        import dashboard
        monkeypatch.setattr(stats, "load_history", lambda: {"closed_trades": [{
            "name": "AC", "ticker": "AC.PA", "qty": 20, "entry_price": 50.0,
            "exit_price": 48.0, "pnl": -45.7, "pnl_eur": -45.7, "result": "loss",
            "date": "2026-07-08"}]})
        monkeypatch.setattr(portfolio, "load", lambda: {"positions": {}, "cash_available": 0})
        t = dashboard.build_data()["trades"][0]
        assert t["held"] is None and t["per_day"] is None
