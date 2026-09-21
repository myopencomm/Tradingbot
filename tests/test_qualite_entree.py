"""Qualité d'entrée — les leçons appliquées AVANT l'ordre, pas après la perte.

AGRO (15/09/2026) est entré avec deux défauts que `lessons.post_mortem` sait
nommer depuis toujours : volume à 0.67× sa moyenne 20 j et +30.5% de momentum
sur un mois. Ils n'ont rien bloqué, parce que les leçons n'existaient que sous
forme de texte injecté dans les prompts — un rappel que le modèle pouvait
ignorer et qu'aucune règle ne vérifiait sur les chiffres.

Ces tests fixent les trois corrections du 21/09/2026 : le veto dur à l'entrée,
la capture du contexte sur TOUS les chemins d'achat, et le tag « gap » mesuré
par rapport au SL réellement posé.
"""
import pytest

import lessons


class TestVetoQualiteEntree:
    def test_agro_aurait_ete_refuse_sur_le_volume(self):
        motif = lessons.entry_quality_veto(
            {"vol_ratio": 0.67, "momentum_1m": 30.5, "rsi": 57.7})
        assert motif and "volume" in motif

    def test_envolee_du_mois_refusee(self):
        motif = lessons.entry_quality_veto({"vol_ratio": 1.4, "momentum_1m": 30.5})
        assert motif and "momentum" in motif

    def test_entree_saine_passe(self):
        assert lessons.entry_quality_veto(
            {"vol_ratio": 1.2, "momentum_1m": 8.0, "rsi": 55}) is None

    def test_donnee_absente_ne_bloque_jamais(self):
        """Un titre peu suivi rend None sur yfinance : refuser sur une absence
        d'information reviendrait à vider le vivier sans raison."""
        assert lessons.entry_quality_veto({}) is None
        assert lessons.entry_quality_veto(
            {"vol_ratio": None, "momentum_1m": None}) is None
        assert lessons.entry_quality_veto(None) is None

    def test_desactivable(self, monkeypatch):
        monkeypatch.setattr(lessons, "ENTRY_QUALITY_VETO", False)
        assert lessons.entry_quality_veto({"vol_ratio": 0.1}) is None

    def test_seuils_pilotes_par_la_config(self, monkeypatch):
        monkeypatch.setattr(lessons, "ENTRY_MIN_VOL_RATIO", 0.5)
        assert lessons.entry_quality_veto({"vol_ratio": 0.67}) is None


class TestGapRelatifAuSL:
    """Le tag « gap » ne se mesure pas à un seuil fixe : avec MAX_SL_PCT=10, un
    stop touché normalement sort à -9.6% et se faisait taguer à tort."""

    def test_stop_touche_normalement_n_est_pas_un_gap(self):
        tags = lessons.post_mortem(
            {"sl_pct": -9.6, "vol_ratio": 1.2}, 11.7454, 10.62, "loss")
        assert not any("gap" in t for t in tags)

    def test_vrai_depassement_sous_le_sl(self):
        tags = lessons.post_mortem(
            {"sl_pct": -9.6, "vol_ratio": 1.2}, 11.7454, 10.20, "loss")
        assert any("gap" in t for t in tags)

    def test_contexte_ancien_sans_sl_garde_le_repli(self):
        tags = lessons.post_mortem({"vol_ratio": 1.2}, 100.0, 90.0, "loss")
        assert any("gap" in t for t in tags)

    def test_volume_faible_reste_diagnostique(self):
        tags = lessons.post_mortem(
            {"sl_pct": -9.6, "vol_ratio": 0.67}, 11.7454, 10.62, "loss")
        assert tags == ["volume faible à l'entrée (< 0.8×)"]


class TestCaptureContexte:
    def test_distances_sl_tp(self):
        d = lessons.entry_distances(100.0, 91.0, 114.0)
        assert d == {"entry": 100.0, "sl_pct": -9.0, "tp_pct": 14.0}

    def test_distances_sans_entree(self):
        assert lessons.entry_distances(None, 91.0, 114.0) == {}

    def test_capture_complete_un_contexte_de_scan(self, monkeypatch):
        """Le contexte du scan est pris à la DÉCISION, avant que le SL réel
        soit connu : on le complète, on ne l'écrase pas."""
        import portfolio
        store = {"AGRO": {"source": "scan", "rsi": 57.7, "vol_ratio": 0.67}}
        monkeypatch.setattr(portfolio, "get_entry_context",
                            lambda t: store.get(t.upper().split(".")[0], {}))
        monkeypatch.setattr(portfolio, "set_entry_context",
                            lambda t, c: store.__setitem__(t.upper().split(".")[0], c))
        assert lessons.capture_entry_context(
            "AGRO", source="autonome", entry=11.7454, sl=10.62, tp=13.40)
        assert store["AGRO"]["source"] == "scan"      # non écrasé
        assert store["AGRO"]["rsi"] == 57.7
        assert store["AGRO"]["sl_pct"] == -9.6        # complété

    def test_capture_cree_le_contexte_absent(self, monkeypatch):
        import portfolio
        import prices
        store = {}
        monkeypatch.setattr(portfolio, "get_entry_context", lambda t: store.get(t, {}))
        monkeypatch.setattr(portfolio, "set_entry_context",
                            lambda t, c: store.__setitem__(t, c))
        monkeypatch.setattr(prices, "get_technicals",
                            lambda t: {"rsi": 61.0, "vol_ratio": 1.3, "momentum_1m": 4.2})
        monkeypatch.setattr(prices, "get_price_context", lambda t: {"perf_1y": 12.0})
        assert lessons.capture_entry_context(
            "BAC", source="sync (découverte BD)", entry=62.86, sl=58.5, tp=69.0)
        ctx = store["BAC"]
        assert ctx["source"] == "sync (découverte BD)"
        assert ctx["rsi"] == 61.0 and ctx["sl_pct"] == -6.9

    def test_capture_ne_leve_jamais(self, monkeypatch):
        """Un chemin d'ACHAT ne doit pas tomber parce que la capture échoue."""
        import prices
        def boom(_):
            raise RuntimeError("yfinance down")
        monkeypatch.setattr(prices, "get_technicals", boom)
        assert lessons.build_entry_context("XXX", source="test") == {}
