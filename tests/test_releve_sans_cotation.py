"""Relevé BD sans cotation vivante (variation « - »).

Cas fondateur, 22/09/2026 22:35 : toutes les lignes US du portefeuille BD
sortent avec un cours converti en EUR mais libellé USD et une variation « - »
(JNJ « 234.52567 USD » pour 269.19 réels). Le sync mémorisait ce relevé ; le
dashboard, qui préfère un relevé BD plus récent qu'une barre yfinance,
affichait des pertes latentes qui n'existaient pas.
"""
import copy

import pytest

import bourse_direct_reader as reader
import portfolio
import sync_engine


def _row(logline: str) -> str:
    return logline.replace(" | ", "\n")


GLITCH_JNJ = ("1 | Johnson & Johnson(XNYS) | XNYS › JNJ | 234.52567 USD | - | 3 | "
              "PRU : 220.000 € | -6.91 % | 614.40 € | -45.60 € | 10%")
VRAI_JNJ = ("1 | Johnson & Johnson(XNYS) | XNYS › JNJ | 269.19 USD | -0.10 % | 3 | "
            "PRU : 220.000 € | +6.83 % | 705.10 € | +45.10 € | 10%")
GVN = ("GV | GENOMIC VISION | 0.0018 EUR | - | 142 | PRU : 0.937 € | "
       "-99.81 % | 0.26 € | -132.79 € | <1%")


class TestParse:
    def test_variation_tiret_non_cote(self):
        assert reader._parse_position(_row(GLITCH_JNJ))["quoted"] is False

    def test_variation_chiffree_cote(self):
        assert reader._parse_position(_row(VRAI_JNJ))["quoted"] is True


LOCAL = {
    "JNJ": {"ticker": "JNJ", "qty": 3, "entry_price": 254.0,
            "target_low": 248.5, "target_high": 286.95, "bd_name": "Johnson & Johnson",
            "bd_price": 269.19, "bd_price_currency": "USD",
            "bd_value_eur": 705.10, "bd_pnl_eur": 45.10,
            "bd_price_at": "2026-09-22T21:40+02:00"},
    "GVN": {"ticker": "FR0011799907.PA", "qty": 142, "entry_price": 0.937,
            "hold": True, "worthless": True, "bd_name": "GENOMIC VISION",
            "bd_price": 0.002, "bd_price_currency": "EUR",
            "bd_value_eur": 0.28, "bd_pnl_eur": -132.77},
}


@pytest.fixture
def sync(monkeypatch):
    saved = {}
    monkeypatch.setattr(portfolio, "save", lambda d: saved.update(copy.deepcopy(d)))
    monkeypatch.setattr(portfolio, "load", lambda: {
        "cash_available": 100.0, "positions": copy.deepcopy(LOCAL),
        "auto_pending_orders": {}})

    def run(rows):
        payload = {"cash": 100.0, "orders": [], "orders_read": True, "programmed": [],
                   "positions": [reader._parse_position(_row(r)) for r in rows]}
        monkeypatch.setattr(reader, "get_portfolio",
                            lambda page, send_fn=None: copy.deepcopy(payload))
        sync_engine.sync(None, lambda *_: None, silent=True)
        return saved.get("positions") or copy.deepcopy(LOCAL)

    return run


class TestSnapshot:
    def test_releve_sans_cotation_n_ecrase_pas_le_dernier_cote(self, sync):
        jnj = sync([GLITCH_JNJ, GVN])["JNJ"]
        assert jnj["bd_price"] == 269.19
        assert jnj["bd_pnl_eur"] == 45.10
        assert jnj["bd_price_at"] == "2026-09-22T21:40+02:00"

    def test_releve_cote_est_memorise(self, sync):
        vrai = VRAI_JNJ.replace("269.19 USD", "270.00 USD")
        assert sync([vrai, GVN])["JNJ"]["bd_price"] == 270.0

    def test_titre_sans_valeur_reste_releve(self, sync):
        """GVN n'a plus JAMAIS de variation : son relevé est sa seule donnée."""
        assert sync([VRAI_JNJ, GVN])["GVN"]["bd_price"] == 0.0018
