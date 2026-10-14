"""Passe de robustesse du chemin d'achat (14/10/2026, après KRT et DGII)."""
import analysis
import prices


def _valider(monkeypatch, texte, cours=100.0, funds=None, dossier="FICTIF"):
    import research
    monkeypatch.setattr(prices, "get_quote", lambda t: {
        "price": cours, "currency": "USD", "stale": False, "as_of": "2026-10-14", "status": "ok"})
    monkeypatch.setattr(prices, "get_technicals", lambda *a, **k: {
        "rsi": 55, "above_ma200": True, "atr_pct": 2.0, "ma200_dist_pct": 50, "momentum_1m": 12})
    monkeypatch.setattr(prices, "get_fundamentals", lambda *a, **k: funds or {"name": "Société X"})
    for nom in ("get_price_context", "get_long_term_context"):
        monkeypatch.setattr(prices, nom, lambda *a, **k: {})
    monkeypatch.setattr(prices, "get_yf_news", lambda *a, **k: [])
    monkeypatch.setattr(analysis, "_analyze_chart", lambda *a, **k: "")
    if dossier != "FICTIF":
        monkeypatch.setattr(research, "deep_research", lambda *a, **k: dossier)

    class FauxIA:
        def complete(self, *a, **k): return texte
        def complete_cheap(self, *a, **k): return texte
    return analysis.validate_candidate("XYZ", ai=FauxIA())


def test_sans_recherche_fiable_pas_d_achat_et_pas_d_appel_ia(monkeypatch):
    appels = []

    class IA:
        def complete(self, *a, **k):
            appels.append(1)
            return "VERDICT : ACHAT"
    import research
    monkeypatch.setattr(research, "deep_research", lambda *a, **k: "")
    monkeypatch.setattr(prices, "get_quote", lambda t: {"price": 10.0, "currency": "USD"})
    monkeypatch.setattr(prices, "get_technicals", lambda *a, **k: {})
    monkeypatch.setattr(prices, "get_fundamentals", lambda *a, **k: {})
    for nom in ("get_price_context", "get_long_term_context"):
        monkeypatch.setattr(prices, nom, lambda *a, **k: {})
    monkeypatch.setattr(prices, "get_yf_news", lambda *a, **k: [])
    r = analysis.validate_candidate("XYZ", ai=IA())
    assert r["verdict"] == "EXCLUS" and "recherche web" in r["reason"] and not appels


def test_cours_au_dessus_de_l_objectif_des_analystes(monkeypatch):
    """KRT : 54 $ pour un objectif moyen de 46,50 $ (3 analystes)."""
    from tests.test_thesis_gate import FORTE
    monkeypatch.setattr(analysis, "_BASE_RATES", {})
    r = _valider(monkeypatch, FORTE.replace("128.4€", "100$").replace("122.8€", "94$").replace("141.2€", "112$"),
                 cours=100.0, funds={"name": "Société X", "analyst_target": 90.0,
                                     "analyst_buy": 2, "analyst_hold": 0, "analyst_sell": 1})
    assert r["verdict"] == "EXCLUS" and "objectif moyen" in r["reason"]


def test_balayage_borne_par_le_plafond(monkeypatch):
    import sizing
    import portfolio
    import lessons
    import correlation_risk
    from unittest.mock import patch
    with patch("prices.fx_to_eur", return_value=1.0), \
         patch("prices._ticker_currency", return_value="EUR"), \
         patch("prices.get_technicals", return_value={}), \
         patch("portfolio.get_autonomous_config", return_value={"budget_total": 4000}), \
         patch("portfolio.get_managed_positions", return_value={}), \
         patch("lessons.size_factor", return_value=1.0), \
         patch("correlation_risk.size_factor", return_value=(1.0, "", None)), \
         patch("config._ttf_liable", return_value=False), \
         patch("config.MAX_POSITION_PCT", 30.0):
        plan = sizing.compute_position_size("X.PA", 50.0, 47.0, 1500.0)
    assert plan["qty"] * 50.0 <= 4000 * 0.30 + 1e-6      # jamais au-delà du plafond


def test_un_seul_achat_autonome_par_jour(monkeypatch):
    import autonomous_engine as ae
    monkeypatch.setattr(ae.portfolio, "get_autonomous_config",
                        lambda: {"entrees": [ae._jour(), "2026-01-01"]})
    assert ae.entrees_du_jour() == 1


def test_sortie_mini(monkeypatch):
    """KRT en perte : objectif abaissé au cours qui rapporte +1 € net."""
    import telegram_bot as tb
    import portfolio
    msgs = []
    monkeypatch.setattr(tb, "send", lambda m, cid=None: msgs.append(m))
    pos = {"ticker": "KRT", "qty": 29, "entry_price": 54.08, "bd_pru_raw": 48.589,
           "target_low": 50.6, "target_high": 59.3}
    monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"KRT": dict(pos)}})
    monkeypatch.setattr(tb, "stop_gain_minimum", lambda p, g=1.0: 54.73)
    monkeypatch.setattr(tb.position_view, "view", lambda n, c: {
        "price": 53.61, "sym": "$", "pnl_eur": -8.0})
    tb.cmd_sortie_mini(["KRT"], "1")
    assert "59.3 → 54.73" in msgs[-1] and "Stop inchangé : 50.6" in msgs[-1]
    monkeypatch.setattr(tb.position_view, "view", lambda n, c: {
        "price": 55.0, "sym": "$", "pnl_eur": 12.0})
    tb.cmd_sortie_mini(["KRT"], "1")
    assert "DÉJÀ au-dessus" in msgs[-1]
