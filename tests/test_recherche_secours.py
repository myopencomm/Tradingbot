"""Recherche sourcée : secours Gemini + Google Search quand la CLI claude
est déconnectée (24/10/2026 — 11 jours sans aucun achat possible)."""
import ai_provider
import research

# conftest remplace deep_research par un dossier fictif : on garde la vraie.
_REEL = research.deep_research
DOSSIER = "ACTUALITÉS\n- 20/10/2026 — fait daté — source\n" * 20


class _Rep:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


def _gemini(queries, chunks, texte=DOSSIER):
    return _Rep({"candidates": [{"content": {"parts": [{"text": texte}]},
                                 "groundingMetadata": {
                                     "webSearchQueries": queries,
                                     "groundingChunks": [{"web": {"title": c}} for c in chunks]}}],
                 "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10}})


def _prep(monkeypatch, tmp_path, rep, cli_err=None):
    monkeypatch.setattr(research, "_DEEP_CACHE", tmp_path / "cache.json")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(research, "_alerte_cli", lambda e: None)
    monkeypatch.setattr(ai_provider, "cli_path", lambda: "/bin/claude")

    def research_cli(self, prompt):
        raise RuntimeError(cli_err or "CLI claude : Failed to authenticate: OAuth session expired")
    monkeypatch.setattr(ai_provider.ClaudeCliProvider, "research", research_cli)
    appels = []

    def post(*a, **k):
        appels.append(k)
        return rep
    monkeypatch.setattr(research.requests, "post", post)
    monkeypatch.setattr(research, "_deep_api", lambda p, t: appels.append("api") or "")
    return appels


def test_cli_deconnectee_bascule_sur_gemini(monkeypatch, tmp_path):
    appels = _prep(monkeypatch, tmp_path, _gemini(["acn news"], ["sec.gov", "reuters.com"]))
    txt = _REEL("ACN", "Accenture")
    assert "fait daté" in txt and "sec.gov" in txt
    assert appels[0]["json"]["tools"] == [{"google_search": {}}]


def test_reponse_gemini_sans_recherche_reelle_rejetee(monkeypatch, tmp_path):
    # Pas de requête ni de source : c'est la mémoire du modèle, pas un dossier du jour.
    appels = _prep(monkeypatch, tmp_path, _gemini([], []))
    assert _REEL("ACN", "Accenture") == ""
    assert len(appels) == 2          # un nouvel essai, puis abandon


def test_cli_ok_pas_d_appel_gemini(monkeypatch, tmp_path):
    appels = _prep(monkeypatch, tmp_path, _gemini(["q"], ["s"]))
    monkeypatch.setattr(ai_provider.ClaudeCliProvider, "research", lambda self, p: DOSSIER)
    assert _REEL("ACN", "Accenture") == DOSSIER
    assert appels == []


def test_gemini_desactivable(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path, _gemini(["q"], ["s"]))
    monkeypatch.setenv("DEEP_RESEARCH_GEMINI", "0")
    assert _REEL("ACN", "Accenture") == ""


def test_alerte_seulement_sur_erreur_d_authentification(monkeypatch):
    envois = []
    import tg
    monkeypatch.setattr(tg, "send", lambda t, *a, **k: envois.append(t))
    monkeypatch.setattr(research, "_last_cli_alert", 0.0)
    research._alerte_cli(RuntimeError("CLI claude : délai de 300s dépassé"))
    assert envois == []
    research._alerte_cli(RuntimeError("Failed to authenticate: OAuth session expired"))
    research._alerte_cli(RuntimeError("Failed to authenticate: OAuth session expired"))
    assert len(envois) == 1 and "setup-token" in envois[0]


def test_controle_pre_achat_cle_api_avant_gemini(monkeypatch, tmp_path):
    appels = _prep(monkeypatch, tmp_path, _gemini(["q"], ["s"]))
    monkeypatch.setattr(research, "_deep_api", lambda p, t: appels.append("api") or DOSSIER)
    assert _REEL("ACN", "Accenture", api=True) == DOSSIER
    assert appels == ["api"]         # Gemini jamais appelé


def test_scan_sans_cle_api(monkeypatch, tmp_path):
    appels = _prep(monkeypatch, tmp_path, _gemini(["q"], ["s"]))
    _REEL("ACN", "Accenture")
    assert "api" not in appels


def test_dossier_gemini_du_scan_le_pre_achat_retente_claude(monkeypatch, tmp_path):
    appels = _prep(monkeypatch, tmp_path, _gemini(["q"], ["sec.gov"]))
    gem = _REEL("ACN", "Accenture")                     # scan : Gemini, en cache
    assert _REEL("ACN", "Accenture") == gem and len(appels) == 1   # cache servi
    # Pré-achat : CLI puis clé API retentées ; si elles échouent, le dossier Gemini reste valable.
    assert _REEL("ACN", "Accenture", api=True) == gem
    assert appels[-1] == "api" and len(appels) == 2


def test_deep_api_reprend_apres_pause_turn_et_cite_les_sources(monkeypatch):
    from types import SimpleNamespace as NS
    rec = NS(type="web_search_tool_result",
             content=[NS(url="https://www.sec.gov/x"), NS(url="https://reuters.com/y")])
    reps = [NS(content=[rec], stop_reason="pause_turn"),
            NS(content=[NS(type="text", text=DOSSIER)], stop_reason="end_turn")]
    vus = []

    class FauxClient:
        class messages:
            @staticmethod
            def create(**k):
                vus.append(k)
                return reps[len(vus) - 1]

    class FauxProv(ai_provider.AnthropicProvider):
        def __init__(self):
            self.client = FauxClient()
    monkeypatch.setattr(ai_provider, "AnthropicProvider", FauxProv)
    monkeypatch.setattr(ai_provider, "_env_key", lambda n: "k")
    monkeypatch.setattr(FauxProv, "_track", staticmethod(lambda *a: None))
    txt = research._deep_api("prompt", "ACN")
    assert "sec.gov" in txt and "reuters.com" in txt and "fait daté" in txt
    assert vus[0]["tools"][0]["type"] == "web_search_20260209"
    assert vus[1]["messages"][1]["role"] == "assistant"      # tour repris


def test_deep_api_sans_recherche_rejete(monkeypatch):
    from types import SimpleNamespace as NS

    class FauxProv(ai_provider.AnthropicProvider):
        def __init__(self):
            self.client = NS(messages=NS(create=lambda **k: NS(
                content=[NS(type="text", text=DOSSIER)], stop_reason="end_turn")))
    monkeypatch.setattr(ai_provider, "AnthropicProvider", FauxProv)
    monkeypatch.setattr(ai_provider, "_env_key", lambda n: "k")
    monkeypatch.setattr(FauxProv, "_track", staticmethod(lambda *a: None))
    assert research._deep_api("prompt", "ACN") == ""
