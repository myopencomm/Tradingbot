"""Sortie par stop suiveur (EXIT_MODE=trail, 11/11/2026). Valeurs fictives.

Stop = plus haut des clôtures depuis l'achat − TRAIL_ATR_MULT × ATR, qui ne
fait que monter ; positions AUTONOMES seulement.
"""
import pytest

import config
import trailing


@pytest.fixture(autouse=True)
def mode_trail(monkeypatch):
    monkeypatch.setattr(config, "EXIT_MODE", "trail")
    monkeypatch.setattr(config, "TRAIL_ATR_MULT", 4.0)
    monkeypatch.setattr(config, "TRAIL_LOCK", False)


def _pos(**kw):
    p = {"entry_price": 100.0, "autonomous": True, "target_low": 95.0, "target_high": 150.0}
    p.update(kw)
    return p


def test_le_stop_suit_le_plus_haut():
    # plus haut 120, ATR 2 % du cours 118 → 120 − 4 × 2,36 = 110,56
    t, step, _ = trailing.trailing_target(_pos(peak_close=120.0), 118.0, 150.0, 2.0)
    assert step == "suiveur"
    assert t == pytest.approx(120 - 4 * 118 * 0.02, abs=0.01)


def test_il_ne_redescend_jamais_sous_le_stop_actuel():
    # plus haut 104 → cible 104 − 8 = 96… mais un stop à 97 est déjà posé
    t, step, _ = trailing.trailing_target(_pos(peak_close=104.0, target_low=97.0),
                                          100.0, 150.0, 2.0)
    assert step != "suiveur"


def test_il_peut_remplacer_le_stop_initial_sous_le_pru():
    # plus haut 106, cours 105, ATR 2 % → 106 − 8,4 = 97,6 > stop initial 95
    t, step, _ = trailing.trailing_target(_pos(peak_close=106.0), 105.0, 150.0, 2.0)
    assert step == "suiveur" and 95.0 < t < 100.0


def test_le_breakeven_l_emporte_s_il_est_plus_haut():
    # +7 % : breakeven au PRU (100) > suiveur 107 − 8,56 = 98,4
    t, step, _ = trailing.trailing_target(_pos(peak_close=107.0), 107.0, 150.0, 2.0)
    assert (t, step) == (100.0, "breakeven")


def test_positions_manuelles_non_concernees():
    t, step, _ = trailing.trailing_target(_pos(peak_close=120.0, autonomous=False),
                                          118.0, 150.0, 2.0)
    assert step != "suiveur"


def test_mode_tp_desactive_le_suiveur(monkeypatch):
    monkeypatch.setattr(config, "EXIT_MODE", "tp")
    t, step, _ = trailing.trailing_target(_pos(peak_close=120.0), 118.0, 150.0, 2.0)
    assert step != "suiveur"


def test_plus_haut_jamais_en_baisse(monkeypatch):
    import pandas as pd
    import portfolio
    import prices

    class T:
        def __init__(self, t): pass
        def history(self, start): return pd.DataFrame({"Close": [101.0, 108.0, 104.0]})
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", T)
    monkeypatch.setattr(prices, "price_divisor", lambda t: 1.0)
    saved = {}
    monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"X": {}}})
    monkeypatch.setattr(portfolio, "save", lambda d: saved.update(d))
    p = trailing.update_peak("X", _pos(ticker="X", opened_at="2026-11-01T10:00:00+01:00"))
    assert p["peak_close"] == 108.0
    p = trailing.update_peak("X", dict(p, peak_close=115.0))   # déjà plus haut : gardé
    assert p["peak_close"] == 115.0


class TestTimeStop:
    @pytest.fixture
    def env(self, monkeypatch):
        from datetime import datetime, timezone
        import bot_mode
        import market
        import playwright_session
        import portfolio
        import stale_exit
        stale_exit._time_stop_done.clear()
        monkeypatch.setattr(config, "TRAIL_MAX_HOLD_DAYS", 60)
        monkeypatch.setattr(bot_mode, "is_playwright", lambda: True)
        monkeypatch.setattr(playwright_session, "is_connected", lambda: True)
        monkeypatch.setattr(market, "is_open_now", lambda t, now=None: True)
        appels = {"annule": [], "vend": []}
        monkeypatch.setattr(stale_exit, "_annuler_protections",
                            lambda pos, n: appels["annule"].append(n) or True)
        monkeypatch.setattr(stale_exit, "_vendre_au_marche",
                            lambda pos, n, s: appels["vend"].append(n) or True)

        def run(opened, autonomous=True):
            monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"XYZ": {
                "ticker": "XYZ", "qty": 5, "entry_price": 10.0,
                "autonomous": autonomous, "opened_at": opened}}})
            msgs = []
            stale_exit.time_stop_cycle(msgs.append,
                                       now=datetime(2026, 11, 11, 15, tzinfo=timezone.utc))
            return appels, msgs
        return run

    def test_vend_apres_60_jours_de_bourse(self, env):
        appels, _ = env("2026-08-01T10:00:00+00:00")
        assert appels["annule"] == ["XYZ"] and appels["vend"] == ["XYZ"]

    def test_ne_vend_pas_avant(self, env):
        appels, _ = env("2026-10-20T10:00:00+00:00")
        assert appels["vend"] == []

    def test_positions_manuelles_jamais_vendues(self, env):
        appels, _ = env("2026-01-05T10:00:00+00:00", autonomous=False)
        assert appels["vend"] == []
