"""Pronostics de résultats (earnings_bets) : sélection, verdict, vérification.
Données fictives (XYZ, ABC), aucun réseau."""
from datetime import date

import pandas as pd

import earnings_bets as eb


class _IA:
    def __init__(self, raw):
        self.raw = raw

    def complete(self, prompt, max_tokens=800):
        return self.raw


def test_selection_fenetre_et_doublons():
    auj = date(2026, 11, 2)
    dates = {"XYZ": date(2026, 11, 3), "ABC": date(2026, 11, 2),      # le jour même : trop tard
             "DEF": date(2026, 11, 10), "GHI": date(2026, 11, 6), "JKL": None}
    out = eb.a_pronostiquer(list(dates), {("GHI", "2026-11-06")}, auj, prochaine=dates.get)
    assert out == [("XYZ", date(2026, 11, 3))]


def test_parse_verdict():
    assert eb.parse_verdict('ok {"sens": "Hausse", "confiance": 99, "raison": "x"}') == \
        {"sens": "hausse", "confiance": 90, "raison": "x"}
    assert eb.parse_verdict('{"sens": "neutre"}') is None
    assert eb.parse_verdict("pas de json") is None


def test_pronostiquer_puis_resoudre(monkeypatch):
    demain = date.today().toordinal() + 1
    pub = date.fromordinal(demain)
    nouveaux = eb.pronostiquer(
        ["XYZ", "ABC"], dossier=lambda t, n, d: "dossier" if t == "XYZ" else "",
        ai=_IA('{"sens": "hausse", "confiance": 60, "raison": "guidance relevée"}'),
        prochaine=lambda t: pub)
    assert [r["ticker"] for r in nouveaux] == ["XYZ"]          # ABC : pas de recherche
    # Relancer le même jour ne double pas le pronostic.
    assert eb.pronostiquer(["XYZ"], dossier=lambda *a: "dossier",
                           ai=_IA('{"sens": "baisse"}'), prochaine=lambda t: pub) == []

    idx = pd.to_datetime([date.fromordinal(demain - 1), pub, date.fromordinal(demain + 1)])
    cours = pd.DataFrame({"Close": [100.0, 95.0, 108.0]}, index=idx)
    monkeypatch.setattr("config.roundtrip_fee", lambda t, taille: 18.0)
    res = eb.resoudre(download=lambda *a, **k: cours, aujourdhui=date.fromordinal(demain + 2))
    assert len(res) == 1 and res[0]["juste"] is True
    assert res[0]["reaction_pct"] == 8.0 and res[0]["net_achat_pct"] == 6.0
    b = eb.bilan()
    assert b["resolus"] == 1 and b["justes_pct"] == 100.0 and not b["prouve"]
    assert "1/30 cas" in eb.texte_bilan()
