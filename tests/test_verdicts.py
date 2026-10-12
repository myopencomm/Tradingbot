"""Verdicts du briefing : notés, mesurés, actionnables."""
import pandas as pd

import verdicts


def test_plan_action_distance_et_commande():
    cfg = {"target_low": 27.15, "target_high": 31.5}
    p = verdicts.plan_action("⚠️ PFE — consensus sous l'objectif", cfg, 28.12)
    assert "SL à -3.4%" in p and "TP à +12.0%" in p and "/sortir PFE" in p
    assert "/sortir" not in verdicts.plan_action("👀 CBLL — support menacé", cfg, 28.12)
    assert verdicts.plan_action("✅ JNJ — ok", cfg, 28.12) == ""


def test_enregistrement_puis_mesure(monkeypatch, tmp_path):
    import prices
    import yfinance as yf
    monkeypatch.setattr(verdicts, "PATH", tmp_path / "v.json")
    monkeypatch.setattr(prices, "get_quote", lambda t: {"price": 100.0})
    pos = {"PFE": {"ticker": "PFE"}, "JNJ": {"ticker": "JNJ"}}
    n = verdicts.enregistrer(["⚠️ PFE — risque", "✅ JNJ — ok", "⬜ X — rien"],
                             pos, date="2026-01-02")
    assert n == 2
    idx = pd.bdate_range("2026-01-02", periods=30)

    class FauxTicker:
        def __init__(self, t):
            self.t = t

        def history(self, period):
            base = 90.0 if self.t == "PFE" else 110.0
            return pd.DataFrame({"Close": [100.0] + [base] * 29}, index=idx)

    monkeypatch.setattr(yf, "Ticker", FauxTicker)
    monkeypatch.setattr(prices, "price_divisor", lambda t: 1.0)
    b = verdicts.bilan()
    assert b["⚠️"][10] == [-10.0] and b["✅"][20] == [10.0]
    assert "⚠️ 1 verdict" in verdicts.rapport()


def test_stop_gain_minimum_euros_et_devise(monkeypatch):
    """+1 € net garanti : Euronext sur le PRU, devise sur le PRU brut BD en €."""
    import prices
    import telegram_bot as tb
    monkeypatch.setattr(prices, "get_quote", lambda t: {"price": 77.46, "currency": "EUR"})
    monkeypatch.setattr(prices, "fx_to_eur", lambda c: 1.0)
    erf = {"ticker": "ERF.PA", "qty": 18, "entry_price": 75.9011}
    stop = tb.stop_gain_minimum(erf)
    assert 76.1 < stop < 76.2
    # Vendre 18 titres au stop rapporte bien ≥ 1 € net de frais de vente
    from config import order_fees
    assert stop * 18 - order_fees("ERF.PA", stop * 18, "sell") - 75.9011 * 18 >= 1.0

    monkeypatch.setattr(prices, "get_quote", lambda t: {"price": 261.44, "currency": "USD"})
    monkeypatch.setattr(prices, "fx_to_eur", lambda c: 0.894)
    jnj = {"ticker": "JNJ", "qty": 5, "entry_price": 264.0162, "bd_pru_raw": 228.638}
    s = tb.stop_gain_minimum(jnj)
    eur = s * 5 * 0.894 - order_fees("JNJ", s * 5 * 0.894, "sell")
    assert eur - 228.638 * 5 >= 1.0 and s < 264.0     # gain venu du change
