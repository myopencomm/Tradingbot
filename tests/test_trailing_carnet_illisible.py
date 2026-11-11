"""Carnet d'ordres illisible : le trailing ne doit plus abandonner.

11/11/2026 — BTSG à +8 % (seuil +6 %) n'a pas été remonté deux cycles de
suite : le carnet legacy ne trouvait pas le compte CTO et `trailing_stop_cycle`
s'arrêtait net (« carnet vide ou illisible — aucune action »). Or sa protection
était connue par ses `protection_ids`, annulables sans le carnet.

Valeurs fictives.
"""
import copy

import pytest

import bot_mode
import bourse_direct_orders as bd_orders
import bourse_direct_reader as reader
import playwright_session
import portfolio
import prices
import trailing

POS = {"ticker": "XYZ", "qty": 10, "entry_price": 100.0, "target_low": 92.0,
       "target_high": 125.0, "autonomous": True, "protected": True,
       "protection_ids": ["sl-1", "tp-1"]}


@pytest.fixture
def cycle(monkeypatch):
    actions = {"cancel": [], "create": [], "msgs": []}

    def run(pos, carnet_rows=()):
        etat = {"positions": {"XYZ": copy.deepcopy(pos)}}
        monkeypatch.setattr(portfolio, "load", lambda: copy.deepcopy(etat))
        monkeypatch.setattr(portfolio, "save", lambda d: etat.update(copy.deepcopy(d)))
        monkeypatch.setattr(portfolio, "best_price",
                            lambda p: {"price": 108.0, "source": "yf", "currency": "EUR", "note": ""})
        monkeypatch.setattr(bot_mode, "is_playwright", lambda: True)
        monkeypatch.setattr(playwright_session, "is_connected", lambda: True)
        monkeypatch.setattr(playwright_session, "run", lambda fn, timeout=None: fn(None))
        monkeypatch.setattr(reader, "read_order_book", lambda page: list(carnet_rows))
        monkeypatch.setattr(reader, "get_portfolio",
                            lambda page, send_fn=None: {"orders_read": True, "orders": []})
        monkeypatch.setattr(trailing.time, "sleep", lambda s: None)
        monkeypatch.setattr(prices, "get_technicals", lambda t: {"atr_pct": 2.0})
        monkeypatch.setattr(bd_orders, "cancel_order",
                            lambda page, oid: actions["cancel"].append(oid) or True)
        monkeypatch.setattr(bd_orders, "create_expert_order",
                            lambda page, t, q, sl, tp, v: actions["create"].append((t, q, sl, tp))
                            or {"id": "neuf", "children": ["n-sl", "n-tp"]})
        monkeypatch.setattr(bd_orders, "confirm_order_auto", lambda page, oid, x: True)
        trailing.trailing_stop_cycle(actions["msgs"].append)
        return actions, etat

    return run


def test_carnet_illisible_mais_ids_connus_le_stop_remonte(cycle):
    actions, etat = cycle(POS)
    assert actions["cancel"] == ["sl-1", "tp-1"]
    assert len(actions["create"]) == 1
    _t, _q, sl, _tp = actions["create"][0]
    assert sl == pytest.approx(100.0)                      # palier 1 : SL au PRU
    assert etat["positions"]["XYZ"]["target_low"] == pytest.approx(100.0)


def test_carnet_lisible_protection_hors_carnet_ne_plante_plus(cycle):
    """Carnet lu (une autre ligne y figure) mais la protection de XYZ est
    portée par l'ordre d'achat : la branche `protection_ids` levait
    UnboundLocalError sur `tp` depuis août 2026."""
    autre = {"ticker": "AUTRE", "ref": "r", "refbo": "rb", "limit": 10.0,
             "type": "stop", "sens": "Vente", "qty": 1}
    actions, _ = cycle(POS, carnet_rows=[autre])
    assert actions["cancel"] == ["sl-1", "tp-1"]
    assert len(actions["create"]) == 1


def test_carnet_illisible_sans_ids_rien_n_est_touche_ni_annonce_a_nu(cycle):
    pos = {k: v for k, v in POS.items() if k != "protection_ids"}
    pos["protected"] = False                               # pire cas : sync sans protection vue
    actions, _ = cycle(pos)
    assert actions["cancel"] == [] and actions["create"] == []
    assert not any("SANS" in m or "nu" in m.split() for m in actions["msgs"])
