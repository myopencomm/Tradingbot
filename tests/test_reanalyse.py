"""Réanalyse : thèse cassée → objectif raisonnable + gain mini dès que possible."""
import reanalyse


CFG = {"target_low": 50.6, "target_high": 59.3}


def test_en_perte_objectif_ramene_sous_le_plus_haut_recent():
    """KRT : cours 53.6, plus haut 3 mois 56.06 → TP 59.3 → ~55.78, stop inchangé."""
    sl, tp = reanalyse.plan_protection(CFG, 53.6, 54.72, 56.06, 2.9)
    assert sl == 50.6 and 55.5 < tp < 56.06


def test_marge_suffisante_stop_remonte_au_gain_mini():
    sl, tp = reanalyse.plan_protection(CFG, 56.6, 54.72, 57.0, 2.9)
    assert (sl, tp) == (54.72, 59.3)       # plus haut 3 mois trop proche : TP gardé


def test_marge_insuffisante_pas_de_stop():
    """Cours juste au-dessus du niveau : un stop serait touché par le bruit."""
    plan = reanalyse.plan_protection(CFG, 55.0, 54.72, 54.0, 2.9)
    assert plan is None


def test_objectif_jamais_sous_le_gain_mini():
    sl, tp = reanalyse.plan_protection(CFG, 50.0, 54.72, 53.0, 2.9)
    assert tp >= 54.72


def test_juger_exige_un_fait(monkeypatch):
    import ai_provider
    import research
    import prices
    monkeypatch.setattr(research, "deep_research", lambda *a, **k: "dossier")
    monkeypatch.setattr(prices, "get_fundamentals", lambda *a, **k: {"name": "X"})
    monkeypatch.setattr(prices, "get_technicals", lambda *a, **k: {})
    monkeypatch.setattr(reanalyse.position_view, "view", lambda n, c: {
        "sym": "$", "price": 10.0, "chg_eur": -2.0})

    class IA:
        def __init__(self, txt): self.txt = txt
        def complete(self, *a, **k): return self.txt
    cfg = {"ticker": "X", "entry_price": 11.0}
    monkeypatch.setattr(ai_provider, "get_provider", lambda role="decision": IA("VERDICT : THESE_CASSEE — baisse"))
    assert reanalyse.juger("X", cfg)["verdict"] is None
    monkeypatch.setattr(ai_provider, "get_provider", lambda role="decision": IA(
        "VERDICT : THESE_CASSEE — le bénéfice du T2 venait d'un remboursement exceptionnel "
        "de 25,8 M$ (10-Q, 06/08/2026)"))
    assert reanalyse.juger("X", cfg)["verdict"] == "THESE_CASSEE"
    monkeypatch.setattr(research, "deep_research", lambda *a, **k: "")
    assert reanalyse.juger("X", cfg)["verdict"] is None


def test_achat_nul_seulement_pour_une_raison_de_fond(monkeypatch):
    import analysis
    def faux(raison):
        monkeypatch.setattr(analysis, "validate_candidate",
                            lambda *a, **k: {"verdict": "EXCLUS", "price": 10.0, "reason": raison})
    faux("hausse portée par remboursement exceptionnel")          # défaut de fond
    assert reanalyse.achat_nul_aujourdhui("KRT", {"ticker": "KRT"})
    faux("cours au-dessus de l'objectif moyen de 3 analystes (54 > 46.5)")
    assert reanalyse.achat_nul_aujourdhui("KRT", {"ticker": "KRT"})
    faux("surchauffe court terme (RSI 74 > 70) — attendre le repli")  # « pas maintenant »
    assert reanalyse.achat_nul_aujourdhui("X", {"ticker": "X"}) is None
    faux("profil historique sous la moyenne (34 % vs 36 %)")
    assert reanalyse.achat_nul_aujourdhui("X", {"ticker": "X"}) is None
    faux("contre-avis Jev — preuves absentes des données (p=0.07)")  # données, pas la société
    assert reanalyse.achat_nul_aujourdhui("HUM", {"ticker": "HUM"}) is None


# ── Vente au marché d'une thèse cassée près du PRU (18/11/2026, valeurs fictives)

def test_sortie_immediate_seulement_pres_du_pru(monkeypatch):
    monkeypatch.setattr(reanalyse, "SORTIE_PCT", 3.0)
    cfg = {"entry_price": 100.0}
    assert reanalyse.sortie_immediate(cfg, 100.7)       # +0,7 % : rien à protéger
    assert reanalyse.sortie_immediate(cfg, 96.0)        # sous le PRU : vendre aussi
    assert not reanalyse.sortie_immediate(cfg, 104.0)   # vrai gain : protection
    monkeypatch.setattr(reanalyse, "SORTIE_PCT", 0.0)   # désactivé
    assert not reanalyse.sortie_immediate(cfg, 100.7)


def _faux_bd(monkeypatch, connecte=True, ouvert=True, annule=True, vend=True):
    import bot_mode, market, playwright_session, stale_exit
    appels = []
    monkeypatch.setattr(bot_mode, "is_playwright", lambda: True)
    monkeypatch.setattr(playwright_session, "is_connected", lambda: connecte)
    monkeypatch.setattr(market, "is_open_now", lambda t: ouvert)
    monkeypatch.setattr(stale_exit, "_annuler_protections",
                        lambda p, n: appels.append("annule") or annule)
    monkeypatch.setattr(stale_exit, "_vendre_au_marche",
                        lambda p, n, s: appels.append("vend") or vend)
    monkeypatch.setattr(reanalyse, "_sortie_tentee", {})
    monkeypatch.setattr(reanalyse, "_sortie_annoncee", {})
    return appels


CFG_SORTIE = {"ticker": "XYZ", "entry_price": 100.0, "qty": 3,
              "target_low": 93.0, "target_high": 150.0}


def test_vente_annule_puis_vend_une_seule_fois_par_jour(monkeypatch):
    appels, msgs = _faux_bd(monkeypatch), []
    assert reanalyse.vendre_these_cassee("XYZ", CFG_SORTIE, 100.7, msgs.append)
    assert appels == ["annule", "vend"]
    assert any("vente au marché envoyée" in m for m in msgs)
    reanalyse.vendre_these_cassee("XYZ", CFG_SORTIE, 100.7, msgs.append)
    assert appels == ["annule", "vend"]                 # pas de seconde vente


def test_marche_ferme_vente_reportee_et_annoncee_une_fois(monkeypatch):
    appels, msgs = _faux_bd(monkeypatch, ouvert=False), []
    reanalyse.vendre_these_cassee("XYZ", CFG_SORTIE, 100.7, msgs.append)
    reanalyse.vendre_these_cassee("XYZ", CFG_SORTIE, 100.7, msgs.append)
    assert appels == [] and len(msgs) == 1 and "à l'ouverture" in msgs[0]


def test_annulation_non_confirmee_pas_de_vente(monkeypatch):
    appels, msgs = _faux_bd(monkeypatch, annule=False), []
    reanalyse.vendre_these_cassee("XYZ", CFG_SORTIE, 100.7, msgs.append)
    assert appels == ["annule"]                         # jamais de vente en doublon


def test_ajuster_vend_au_lieu_de_proteger(monkeypatch):
    import prices, protection_ops
    _faux_bd(monkeypatch)
    monkeypatch.setattr(reanalyse, "SORTIE_PCT", 3.0)
    monkeypatch.setattr(prices, "get_quote", lambda t: {"price": 100.7})
    monkeypatch.setattr(protection_ops, "remplacer_protection",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("protection posée")))
    vendu = []
    monkeypatch.setattr(reanalyse, "vendre_these_cassee",
                        lambda n, c, p, s: vendu.append(n) or True)
    assert reanalyse.ajuster("XYZ", CFG_SORTIE, lambda m: None)
    assert vendu == ["XYZ"]
