"""Scan par vagues (03/11/2026) : si le top 8 n'a aucun ACHAT, le scan descend
dans le classement ; les refus connus d'avance sont écartés sans IA.
Tickers fictifs, aucun réseau."""
from datetime import date

import pytest

import analysis


class _IA:
    def complete_cheap(self, *a, **k):
        return "XYZ : MAINTENIR — rien"

    def complete(self, *a, **k):
        return ""


@pytest.fixture
def scan(monkeypatch):
    """Lance scan_opportunities sur 30 candidats fictifs T00…T29 ; `achats`
    et `prevetos` disent lesquels sont validés ou écartés sans IA."""
    valides = []

    def lancer(achats=(), prevetos=(), max_ai=24):
        monkeypatch.setattr(analysis, "SCAN_MAX_AI", max_ai)
        monkeypatch.setattr(analysis, "SMALL_GAIN_MODE", False)
        monkeypatch.setattr(analysis, "get_provider", lambda *a, **k: _IA())
        monkeypatch.setattr(analysis.portfolio, "get_cash", lambda: 1000.0)
        monkeypatch.setattr(analysis.portfolio, "load", lambda: {"positions": {}})
        monkeypatch.setattr(analysis.portfolio, "get_autonomous_config", lambda: {})
        monkeypatch.setattr(analysis.portfolio, "add_pending_opportunity", lambda *a, **k: None)
        monkeypatch.setattr(analysis, "_portfolio_snapshot", lambda: "")
        monkeypatch.setattr(analysis, "_trading_context", lambda: "")
        monkeypatch.setattr(analysis, "_macro_context", lambda: "")
        monkeypatch.setattr(analysis, "_trigger_autonomous", lambda send_fn: None)
        monkeypatch.setattr(analysis.prices, "get_market_regime",
                            lambda: {"label": "NEUTRAL", "summary": "RÉGIME NEUTRAL"})
        monkeypatch.setattr(analysis.prices, "get_yf_news", lambda *a, **k: [])
        monkeypatch.setattr(analysis.research, "market_context", lambda: "")
        monkeypatch.setattr(analysis.research, "market_catalysts", lambda: "")
        monkeypatch.setattr(analysis, "_split_by_fee_viability", lambda c, taille: (c, []))
        cands = [{"ticker": f"T{i:02d}.PA", "score": 30 - i} for i in range(30)]
        monkeypatch.setattr(analysis, "_quant_screen", lambda *a, **k: cands)
        monkeypatch.setattr(analysis, "_pre_veto",
                            lambda t: "résultats le 05/11 (dans 2 j)" if t in prevetos else None)

        def valider(t, **k):
            valides.append(t)
            base = {"price": 10.0, "currency": "EUR", "sym": "€", "fx": 1.0,
                    "company_name": t}
            if t in achats:
                return {**base, "verdict": "ACHAT",
                        "raw": f"{t} — Entrée : 10  SL : 9.5  TP : 11.5"}
            return {**base, "verdict": "EXCLUS", "reason": "résistance proche"}
        monkeypatch.setattr(analysis, "validate_candidate", valider)
        envoyes = []
        analysis.scan_opportunities(envoyes.append, universe=[c["ticker"] for c in cands])
        return valides, envoyes[-1]
    return lancer


def test_achat_dans_le_top8_on_s_arrete_au_top8(scan):
    valides, msg = scan(achats={"T02.PA"})
    assert len(valides) == 8
    assert "OPPORTUNITÉS VALIDÉES" in msg


def test_aucun_achat_on_continue_jusqu_au_premier(scan):
    valides, msg = scan(achats={"T13.PA"})
    assert valides[-1] == "T13.PA" and len(valides) == 14
    assert "OPPORTUNITÉS VALIDÉES" in msg


def test_les_refus_connus_ne_coutent_pas_d_ia(scan):
    valides, msg = scan(prevetos={"T00.PA", "T03.PA"}, achats={"T05.PA"})
    assert "T00.PA" not in valides and "T03.PA" not in valides
    assert len(valides) == 8          # le top 8 est fait de 8 analyses IA
    assert "ÉCARTÉS SANS ANALYSE IA" in msg and "T03.PA (résultats" in msg


def test_plafond_d_analyses_ia(scan):
    valides, msg = scan(max_ai=12)
    assert len(valides) == 12
    assert "12/30 candidats examinés" in msg and "plafond de 12 analyses IA" in msg


def test_pre_veto_resultats_et_objectif(monkeypatch):
    monkeypatch.setattr(analysis.prices, "get_fundamentals", lambda t: {
        "next_earnings": "2026-11-05", "analyst_target": 50.0,
        "analyst_buy": 5, "analyst_hold": 1, "analyst_sell": 0})
    monkeypatch.setattr(analysis.prices, "get_quote", lambda t: {"price": 55.0})
    assert analysis._pre_veto("XYZ", date(2026, 11, 3)) == "résultats le 05/11 (dans 2 j)"
    assert "objectif moyen de 6 analystes" in analysis._pre_veto("XYZ", date(2026, 10, 20))
    monkeypatch.setattr(analysis.prices, "get_quote", lambda t: {"price": 45.0})
    assert analysis._pre_veto("XYZ", date(2026, 10, 20)) is None
