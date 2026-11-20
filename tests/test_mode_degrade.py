"""Pas d'achat autonome quand un autre modèle que Claude a jugé (SWK 19/11/2026)."""
import ai_provider
import autonomous_engine


def test_degrade():
    assert autonomous_engine.degrade(["claude_cli"]) == []
    assert autonomous_engine.degrade(["claude_cli", "anthropic"]) == []
    assert autonomous_engine.degrade([]) == []
    assert autonomous_engine.degrade(None) == []
    assert autonomous_engine.degrade(["claude_cli", "gemini"]) == ["gemini"]


def test_fallback_note_qui_a_repondu(monkeypatch):
    class KO(ai_provider.AIProvider):
        def complete(self, prompt, max_tokens=800):
            raise RuntimeError("You've hit your 5-hour limit")

    class OK(ai_provider.AIProvider):
        def complete(self, prompt, max_tokens=800):
            return "ok"

    monkeypatch.setitem(ai_provider._PROVIDERS, "ko", KO)
    monkeypatch.setitem(ai_provider._PROVIDERS, "ok", OK)
    monkeypatch.setattr(ai_provider.FallbackProvider, "_notify_switch", lambda *a: None)
    fp = ai_provider.FallbackProvider(["ko", "ok"])
    assert fp.complete("x") == "ok"
    assert ai_provider.served_by(fp) == ["ok"]
