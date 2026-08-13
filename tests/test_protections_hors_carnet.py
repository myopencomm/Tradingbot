"""Protections portées par l'ordre d'achat : remontables ou vraiment soudées ?

`trailable` répond à UNE question — « le carnet expose-t-il une jambe de vente
annulable ? ». Ce n'est pas la même que « le bot peut-il remonter ce stop ? ».

Depuis le 05/08/2026, une position achetée en Expert garde les ids de ses deux
jambes (`protection_ids`, les `children` renvoyés par /order/create) et
`trailing.py` sait les annuler une par une pour reposer plus haut.

Le compte rendu du sync n'avait pas suivi : le 13/08/2026 il annonçait sur JNJ
« annulation depuis l'interface BD requise » alors que ses deux ids étaient en
base et que le trailing les gérait tout seul.
"""
import copy

import pytest

import bourse_direct_reader as reader
import portfolio
import sync_engine

BD = {"cash": 454.21, "orders_read": True, "programmed": [],
      "positions": [{"bd_ticker": "JNJ", "name": "Johnson & Johnson", "qty": 5,
                     "price": 261.21, "price_currency": "USD", "pru": 228.638,
                     "pru_currency": "EUR", "value_eur": 1132.08,
                     "pnl_eur": -11.11, "mic": "XNYS"}]}

# Protection rendue dans le nœud de l'ordre d'ACHAT exécuté : aucune jambe de
# vente annulable au carnet, donc `trailable` sera faux.
ORDRE_SOUDE = {
    "statut": "En cours", "sens": "Achat", "type": "Take Profit",
    "bd_ticker": "JNJ", "name": "Johnson & Johnson",
    "seuil": 248.5, "profit": 286.95, "currency": "USD",
    "order_entries": [{"id": "d9f9a48d", "text": "Achat(CPT) Ordre exécuté Seuil248.50 $US"}],
}

BASE = {"ticker": "JNJ", "qty": 5, "entry_price": 262.29, "target_low": 248.5,
        "target_high": 286.95, "autonomous": True, "bd_name": "JOHNSON & JOHNSON"}


@pytest.fixture
def sync(monkeypatch):
    saved = {}
    monkeypatch.setattr(portfolio, "save", lambda d: saved.update(copy.deepcopy(d)))

    def run(cfg):
        monkeypatch.setattr(portfolio, "load", lambda: {
            "cash_available": 454.21, "positions": {"JNJ": copy.deepcopy(cfg)},
            "auto_pending_orders": {}})
        monkeypatch.setattr(reader, "get_portfolio",
                            lambda page, send_fn=None: copy.deepcopy(
                                dict(BD, orders=[copy.deepcopy(ORDRE_SOUDE)])))
        out = []
        sync_engine.sync(None, out.append, silent=False)
        return "\n".join(out), saved

    return run


class TestAvecIds:
    """Les `children` ont été capturés à l'achat — le bot se débrouille seul."""

    def test_pas_annonce_comme_non_remontable(self, sync):
        msg, _ = sync(dict(BASE, protection_ids=["478ac2a1", "652e9f5a"]))
        assert "NON REMONTABLES" not in msg
        assert "interface BD requise" not in msg

    def test_annonce_comme_remontable_et_sans_action(self, sync):
        msg, _ = sync(dict(BASE, protection_ids=["478ac2a1", "652e9f5a"]))
        assert "REMONTABLES PAR LE BOT" in msg
        assert "2 jambes" in msg
        assert "Rien à faire" in msg

    def test_reste_marquee_protegee_et_non_trailable(self, sync):
        """Les drapeaux ne changent pas de sens : `trailable` décrit toujours le
        carnet. C'est le MESSAGE qui devait tenir compte des ids."""
        _msg, saved = sync(dict(BASE, protection_ids=["478ac2a1", "652e9f5a"]))
        assert saved["positions"]["JNJ"]["protected"] is True
        assert saved["positions"]["JNJ"]["trailable"] is False


class TestSansIds:
    """Cas réellement soudé (positions antérieures au correctif du 05/08)."""

    def test_annonce_comme_non_remontable(self, sync):
        msg, _ = sync(dict(BASE))
        assert "NON REMONTABLES" in msg
        assert "interface BD requise" in msg

    def test_pas_annonce_comme_remontable(self, sync):
        msg, _ = sync(dict(BASE))
        assert "REMONTABLES PAR LE BOT" not in msg.replace("NON REMONTABLES PAR LE BOT", "")


class TestLeTrailingSaitLesUtiliser:
    def test_la_branche_protection_ids_precede_la_branche_soudee(self):
        """Garde-fou d'ordre : si `elif protected` passait AVANT
        `elif protection_ids`, une position avec ses ids retomberait dans le cas
        « on ne peut rien faire » — le bug de message, mais en vrai."""
        import inspect

        import trailing
        src = inspect.getsource(trailing.trailing_stop_cycle)
        i_ids = src.index('elif pos.get("protection_ids")')
        i_prot = src.index('elif pos.get("protected")')
        assert i_ids < i_prot
