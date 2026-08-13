"""Suivi des coûts API — non-régression du silence du 04→13/08/2026.

Pendant neuf jours, `record()` n'a rien enregistré : les deux appelants lui
passaient CINQ arguments alors qu'elle n'en acceptait que TROIS. Le `TypeError`
partait avant même d'entrer dans la fonction, donc son propre `except` ne
pouvait rien rattraper — et les appelants l'avalaient par un `except: pass`.

Conséquence invisible : `/stats` affichait des coûts figés au 04/08 et
`top_model` restait vide, alors que c'est précisément l'indicateur construit
pour révéler que le bot tournait sur Gemini depuis le 20/07.

Ces tests appellent `record()` avec les signatures RÉELLES du code appelant.
"""
import json

import pytest

import api_costs


@pytest.fixture
def couts(tmp_path, monkeypatch):
    """Redirige le fichier de coûts — jamais le vrai (données personnelles)."""
    p = tmp_path / "api_costs.json"
    p.write_text(json.dumps({"seed_usd": 0.0, "daily": {}}))
    monkeypatch.setattr(api_costs, "COSTS_PATH", p)
    return p


class TestSignature:
    def test_appel_anthropic_reel(self, couts):
        """ai_provider.AnthropicProvider._track : 5 arguments."""
        api_costs.record("claude-opus-5", 1000, 200, 500, 3000)
        assert api_costs.get_costs()["calls"] == 1

    def test_appel_gemini_reel(self, couts):
        """ai_provider.GeminiProvider._track : 5 arguments aussi."""
        api_costs.record("gemini-flash-latest", 2000, 300, 0, 1500)
        assert api_costs.get_costs()["calls"] == 1

    def test_appel_minimal_toujours_accepte(self, couts):
        api_costs.record("gemini-flash-latest", 100, 10)
        assert api_costs.get_costs()["calls"] == 1

    def test_les_appelants_du_code_correspondent(self):
        """Le contrat est vérifié sur la VRAIE signature, pas sur une copie :
        c'est leur divergence qui a causé la panne."""
        import inspect
        params = inspect.signature(api_costs.record).parameters
        assert len(params) >= 5, "record() doit accepter les compteurs de cache"


class TestQuiRepondVraiment:
    def test_la_ventilation_par_modele_est_persistee(self, couts):
        """`get_costs()` lit `daily[jour]['models']` — que `record()` doit donc
        écrire, sinon `top_model` reste vide même sans erreur."""
        api_costs.record("gemini-flash-latest", 1000, 100)
        api_costs.record("gemini-flash-latest", 1000, 100)
        api_costs.record("claude-opus-5", 1000, 100)
        c = api_costs.get_costs()
        assert c["top_model"] == "gemini-flash-latest"
        assert c["by_model"]["gemini-flash-latest"]["calls"] == 2
        assert c["by_model"]["claude-opus-5"]["calls"] == 1

    def test_le_fichier_contient_bien_les_modeles(self, couts):
        api_costs.record("gemini-flash-latest", 10, 1)
        jour = next(iter(json.loads(couts.read_text())["daily"].values()))
        assert "models" in jour


class TestFacturation:
    def test_les_jetons_de_cache_sont_factures_a_part(self, couts):
        """Écrire dans le cache coûte plus cher qu'un jeton normal, le relire
        presque rien. Les ignorer fausse la facture dans les deux sens."""
        plein  = api_costs._price("gemini-flash-latest", 1_000_000, 0)
        ecrit  = api_costs._price("gemini-flash-latest", 0, 0, 1_000_000, 0)
        relu   = api_costs._price("gemini-flash-latest", 0, 0, 0, 1_000_000)
        assert ecrit == pytest.approx(plein * api_costs.CACHE_WRITE_MULT)
        assert relu == pytest.approx(plein * api_costs.CACHE_READ_MULT)
        assert relu < plein < ecrit

    def test_modele_inconnu_facture_au_tarif_haut(self):
        """Sous-estimer une facture qu'on ne sait pas lire donne un bilan
        flatteur et faux ; la surestimer se voit."""
        assert api_costs.rates("modele-jamais-vu") == api_costs._DEFAULT_PRICING

    def test_alias_gemini_resolus_au_bon_tarif(self):
        assert api_costs.rates("gemini-2.5-flash-lite") == (0.3, 2.5)
        assert api_costs.rates("gemini-2.5-flash") == (1.5, 7.5)

    def test_les_compteurs_s_additionnent(self, couts):
        for _ in range(3):
            api_costs.record("gemini-flash-latest", 100, 10, 5, 20)
        jour = next(iter(json.loads(couts.read_text())["daily"].values()))
        assert (jour["input"], jour["output"], jour["calls"]) == (300, 30, 3)
        assert (jour["cache_write"], jour["cache_read"]) == (15, 60)


class TestRobustesse:
    def test_une_erreur_est_tracee_et_jamais_propagee(self, couts, capsys):
        """Best-effort côté appelant, mais JAMAIS silencieux : c'est le silence
        qui a coûté neuf jours."""
        api_costs.record("gemini", "pas-un-nombre", 10)   # ne doit pas lever
        assert "[api costs]" in capsys.readouterr().out

    def test_ecriture_atomique(self, couts):
        api_costs.record("gemini-flash-latest", 10, 1)
        assert not list(couts.parent.glob("*.tmp"))
        json.loads(couts.read_text())      # fichier toujours valide
