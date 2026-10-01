"""Provider CLI claude : abonnement d'abord, clés API si la CLI échoue."""
import json
import subprocess

import pytest

import ai_provider


@pytest.fixture
def cli(monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(fake))
    monkeypatch.setenv("AI_USE_CLAUDE_CLI", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.delenv("AI_FINAL_PROVIDER", raising=False)
    monkeypatch.delenv("AI_DECISION_PROVIDER", raising=False)
    monkeypatch.setattr(ai_provider, "AI_PROVIDER", "anthropic")
    ai_provider.FallbackProvider._quarantaine.clear()
    return fake


def _reponse(monkeypatch, payload, vu=None):
    def run(cmd, **kw):
        if vu is not None:
            vu.update(cmd=cmd, env=kw["env"])
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
    monkeypatch.setattr(subprocess, "run", run)


def test_chaines_par_defaut(cli):
    assert ai_provider.role_chain("final") == ["claude_cli", "anthropic", "gemini"]
    # Jamais la clé API Anthropic pour les décisions courantes (budget).
    assert ai_provider.role_chain("decision") == ["claude_cli", "gemini"]


def test_cle_api_retiree_de_l_environnement(cli, monkeypatch):
    vu = {}
    _reponse(monkeypatch, {"result": "OK", "is_error": False, "usage": {}}, vu)
    assert ai_provider.ClaudeCliProvider().complete("x") == "OK"
    assert "ANTHROPIC_API_KEY" not in vu["env"]      # sinon : facturé à l'API
    assert "--tools" in vu["cmd"] and "claude-opus-5-5" in vu["cmd"]


def test_limite_d_usage_bascule_sur_les_cles(cli, monkeypatch):
    _reponse(monkeypatch, {"result": "Claude AI usage limit reached|1790000000",
                           "is_error": True})

    class FauxAPI:
        def complete(self, *a, **k): return "réponse API"

    chaine = ai_provider.FallbackProvider(["claude_cli", "anthropic"])
    chaine._instances["anthropic"] = FauxAPI()
    monkeypatch.setattr(chaine, "_notify_switch", lambda *a: None)
    assert chaine.complete("x") == "réponse API"
    # Mise en quarantaine : les appels suivants ne repassent pas par la CLI.
    assert ai_provider.FallbackProvider._en_quarantaine("claude_cli")


def test_revocation_fin_de_mois_jamais_un_week_end():
    """31/10/2026 = samedi : BD refusait la repose des protections US
    (« date de révocation … ne correspond pas au mode de règlement »)."""
    from datetime import datetime
    from bourse_direct_orders import parse_validity
    assert parse_validity("max", "XNYS", now=datetime(2026, 10, 1))[1][:10] == "2026-10-30"
    assert parse_validity("max", "XNYS", now=datetime(2026, 9, 10))[1][:10] == "2026-09-30"
    assert parse_validity("max", "XPAR", now=datetime(2028, 12, 1))[1][:10] == "2028-12-29"
