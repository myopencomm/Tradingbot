"""Achat Expert passé à la main (/ordre) : à l'exécution, le sync rattache à
la nouvelle position les ids de ses jambes SL/TP (13/11/2026). Valeurs fictives."""
import copy

import bourse_direct_reader as reader
import portfolio
import sync_engine

BD = {"cash": 100.0, "orders_read": True, "programmed": [], "orders": [],
      "positions": [{"bd_ticker": "XYZ", "name": "XYZ Corp", "qty": 4,
                     "price": 50.0, "price_currency": "USD", "pru": 44.0,
                     "pru_currency": "EUR", "value_eur": 180.0,
                     "pnl_eur": 4.0, "mic": "XNGS"}]}


def test_ids_rattaches_a_la_position_creee(monkeypatch):
    etat = {"cash_available": 100.0, "positions": {}, "auto_pending_orders": {},
            "manual_buy_protections": {"XYZ": ["sl-id", "tp-id"]}}
    saved = {}
    monkeypatch.setattr(portfolio, "load", lambda: copy.deepcopy(etat))
    monkeypatch.setattr(portfolio, "save", lambda d: saved.update(copy.deepcopy(d)))
    monkeypatch.setattr(reader, "get_portfolio", lambda page, send_fn=None: copy.deepcopy(BD))
    sync_engine.sync(None, lambda m: None, silent=True)
    pos = next(iter(saved["positions"].values()))
    assert pos["protection_ids"] == ["sl-id", "tp-id"]
    assert not pos.get("autonomous")
    assert "XYZ" not in saved.get("manual_buy_protections", {})
