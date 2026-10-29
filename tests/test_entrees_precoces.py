"""Entrées précoces (29/10/2026) : règles et mode fantôme.
Séries de prix SYNTHÉTIQUES — aucun cours réel."""
import numpy as np
import pandas as pd

import early_entries as ee


def _ohlcv(closes, vol=None, gap_last=None):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    o = c.shift(1).fillna(c.iloc[0])
    df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.005,
                       "Low": np.minimum(o, c) * 0.995, "Close": c,
                       "Volume": vol if vol is not None else 1_000_000.0}, index=idx)
    return df


def test_repli_sur_mm20_dans_la_tendance():
    # Hausse régulière d'un an, puis petit repli de 4 séances vers la MM20.
    up = list(np.linspace(50, 100, 290))
    df = _ohlcv(up + [99.6, 99.2, 98.9, 98.7])
    s = ee.latest_signals(df)
    assert s and "pullback" in s and "breakout" not in s


def test_au_plus_haut_ce_n_est_pas_un_repli():
    df = _ohlcv(list(np.linspace(50, 100, 296)))   # tout droit, au sommet
    assert "pullback" not in (ee.latest_signals(df) or {})


def test_cassure_de_base_sur_volume():
    rng = np.random.default_rng(1)
    up = list(np.linspace(50, 80, 260))
    base = list(80 + rng.uniform(-0.8, 0.8, 30))
    closes = up + base + [82.0]
    vol = [1_000_000.0] * (len(closes) - 1) + [3_000_000.0]
    s = ee.latest_signals(_ohlcv(closes, vol=vol))
    assert s and "breakout" in s


def test_gap_sur_volume_tenu():
    up = list(np.linspace(50, 80, 290))
    df = _ohlcv(up + [80, 80])
    # Gap +6 % sur volume ×4 il y a 2 séances, séance verte, cours tenu.
    j = df.index[-2]
    df.loc[j, ["Open", "Low", "High", "Close"]] = [85.0, 84.8, 87.5, 87.0]
    df.loc[j, "Volume"] = 4_000_000.0
    df.loc[df.index[-1], ["Open", "Low", "High", "Close"]] = [87.0, 86.0, 88.0, 87.5]
    s = ee.latest_signals(df)
    assert s and "gap" in s


def test_fantome_note_suit_et_fait_le_bilan():
    ind = {"AAA": {"early": {"close": 100.0, "atr_pct": 2.0, "pullback": 30.0}},
           "BBB": {"early": {"close": 50.0, "atr_pct": 2.0}},
           "HELD": {"early": {"close": 10.0, "atr_pct": 2.0, "pullback": 99.0}}}
    assert ee.noter_signaux(ind, ["BBB"], {"HELD"}, jour="2026-10-01") == 2
    assert ee.noter_signaux(ind, ["BBB"], {"HELD"}, jour="2026-10-01") == 0  # idempotent

    idx = pd.bdate_range("2026-10-01", periods=4)
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Open", "High", "Low", "Close"]])
    raw = pd.DataFrame([[100, 101, 99, 100, 50, 50.5, 49.5, 50],
                        [100, 101, 99, 100, 50, 50.5, 49.5, 50],
                        [101, 112, 100, 111, 50, 50.2, 45, 46],   # AAA → TP, BBB → SL
                        [111, 112, 110, 111, 46, 46, 45, 45]], index=idx, columns=cols, dtype=float)
    assert ee.suivre(download=lambda *a, **k: raw) == 2
    b = ee.bilan()
    assert b["pullback"]["clos"] == 1 and b["pullback"]["moyenne"] > 0
    assert b["momentum"]["clos"] == 1 and b["momentum"]["moyenne"] < 0
    assert b["pullback"]["prouvee"] is False          # 1 cas : rien de prouvé
    assert "fantôme" in ee.texte_bilan().lower() or "FANTÔMES" in ee.texte_bilan()
