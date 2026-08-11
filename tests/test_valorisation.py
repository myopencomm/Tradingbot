"""Choix du cours retenu et diagnostic d'absence de cours.

Deux fonctions nées d'incidents réels :
  · best_price   — yfinance a servi des cours vieux de 2 à 3 séances en les
                   présentant comme courants (04/08/2026).
  · quote_problem— NVDA enregistré en « NVDA.PA » : Yahoo muet sur un titre que
                   BD cotait, annoncé « COURS SUSPENDU » à tort (03/08/2026).
"""
import portfolio


def q(price=None, currency="EUR", stale=False, as_of="2026-08-11", status="ok"):
    return {"price": price, "currency": currency, "stale": stale,
            "as_of": as_of, "status": status}


class TestBestPrice:
    def test_yfinance_frais_gagne(self):
        b = portfolio.best_price({"ticker": "AIR.PA", "bd_price": 200.0},
                                 q(price=211.95))
        assert (b["price"], b["source"], b["note"]) == (211.95, "yf", "")

    def test_bd_bat_un_yfinance_perime(self):
        """Le relevé BD est le cours du courtier chez qui la position est
        détenue — c'est lui qui déclenchera le SL."""
        b = portfolio.best_price(
            {"ticker": "NVDA", "bd_price": 218.42, "bd_price_currency": "USD",
             "bd_price_at": "2026-08-11T10:35"},
            q(price=200.75, currency="USD", stale=True, as_of="2026-08-08"))
        assert b["price"] == 218.42
        assert b["source"] == "bd"
        assert b["currency"] == "USD"
        assert "périmé" in b["note"]

    def test_un_releve_bd_plus_vieux_que_yfinance_ne_vaut_pas_mieux(self):
        """Session Playwright déconnectée depuis des jours : on garde yfinance,
        périmé mais moins."""
        b = portfolio.best_price(
            {"ticker": "NVDA", "bd_price": 180.0, "bd_price_at": "2026-08-01T10:00"},
            q(price=200.75, stale=True, as_of="2026-08-08"))
        assert b["price"] == 200.75
        assert b["source"] == "yf_stale"

    def test_aucune_source(self):
        b = portfolio.best_price({"ticker": "GVN.PA"}, q(price=None))
        assert b["price"] is None and b["source"] == ""

    def test_la_source_est_toujours_annoncee(self):
        """Tout cours qui ne vient pas d'un yfinance frais porte une note —
        c'est ce qui empêche d'afficher un cours mort comme s'il était live."""
        for cfg, quote in (
            ({"ticker": "X", "bd_price": 10.0, "bd_price_at": "2026-08-11T10:00"},
             q(price=9.0, stale=True, as_of="2026-08-05")),
            ({"ticker": "X"}, q(price=9.0, stale=True, as_of="2026-08-05")),
        ):
            b = portfolio.best_price(cfg, quote)
            assert b["source"] != "yf" and b["note"]


class TestQuoteProblem:
    def test_bd_cote_donc_le_ticker_est_faux(self):
        code, msg = portfolio.quote_problem(
            {"ticker": "NVDA.PA", "bd_price": 218.42, "bd_price_currency": "USD"},
            q(price=None, status="suspended"))
        assert code == "ticker"
        assert "$218.42" in msg
        assert "NON SUIVIE" in msg

    def test_vraie_suspension(self):
        code, msg = portfolio.quote_problem({"ticker": "GVN.PA"},
                                            q(price=None, status="suspended"))
        assert code == "suspended"

    def test_indisponible_sans_diagnostic(self):
        code, _ = portfolio.quote_problem({"ticker": "X.PA"}, q(price=None))
        assert code == "unavailable"

    def test_le_releve_bd_prime_sur_le_statut_yahoo(self):
        """Ordre de priorité : si BD cote, c'est un problème de ticker, JAMAIS
        une suspension — même si Yahoo dit « suspended » et que la position
        porte le drapeau worthless."""
        code, _ = portfolio.quote_problem(
            {"ticker": "X.PA", "bd_price": 5.0, "worthless": True},
            q(price=None, status="suspended"))
        assert code == "ticker"
