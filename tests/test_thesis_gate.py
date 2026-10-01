"""Porte d'achat : pas d'ACHAT sans analyse forte (cas KBC.BR, 01/10/2026)."""
import thesis_gate

DONNEES = """• Société X relève sa guidance 2026 : croissance du chiffre d'affaires de 12 %
après un troisième trimestre record, publié le 24/09/2026.
• Les analystes de Kepler relèvent leur objectif à 145 € (Achat).
- Prochains résultats : 2026-11-12 (dans 42 j)"""

FORTE = """VERDICT : ACHAT
Société X (X.BR) — Entrée : 128.4€  SL : 122.8€ (-4.4%)  TP : 141.2€ (+9.9%)
- Thèse : Société X a relevé sa guidance 2026 grâce à une croissance de ses revenus et le marché n'a pas fini de l'intégrer dans les estimations.
- Pourquoi maintenant : repli de 5 % sur le support des 128 € sans nouvelle négative.
- Preuve 1 : guidance relevée, croissance du chiffre d'affaires de 12 % (communiqué du 24/09/2026)
- Preuve 2 : Kepler relève son objectif à 145 € avec une recommandation Achat
- Risque principal : un ralentissement des volumes au T3 publié le 12/11 démentirait la guidance relevée.
- Invalidation : clôture sous 122.8 €
- Conviction : 4
- Risque : MEDIUM"""


def test_analyse_forte_acceptee():
    ok, motif, f = thesis_gate.check(FORTE, DONNEES, "Société X")
    assert ok, motif
    assert thesis_gate.resume(f).startswith("Société X a relevé sa guidance")
    assert f["conviction"] == 4 and len(f["preuves"]) == 2


def test_cas_kbc_entete_seul_refuse():
    val = ("KBC Group NV (KBC.BR) — Financial Services\n"
           "- Entrée : 128.434€  SL : 122.8€  TP : 141.2€\nRisque : MEDIUM")
    ok, motif, _ = thesis_gate.check(val, DONNEES, "KBC Group NV")
    assert not ok and "verdict" in motif


def test_these_creuse_refusee():
    val = FORTE.replace(
        "Société X a relevé sa guidance 2026 grâce à une croissance de ses revenus et le marché n'a pas fini de l'intégrer dans les estimations.",
        "momentum")
    ok, motif, _ = thesis_gate.check(val, DONNEES, "Société X")
    assert not ok and "thèse" in motif


def test_preuves_inventees_refusees():
    """Aucune donnée de recherche ne les porte → refus."""
    pages_navigation = ("• Investor Relations - Agenda, communiqués, présentations\n"
                        "• Consensus analystes, recommandations et objectifs de cours")
    ok, motif, _ = thesis_gate.check(FORTE, pages_navigation, "Société X")
    assert not ok and "données" in motif


def test_une_seule_preuve_refusee():
    val = FORTE.replace("- Preuve 2 : Kepler relève son objectif à 145 € avec une recommandation Achat\n", "")
    ok, motif, _ = thesis_gate.check(val, DONNEES, "Société X")
    assert not ok and "preuves" in motif


def test_conviction_faible_refusee():
    ok, motif, _ = thesis_gate.check(FORTE.replace("Conviction : 4", "Conviction : 3"),
                                     DONNEES, "Société X")
    assert not ok and "conviction" in motif


def test_verdict_exclus():
    ok, _, f = thesis_gate.check("VERDICT : EXCLUS — information insuffisante", DONNEES)
    assert not ok and f["verdict"] == "EXCLUS"


def test_yf_news_nouveau_format(monkeypatch):
    """yfinance range l'article sous `content` : l'ancien parseur n'en lisait
    aucun (zéro news fournie à l'IA jusqu'au 01/10/2026)."""
    import prices

    class FauxTicker:
        news = [{"id": "1", "content": {
            "title": "KBC relève sa guidance", "provider": {"displayName": "Reuters"},
            "pubDate": "2026-09-30T08:00:00Z", "summary": "<p>Hausse du résultat</p>"}},
            {"title": "Ancien format", "publisher": "AFP", "providerPublishTime": 1790000000}]

    monkeypatch.setattr(prices.yf, "Ticker", lambda t: FauxTicker())
    n = prices.get_yf_news("KBC.BR")
    assert n[0] == {"title": "KBC relève sa guidance", "publisher": "Reuters",
                    "date": "2026-09-30", "summary": "Hausse du résultat"}
    assert n[1]["title"] == "Ancien format" and n[1]["publisher"] == "AFP"


def _jev(monkeypatch, ancrage, specifique):
    import jev
    monkeypatch.setattr(jev, "ask", lambda *a, **k: {
        "ancrage": {"noul": ancrage}, "specifique": {"noul": specifique}})


def test_jev_veto_preuves_non_ancrees(monkeypatch):
    _jev(monkeypatch, 0.1, 0.9)
    _, _, f = thesis_gate.check(FORTE, DONNEES, "Société X")
    assert "absentes" in thesis_gate.jev_review(f, DONNEES, "Société X")["veto"]


def test_jev_incertain_ne_bloque_pas(monkeypatch):
    _jev(monkeypatch, 0.45, 0.6)
    _, _, f = thesis_gate.check(FORTE, DONNEES, "Société X")
    assert thesis_gate.jev_review(f, DONNEES, "Société X")["veto"] is None


def test_jev_absent_pas_d_avis(monkeypatch):
    import jev
    monkeypatch.setattr(jev, "ask", lambda *a, **k: None)
    _, _, f = thesis_gate.check(FORTE, DONNEES, "Société X")
    assert thesis_gate.jev_review(f, DONNEES, "Société X") is None


def test_roles_ia_budget(monkeypatch):
    """Opus seulement au contrôle final ; jamais en secours des décisions."""
    import ai_provider
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AI_FALLBACK_PROVIDERS", "gemini")
    monkeypatch.delenv("AI_DECISION_PROVIDER", raising=False)
    monkeypatch.delenv("AI_FINAL_PROVIDER", raising=False)
    monkeypatch.setattr(ai_provider, "AI_PROVIDER", "anthropic")
    assert ai_provider.role_chain("final") == ["anthropic", "gemini"]
    assert ai_provider.role_chain("decision") == ["gemini"]
