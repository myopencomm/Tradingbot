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
