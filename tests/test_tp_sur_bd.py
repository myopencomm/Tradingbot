"""/tp remplace l'objectif SUR BD quand la position est protégée et la session
ouverte (11/11/2026). Valeurs fictives."""
import pytest

import bot_mode
import playwright_session
import portfolio
import telegram_bot as tb

POS = {"ticker": "XYZ", "qty": 10, "entry_price": 10.0, "target_low": 10.5,
       "target_high": 11.5, "protected": True, "protection_ids": ["a", "b"]}


@pytest.fixture
def env(monkeypatch):
    out = {"msgs": [], "remplace": [], "local": []}
    monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"XYZ": dict(POS)}})
    monkeypatch.setattr(portfolio, "best_price", lambda p: {"price": 11.0})
    monkeypatch.setattr(portfolio, "update_tp", lambda n, p: out["local"].append(p) or True)
    monkeypatch.setattr(tb, "send", lambda m, cid=None: out["msgs"].append(m))
    monkeypatch.setattr(tb, "_run_long", lambda cid, fn, *a: fn(*a))
    monkeypatch.setattr(tb, "_remplacer_protection",
                        lambda n, p, stop, tp, cid, label, ok: out["remplace"].append((stop, tp)))

    def connecte(oui):
        monkeypatch.setattr(bot_mode, "is_playwright", lambda: oui)
        monkeypatch.setattr(playwright_session, "is_connected", lambda: oui)
    return out, connecte


def test_connecte_remplace_sur_bd_en_gardant_le_stop(env):
    out, connecte = env
    connecte(True)
    tb.cmd_tp(["XYZ", "15"], 1)
    assert out["remplace"] == [(10.5, 15.0)] and out["local"] == []


def test_tp_sous_le_cours_refuse(env):
    out, connecte = env
    connecte(True)
    tb.cmd_tp(["XYZ", "10.8"], 1)
    assert out["remplace"] == [] and "refusé" in out["msgs"][-1]


def test_deconnecte_fiche_locale_et_avertissement(env):
    out, connecte = env
    connecte(False)
    tb.cmd_tp(["XYZ", "15"], 1)
    assert out["local"] == [15.0] and "fiche locale" in out["msgs"][-1]
