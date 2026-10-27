"""Annulation acceptée par BD mais carnet illisible (27/10/2026) : le bot
ne doit plus annoncer « la protection reste en place »."""
import bourse_direct_orders as bd_orders
import bourse_direct_reader as reader
import playwright_session
import stale_exit

POS = {"ticker": "XYZ", "qty": 10, "target_low": 40.0, "target_high": 45.0,
       "protection_ids": ["sl-id", "tp-id"]}


def _prep(monkeypatch, cancel_ok, lectures):
    monkeypatch.setattr(playwright_session, "run", lambda fn, timeout=0: fn(None))
    monkeypatch.setattr(bd_orders, "cancel_order", lambda page, o: {"ok": 1} if cancel_ok else None)
    monkeypatch.setattr(reader, "read_order_book", lambda page: [])
    it = iter(lectures)
    monkeypatch.setattr(reader, "get_portfolio", lambda page, send_fn=None: next(it))
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_annulation_acceptee_carnet_illisible_alerte_position_a_nu(monkeypatch):
    _prep(monkeypatch, True, [{"orders_read": False}] * 3)
    assert stale_exit._annuler_protections(POS, "XYZ") is False
    msg = stale_exit.message_echec_annulation(POS, "XYZ", "nouvelle protection")
    assert "SANS PROTECTION" in msg and "/ordre vendre XYZ 10 expert 40.0 45.0" in msg
    assert "reste en place" not in msg


def test_relecture_reussie_au_deuxieme_essai(monkeypatch):
    _prep(monkeypatch, True, [{"orders_read": False}, {"orders_read": True, "orders": []}])
    assert stale_exit._annuler_protections(POS, "XYZ") is True


def test_annulation_refusee_protection_reste_en_place(monkeypatch):
    _prep(monkeypatch, False, [{"orders_read": False}] * 3)
    assert stale_exit._annuler_protections(POS, "XYZ") is False
    assert "reste en place" in stale_exit.message_echec_annulation(POS, "XYZ", "vente")


def test_carnet_vide_texte_reel_bd_reconnu():
    corps = ("mes ordres mes investissements programmés votre liste d’ordres est "
             "vide pour l’instant. les ordres que vous passerez seront affichés ici.")
    assert any(m in corps for m in reader.EMPTY_ORDERS_MARKERS)
