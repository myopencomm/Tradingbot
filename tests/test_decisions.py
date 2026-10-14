"""Boucle d'apprentissage : journal, mesure en excès de l'indice, preuve."""
import random

import decisions


def test_motifs():
    assert decisions.categorie_motif("") == "achat"
    assert decisions.categorie_motif("contre-avis Jev — preuves absentes") == "veto Jev"
    assert decisions.categorie_motif("information insuffisante") == "information insuffisante"
    assert decisions.categorie_motif("analyse insuffisante — thèse creuse") == "thèse refusée par la porte"
    assert decisions.categorie_motif("surchauffe court terme (RSI 72 > 70)") == "veto quantitatif"


def test_enregistrement_une_ligne_par_jour_titre_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(decisions, "PATH", tmp_path / "j.json")
    res = {"ticker": "NVDA", "price": 100.0, "verdict": "ACHAT", "conviction": 4,
           "tech": {"rsi": 55}, "jev": {"ancrage": 0.9}}
    decisions.enregistrer(res, "standard", date="2026-10-13")
    decisions.enregistrer(dict(res, verdict="EXCLUS", reason="information insuffisante"),
                          "standard", date="2026-10-13")
    decisions.enregistrer(res, "confirm", date="2026-10-13")
    rows = decisions._load()
    assert len(rows) == 2
    assert {r["mode"] for r in rows} == {"standard", "confirm"}
    assert decisions.indice_de("NVDA") == "^GSPC" and decisions.indice_de("AIR.PA") == "^STOXX50E"


def test_piste_seulement_si_prouvee():
    random.seed(1)
    bruit = [{"date": f"2026-01-{i % 28 + 1:02d}", "ticker": f"T{i}", "mode": "standard",
              "verdict": "ACHAT" if i % 2 else "EXCLUS", "motif": "achat" if i % 2 else "veto Jev",
              "x20": random.gauss(0, 5)} for i in range(40)]
    assert not decisions.bilan(bruit)["achat_vs_exclus"]["prouve"]      # 20 cas : trop peu
    fort = [dict(r, ticker=f"U{i}", x20=(6 if r["verdict"] == "ACHAT" else -6) + random.gauss(0, 2))
            for i, r in enumerate(bruit * 2)]
    e = decisions.bilan(fort)["achat_vs_exclus"]
    assert e["prouve"] and e["diff"] > 0


def test_profil_historique(monkeypatch):
    import analysis
    monkeypatch.setattr(analysis, "_BASE_RATES", {"n": 100, "tp_pct": 36.0, "periode": "x", "profils": {
        "ma200_dist": [{"de": 45, "a": 1e9, "n": 89, "tp_pct": 45.0, "ret_moy": 2.1}],
        "rsi": [{"de": 45, "a": 55, "n": 50, "tp_pct": 33.0, "ret_moy": 0.0}]}})
    txt = analysis.profil_historique({"ma200_dist_pct": 61.0, "rsi": 47.5})
    assert "45 % des 89 trades" in txt and "+9 pts" in txt and "33 %" in txt
    monkeypatch.setattr(analysis, "_BASE_RATES", {})
    assert analysis.profil_historique({"rsi": 50}) == ""
