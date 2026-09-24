"""Bot vs S&P 500, et rotation US / Euronext (24/09/2026)."""
import pandas as pd

import benchmark
import config
import portfolio
import sizing


def _serie(monkeypatch, valeurs: dict):
    s = pd.Series(valeurs)
    monkeypatch.setattr(benchmark, "_spx_eur", lambda start: s)


class TestVsSP500:
    def test_meme_capital_memes_dates(self, monkeypatch):
        # S&P +10 % entre l'achat et la vente ; le trade fait +50 € sur 1000 €
        _serie(monkeypatch, {"2026-06-01": 100.0, "2026-06-10": 110.0})
        t = {"qty": 10, "entry_price": 100.0, "currency": "EUR", "pnl": 50.0,
             "pnl_eur": 50.0, "opened_at": "2026-06-01T10:00", "closed_at": "2026-06-10T15:00"}
        r = benchmark.compare([t])
        assert r["spx_eur"] == 100.0 and r["bot_eur"] == 50.0
        assert r["ecart_eur"] == -50.0 and r["battus"] == 0

    def test_trade_us_investi_en_euros(self, monkeypatch):
        _serie(monkeypatch, {"2026-06-01": 100.0, "2026-06-10": 100.0})
        t = {"qty": 10, "entry_price": 100.0, "currency": "USD", "pnl": 20.0,
             "pnl_eur": 17.0, "opened_at": "2026-06-01", "closed_at": "2026-06-10"}
        assert benchmark._invested_eur(t) == 850.0
        assert benchmark.compare([t])["battus"] == 1

    def test_jour_sans_cotation_prend_la_seance_precedente(self, monkeypatch):
        _serie(monkeypatch, {"2026-06-05": 100.0, "2026-06-08": 105.0})
        t = {"qty": 1, "entry_price": 100.0, "pnl": 0, "opened_at": "2026-06-06",
             "closed_at": "2026-06-09"}
        assert benchmark.compare([t])["spx_eur"] == 5.0


class TestRotationUS:
    def _etat(self, monkeypatch, positions, pending=None, cap=2):
        monkeypatch.setattr(config, "MAX_US_POSITIONS", cap)
        monkeypatch.setattr(portfolio, "get_autonomous_positions",
                            lambda: {k: {"ticker": k} for k in positions})
        monkeypatch.setattr(portfolio, "get_auto_pending_orders",
                            lambda: {t: {} for t in (pending or [])})

    def test_place_us_libre(self, monkeypatch):
        self._etat(monkeypatch, ["JNJ", "AIR.PA"])
        assert sizing.us_slots_block() is None

    def test_plafond_us_atteint_ordre_en_attente_compris(self, monkeypatch):
        self._etat(monkeypatch, ["JNJ", "AIR.PA"], pending=["PFE"])
        assert "2/2 places US" in sizing.us_slots_block()
        assert sizing.market_counts() == (2, 1)

    def test_zero_desactive(self, monkeypatch):
        self._etat(monkeypatch, ["JNJ", "PFE", "CBLL"], cap=0)
        assert sizing.us_slots_block() is None
