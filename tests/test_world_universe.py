"""Univers mondial : volume du dernier jour COMPLET, dédoublonnage, pence."""
import numpy as np
import pandas as pd

import market_universe as mu


def test_volume_du_jour_partiel_ignore(monkeypatch):
    """La barre du jour, partielle, faisait écarter 97 % de l'univers."""
    idx = pd.bdate_range("2025-09-01", periods=300)
    close = pd.Series(np.linspace(100, 150, 300), index=idx)
    vol = pd.Series(1_000_000.0, index=idx)
    vol.iloc[-1] = 50_000          # séance du jour à peine commencée
    df = pd.concat({"Close": close.to_frame("X"), "High": (close * 1.01).to_frame("X"),
                    "Low": (close * 0.99).to_frame("X"), "Volume": vol.to_frame("X")}, axis=1)
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)
    assert mu.compute_indicators_bulk(["X"])["X"]["vol_ratio"] == 1.0


def test_double_cotation_ecartee():
    entries = [{"ticker": "GOOGL", "name": "Alphabet Inc.", "traded_eur": 9e9},
               {"ticker": "ABEA.DE", "name": "Alphabet Inc. Class A", "traded_eur": 1e7},
               {"ticker": "SHEL.L", "name": "Shell plc", "traded_eur": 5e8}]
    assert [e["ticker"] for e in mu._dedupe(entries, log=lambda m: None)] == ["GOOGL", "SHEL.L"]
