"""
Budget, capacité d'entrée et taille de position.

« Ai-je le droit d'entrer, et pour combien ? » — la question que se posent
AUSSI BIEN le moteur autonome (avant de passer un ordre réel) que les scans
présentés à l'utilisateur (avant d'afficher une taille). Les deux DOIVENT
répondre pareil : un scan qui suggère une taille que le moteur refuserait
pousse à contourner ses propres garde-fous à la main (constaté le 28/07/2026,
LLY affiché à 89 % du cash puis refusé par le moteur).

Séparé de `autonomous_engine` parce que c'est exactement ce que `analysis` lui
empruntait — et c'est ce qui faisait tourner en rond les deux modules.
`analysis` dépend désormais de ce module-ci, pas du moteur.
"""
import bot_mode
import playwright_session
import portfolio
import prices
from config import AUTO_BREAKEVEN_PCT

MAX_POSITIONS = 2      # positions autonomes simultanées, défaut
BREAKEVEN_PCT = AUTO_BREAKEVEN_PCT

def is_enabled() -> bool:
    return bool(portfolio.get_autonomous_config().get("enabled"))


def get_budget_info() -> dict:
    """
    Retourne {total, engaged, available} en EUR.
    Engagé = positions autonomes + ordres d'achat autonomes EN ATTENTE sur BD
    (fonds réservés par BD dès le placement, pas à l'exécution).
    """
    cfg     = portfolio.get_autonomous_config()
    total   = cfg.get("budget_total", 0.0)
    engaged = 0.0
    for p in portfolio.get_autonomous_positions().values():
        fx = prices.fx_to_eur(prices._ticker_currency(p.get("ticker", "")))
        engaged += p.get("entry_price", 0) * p.get("qty", 0) * fx
    for t, o in portfolio.get_auto_pending_orders().items():
        fx = prices.fx_to_eur(prices._ticker_currency(t))
        engaged += o.get("entry", 0) * o.get("qty", 0) * fx
    return {
        "total":     round(total, 2),
        "engaged":   round(engaged, 2),
        "available": round(max(0.0, total - engaged), 2),
    }


def set_config(enabled: bool, budget_total: float | None = None,
               budget_pct: float | None = None,
               max_positions: int | None = None) -> dict:
    with portfolio.mutate() as data:
        cfg = data.get("autonomous_config", {})
        cfg["enabled"] = enabled
        # None = on CONSERVE la valeur existante. Avant, le défaut MAX_POSITIONS
        # écrasait le réglage à chaque `/auto on` : un nombre de places choisi à
        # la main était silencieusement perdu au prochain changement de budget.
        if max_positions is not None:
            cfg["max_positions"] = int(max_positions)
        else:
            cfg.setdefault("max_positions", MAX_POSITIONS)
        cfg["breakeven_pct"] = BREAKEVEN_PCT
        if budget_total is not None:
            cfg["budget_total"] = round(budget_total, 2)
            cfg.pop("budget_pct", None)
        if budget_pct is not None:
            cfg["budget_pct"] = round(budget_pct, 1)
            cfg["budget_total"] = round(
                data.get("cash_available", 0) * budget_pct / 100, 2)
        data["autonomous_config"] = cfg
    return cfg


def compute_position_size(ticker: str, entry: float, sl: float,
                          available: float, send_fn=None) -> dict:
    """
    SIZING PAR LE RISQUE — source UNIQUE, partagée par le passage d'ordre réel
    et par l'affichage du scan. Les deux DOIVENT donner le même nombre : un
    scan qui suggère une taille que le moteur refuserait pousse l'utilisateur
    à contourner ses propres garde-fous à la main (constaté le 28/07/2026 :
    LLY affiché à 89% du cash, refusé par le moteur).

    La perte au SL vaut RISK_PER_TRADE_PCT % du budget autonome (fractional
    Kelly conservateur), le coût est plafonné à MAX_POSITION_PCT % du budget
    et au cash disponible. Trois réducteurs se cumulent :
      - série de pertes : 2 → 75%, 3 → 50%, 4+ → 35%
      - volatilité 20j > VOL_SCALE_TRIGGER × la normale annuelle → moitié
      - corrélation forte avec une position détenue → moitié, ou veto

    `send_fn` (passage d'ordre réel) : notifie chaque réduction appliquée.
    Sans lui (affichage), le calcul est silencieux et sans effet de bord.

    Retourne : qty, entry_eur, risk_eur, cost_cap, notes[], veto (str|None),
    reason (str : pourquoi qty vaut 0).
    """
    from config import (RISK_PER_TRADE_PCT, MAX_POSITION_PCT, VOL_SCALE_TRIGGER,
                        CASH_SWEEP_MIN_LEFTOVER, order_fees)
    import lessons
    import correlation_risk

    def _say(msg):
        if send_fn:
            send_fn(msg)

    notes: list[str] = []
    fx = prices.fx_to_eur(prices._ticker_currency(ticker))
    budget_total = portfolio.get_autonomous_config().get("budget_total", 0.0) or available
    risk_eur = budget_total * RISK_PER_TRADE_PCT / 100

    factor = lessons.size_factor()
    if factor < 1.0:
        risk_eur *= factor
        n = (f"série de {lessons.loss_streak()} perte(s) → risque réduit à "
             f"{int(factor * 100)}%")
        notes.append(n)
        _say(f"🛡️ {n} ({risk_eur:.0f}€ max au SL) sur {ticker}.")

    vol_r = (prices.get_technicals(ticker) or {}).get("vol_ratio_20_250")
    if vol_r and vol_r > VOL_SCALE_TRIGGER:
        risk_eur *= 0.5
        n = f"volatilité 20j à {vol_r:.1f}× la normale → risque ÷2"
        notes.append(n)
        _say(f"🌊 {ticker} : {n} ({risk_eur:.0f}€ max au SL).")

    held = [v.get("ticker", "") for v in portfolio.get_managed_positions().values()
            if v.get("ticker")]
    corr_factor, corr_note, corr_veto = correlation_risk.size_factor(ticker, held)
    if corr_veto:
        return {"qty": 0, "entry_eur": entry * fx, "risk_eur": risk_eur,
                "cost_cap": 0.0, "notes": notes, "veto": corr_veto,
                "reason": corr_veto}
    if corr_factor < 1.0:
        risk_eur *= corr_factor
        notes.append(corr_note)
        _say(f"🔗 {ticker} : {corr_note} ({risk_eur:.0f}€ max au SL).")

    entry_eur   = entry * fx
    sl_dist_eur = max((entry - sl) * fx, entry_eur * 0.005)  # garde division
    qty = int(risk_eur / sl_dist_eur)

    cost_cap = min(available, budget_total * MAX_POSITION_PCT / 100)
    if qty * entry_eur > cost_cap:
        qty = int(cost_cap / entry_eur)

    # ── Balayage du reliquat de cash ─────────────────────────────────────────
    # Un fond de cash trop petit pour financer un second trade ne travaille pas.
    # S'il reste moins de CASH_SWEEP_MIN_LEFTOVER après l'achat, on agrandit la
    # position pour l'absorber, frais inclus.
    #
    # PRIME délibérément sur cost_cap : sans ça le balayage serait sans effet
    # dès que le plafond de taille est la contrainte active. La contrepartie est
    # réelle et annoncée — la perte au SL grandit dans la même proportion.
    swept_from = 0
    if qty >= 1 and CASH_SWEEP_MIN_LEFTOVER > 0 and entry_eur > 0:
        base_cost = qty * entry_eur
        leftover  = available - base_cost - order_fees(ticker, base_cost)
        if 0 < leftover < CASH_SWEEP_MIN_LEFTOVER:
            extra = 0
            while True:
                trial = (qty + extra + 1) * entry_eur
                if trial + order_fees(ticker, trial) > available:
                    break
                extra += 1
            if extra:
                swept_from = qty
                qty += extra
                new_cost = qty * entry_eur
                new_risk = qty * sl_dist_eur
                n = (f"reliquat de {leftover:.0f}€ sous le seuil de "
                     f"{CASH_SWEEP_MIN_LEFTOVER:.0f}€ → {swept_from} → {qty} titres "
                     f"({new_cost:.0f}€), risque au SL {new_risk:.0f}€ "
                     f"au lieu de {swept_from * sl_dist_eur:.0f}€")
                notes.append(n)
                _say(f"💰 {ticker} : {n}. Ce cash ne pouvait financer aucun "
                     f"autre trade — il travaille ici plutôt que de dormir.")

    reason = ""
    if qty < 1:
        reason = (f"titre trop cher pour le budget de risque — 1 titre à "
                  f"{entry_eur:.0f}€ dépasse le plafond ({cost_cap:.0f}€) ou "
                  f"le risque au SL ({risk_eur:.0f}€)")
    return {"qty": qty, "entry_eur": entry_eur, "risk_eur": risk_eur,
            "cost_cap": cost_cap, "notes": notes, "veto": None, "reason": reason,
            "swept_from": swept_from}


def entry_capacity_block(min_cash: float | None = None) -> str | None:
    """
    Blocage STRUCTUREL d'une nouvelle entrée autonome : plus aucun emplacement
    libre, ou budget/cash trop faible pour un achat viable. `None` = une entrée
    reste possible — ou le mode autonome est désactivé, et c'est alors à
    l'utilisateur de décider quoi faire d'une opportunité.

    Volontairement AUCUN test de session BD ni de cours : pas d'appel réseau,
    pas d'état transitoire (une session déconnectée se reconnecte, une place
    prise ne se libère qu'à une sortie). Sert de garde-fou EN AMONT des
    analyses IA coûteuses — scan US planifié, candidats du briefing. Sans lui,
    le bot brûle 8 validations IA pour des opportunités que rien ne pourra
    acheter (31/07/2026 : scan US de 16h lancé sur 3/3 places occupées).

    `min_cash` : plancher de cash exigé (défaut : garde-fou frais du scan).
    """
    cfg = portfolio.get_autonomous_config()
    if not cfg.get("enabled"):
        return None

    max_pos  = cfg.get("max_positions", MAX_POSITIONS)
    auto_pos = portfolio.get_autonomous_positions()
    pending  = portfolio.get_auto_pending_orders()
    used     = len(auto_pos) + len(pending)
    if used >= max_pos:
        held = ", ".join(list(auto_pos.keys()) + list(pending.keys()))
        return (f"{used}/{max_pos} emplacements occupés ({held}) — il faut une "
                f"sortie pour libérer une place (/auto positions N pour en ouvrir plus)")

    if min_cash is None:
        try:
            from config import min_viable_cash
            min_cash = min_viable_cash()
        except Exception:
            min_cash = 50.0
    available = min(get_budget_info()["available"], portfolio.get_cash())
    if available < min_cash:
        return (f"budget autonome libre {available:.0f}€ — sous le minimum de "
                f"{min_cash:.0f}€ pour qu'un achat couvre ses frais aller-retour")
    return None


def entry_blocked_reason() -> str | None:
    """
    Pourquoi le moteur autonome n'entrera PAS en position en ce moment.
    None = rien ne bloque. Sert au scan pour expliquer son inaction au lieu
    d'afficher un mode d'emploi manuel sans contexte (28/07/2026).
    """
    cfg = portfolio.get_autonomous_config()
    if not cfg.get("enabled"):
        return "mode autonome désactivé (/auto on BUDGET)"
    max_pos  = cfg.get("max_positions", MAX_POSITIONS)
    auto_pos = portfolio.get_autonomous_positions()
    pending  = portfolio.get_auto_pending_orders()
    used     = len(auto_pos) + len(pending)
    if used >= max_pos:
        held = ", ".join(list(auto_pos.keys()) + list(pending.keys()))
        return (f"{used}/{max_pos} places occupées ({held}) — "
                f"/auto positions N pour en ouvrir plus")
    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        return "session Bourse Direct non connectée (/connect)"
    info = get_budget_info()
    available = min(info["available"], portfolio.get_cash())
    if available < 50:
        return f"budget autonome insuffisant ({available:.0f}€ libre)"

    # Budget libre non nul mais trop petit pour le MOINS CHER des candidats en
    # attente : le cycle refuse alors chaque ticker un par un, en silence côté
    # Telegram (incident 28-29/07 : NVDA/LLY/JNJ recalés toutes les heures sur
    # « cours 173€ > budget 103€ », alors que le scan annonçait une entrée auto).
    # Le seuil de 50€ ne suffit pas à décrire ce cas.
    pending = portfolio.get_pending_opportunities()
    if pending:
        cheapest, cheapest_t = None, ""
        for opp in pending:
            t = opp.get("ticker", "")
            q = prices.get_quote(t)
            px = q.get("price")
            if not px:
                continue
            eur = px * prices.fx_to_eur(q.get("currency") or "EUR")
            if cheapest is None or eur < cheapest:
                cheapest, cheapest_t = eur, t
        if cheapest is not None and cheapest > available:
            return (f"budget autonome libre {available:.0f}€ — insuffisant même pour "
                    f"1 titre du moins cher en attente ({cheapest_t} à {cheapest:.0f}€). "
                    f"{info['engaged']:.0f}€ sont engagés sur {info['total']:.0f}€ de "
                    f"budget : /auto on MONTANT pour l'augmenter")
    return None