"""Taille d'achat : jamais au-delà de ce que BD acceptera de couvrir.

01/10/2026 : 9 KBC.BR à 128.43 € (≈1 158 € frais compris) pour ≈1 167 € de
cash — refusé par BD « Couverture insuffisante ». Le montant seul était
comparé au cash ; ni les frais ni la marge de BD n'étaient comptés.
"""
from unittest.mock import patch

import config
import sizing


def _size(available, entry=128.434, sl=122.8):
    with patch("prices.fx_to_eur", return_value=1.0), \
         patch("prices._ticker_currency", return_value="EUR"), \
         patch("prices.get_technicals", return_value={}), \
         patch("portfolio.get_autonomous_config", return_value={"budget_total": 10000}), \
         patch("portfolio.get_managed_positions", return_value={}), \
         patch("lessons.size_factor", return_value=1.0), \
         patch("correlation_risk.size_factor", return_value=(1.0, "", None)), \
         patch.object(config, "_ttf_liable", return_value=False):
        return sizing.compute_position_size("KBC.BR", entry, sl, available)


def test_cas_kbc_un_titre_de_moins():
    plan = _size(1166.69)
    assert plan["qty"] == 8


def test_montant_plus_frais_sous_la_marge_bd():
    for cash in (900.0, 1166.69, 1172.31, 2500.0):
        plan = _size(cash)
        cost = plan["qty"] * 128.434
        total = cost + config.order_fees("KBC.BR", cost, ttf_liable=False)
        assert total <= cash * (1 - config.BD_COVERAGE_MARGIN_PCT / 100)
