"""
Mode autonome : scan → entrée expert → suivi → sortie.
Le bot gère en totale autonomie un budget isolé.

- Entrée : ordre Expert achat (SL+TP sur BD) quand Playwright connecté
- Breakeven : SL relevé au PRU à +3% (vs +5% pour positions manuelles)
- Sorties : détectées via surveillance prix, exécutées par l'Expert BD
- Notifications Telegram pour chaque action
"""
import threading
import time
from datetime import datetime
import pytz

import portfolio
import prices
import bot_mode
import playwright_session

PARIS = pytz.timezone("Europe/Paris")
from config import AUTO_BREAKEVEN_PCT
BREAKEVEN_PCT = AUTO_BREAKEVEN_PCT  # trailing stop au PRU (défaut +6% — backtest 07/2026)
MAX_POSITIONS = 2     # Positions autonomes simultanées max

_entry_lock = threading.Lock()
# Anti-spam fallback gain réduit : max 1 recherche toutes les 2h
_last_smallgain_ts = 0.0
SMALLGAIN_COOLDOWN = 2 * 3600


# ─── Config ─────────────────────────────────────────────────────────────────

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
               max_positions: int = MAX_POSITIONS) -> dict:
    data = portfolio.load()
    cfg  = data.get("autonomous_config", {})
    cfg["enabled"]      = enabled
    cfg["max_positions"] = max_positions
    cfg["breakeven_pct"] = BREAKEVEN_PCT
    if budget_total is not None:
        cfg["budget_total"] = round(budget_total, 2)
        cfg.pop("budget_pct", None)
    if budget_pct is not None:
        cfg["budget_pct"]  = round(budget_pct, 1)
        cash = portfolio.get_cash()
        cfg["budget_total"] = round(cash * budget_pct / 100, 2)
    data["autonomous_config"] = cfg
    portfolio.save(data)
    return cfg


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    """Au moins un marché tradable est ouvert (Euronext OU US)."""
    now = datetime.now(PARIS)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 5 <= mins <= 21 * 60 + 55


def market_open_for(ticker: str) -> bool:
    """
    Le marché du TICKER est-il ouvert maintenant (heure de Paris) ?
    - US (pas de suffixe) : NYSE/NASDAQ 15:30-22:00 Paris
    - Londres (.L)        : LSE 9:00-17:30 Paris
    - Euronext/Xetra (défaut) : 9:00-17:30 Paris
    Sans gestion des jours fériés locaux : BD rejette alors l'ordre, le bot
    réessaie au cycle suivant (l'opportunité reste en attente).
    """
    now = datetime.now(PARIS)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    t = ticker.upper()
    is_us = "." not in t
    if is_us:
        return 15 * 60 + 35 <= mins <= 21 * 60 + 55
    return 9 * 60 + 5 <= mins <= 17 * 60 + 25



# ─── Annulation des ordres d'entrée périmés ─────────────────────────────────
# Un ordre limite d'ACHAT resté non exécuté après la clôture souffre
# d'anti-sélection : il ne se remplit plus que si le cours retombe à travers la
# limite, c'est-à-dire quand la thèse momentum est déjà invalidée (cas AF.PA
# 07/2026 : limite 13.405 posée pendant que le titre montait à 14.25, remplie
# des jours plus tard à la cassure baissière → tout droit au SL, -68€).

def _cancel_bd_order(order_id: str) -> bool:
    import bourse_direct_orders as bd_orders
    try:
        res = playwright_session.run(
            lambda page, oid=order_id: bd_orders.cancel_order(page, oid),
            timeout=30,
        )
        return bool(res)
    except Exception as e:
        print(f"[Auto] cancel BD {order_id}: {e}")
        return False


def cancel_stale_entry_orders(send_fn) -> None:
    """Annule sur BD tout ordre d'entrée autonome non exécuté dont la validité
    logique (clôture du marché du titre le jour du placement) est dépassée.
    Appelé au début de chaque cycle d'entrée et par le sync horaire — un ordre
    expiré pendant une indisponibilité du bot est annulé au retour."""
    pending = portfolio.get_auto_pending_orders()
    if not pending:
        return
    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        return
    now = datetime.now(PARIS)
    for ticker, rec in list(pending.items()):
        exp = rec.get("expires_at")
        if exp:
            try:
                expired = now > datetime.fromisoformat(exp)
            except Exception:
                expired = False
        else:
            # Enregistrement d'avant cette protection : périmé si placé un jour précédent
            placed = rec.get("placed_at", "")
            expired = bool(placed) and placed[:10] < now.strftime("%Y-%m-%d")
        if not expired:
            continue
        oid = rec.get("order_id")
        if oid and _cancel_bd_order(oid):
            portfolio.clear_auto_pending_order(ticker)
            send_fn(
                f"🗑️ {ticker} : ordre d'entrée autonome EXPIRÉ annulé sur BD "
                f"(limite {rec.get('entry')} posée le {rec.get('placed_at', '?')[:10]}, "
                f"jamais exécutée). Un limite qui traîne ne se remplit que si le "
                f"momentum s'est retourné — budget libéré."
            )
        elif not oid:
            # Pas d'order_id mémorisé : le sync réconciliera si BD l'a annulé,
            # sinon annulation manuelle nécessaire.
            send_fn(
                f"⚠️ {ticker} : ordre d'entrée autonome périmé mais sans order_id "
                f"mémorisé — annule-le sur BD : /annuler_bd {ticker}"
            )


def cancel_auto_order_if_rejected(ticker: str, reason: str, send_fn=None) -> None:
    """Une validation vient de rendre EXCLUS sur `ticker` : si un ordre d'entrée
    autonome est encore en attente sur BD pour ce même titre, la thèse qui a
    motivé l'ordre est contredite → annulation immédiate (l'ordre ne doit pas
    rester à attendre une exécution par cassure baissière)."""
    base = (ticker or "").upper().split(".")[0]
    pending = portfolio.get_auto_pending_orders()
    match = next((t for t in pending if t.upper().split(".")[0] == base), None)
    if not match:
        return
    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        return
    rec = pending[match]
    oid = rec.get("order_id")
    notify = send_fn or (lambda m: print(f"[Auto] {m}"))
    if oid and _cancel_bd_order(oid):
        portfolio.clear_auto_pending_order(match)
        notify(
            f"🗑️ {match} : ordre d'entrée autonome ANNULÉ sur BD — une nouvelle "
            f"validation vient de rejeter ce titre (« {reason[:80]} »). "
            f"La thèse d'achat n'est plus valide, l'ordre ne doit pas traîner."
        )
    elif not oid:
        notify(
            f"⚠️ {match} : validation EXCLUS (« {reason[:60]} ») mais ordre autonome "
            f"en attente sans order_id — annule-le sur BD : /annuler_bd {match}"
        )


# ─── Cycle d'entrée ─────────────────────────────────────────────────────────

def _deal_summary(ticker: str, reason: str = "") -> str:
    """3 lignes en langage SIMPLE pour un achat auto : ce que fait l'entreprise
    + pourquoi le deal peut être gagnant. Sert quand on ne reçoit qu'un ticker
    (ex: GLE.PA) sans savoir ce que c'est. Best-effort — jamais bloquant :
    généré APRÈS le placement de l'ordre, chaîne vide si l'IA/les données échouent."""
    try:
        funds = prices.get_fundamentals(ticker) or {}
        tech  = prices.get_technicals(ticker) or {}
        name  = funds.get("name") or ticker
        thesis = ""
        try:
            thesis = (portfolio.get_entry_context(ticker) or {}).get("thesis", "") or ""
        except Exception:
            pass
        thesis = thesis or reason

        facts = []
        if funds.get("sector"):
            facts.append(f"secteur {funds['sector']}")
        if tech.get("mom_12_1") is not None:
            facts.append(f"momentum 12 mois {tech['mom_12_1']:+.0f}%")
        if tech.get("above_ma200"):
            facts.append("au-dessus de sa moyenne 200 jours (tendance haussière)")
        if tech.get("rsi") is not None:
            facts.append(f"RSI {tech['rsi']:.0f}")
        if funds.get("analyst_target"):
            facts.append(f"objectif analystes {funds['analyst_target']}")
        if any(k in funds for k in ("analyst_buy", "analyst_hold", "analyst_sell")):
            facts.append(f"avis analystes {funds.get('analyst_buy',0)} achat / "
                         f"{funds.get('analyst_hold',0)} neutre / {funds.get('analyst_sell',0)} vente")
        facts_str = " ; ".join(facts) or "momentum haussier confirmé par le filtre quantitatif"

        prompt = (
            "Tu écris pour un investisseur particulier NON expert. "
            "Exactement 3 lignes, sans jargon, sans markdown, sans préambule :\n"
            f"Ligne 1 — ce que fait {name} ({ticker}) en une phrase simple et concrète.\n"
            "Ligne 2 — pourquoi ce trade peut être gagnant, en langage simple.\n"
            "Ligne 3 — la dynamique/le contexte qui soutient la hausse, en une phrase.\n\n"
            f"Données validées : {facts_str}.\n"
            f"Thèse du filtre : {thesis[:200]}\n"
            "Chaque ligne fait moins de 120 caractères."
        )
        from ai_provider import get_provider
        return get_provider().complete_cheap(prompt, max_tokens=200).strip()
    except Exception as e:
        print(f"[Auto] deal summary {ticker}: {e}")
        return ""


def _place_order(ticker: str, entry: float, sl: float, tp: float,
                 available: float, reason: str, send_fn) -> bool:
    """
    Place un ordre Expert achat sur BD et enregistre la position.
    Retourne True si réussi. Facteur commun aux deux chemins d'entrée.
    `available` est en EUR ; `entry/sl/tp` sont dans la devise du titre —
    conversion FX appliquée pour le sizing et la rentabilité.
    """
    quote_cur = prices._ticker_currency(ticker)
    fx  = prices.fx_to_eur(quote_cur)      # 1 unité devise → EUR
    sym = prices.currency_symbol(quote_cur)

    # ── SIZING PAR LE RISQUE (Phase 1, 07/2026) ──────────────────────────────
    # Fini le all-in (AF.PA : 992€ sur 1000€ de budget). La perte au SL vaut
    # RISK_PER_TRADE_PCT % du budget autonome total (fractional-Kelly
    # conservateur), coût plafonné à MAX_POSITION_PCT % du budget et au cash
    # disponible. Deux réducteurs de risque se cumulent :
    #  - série de pertes : 2 → 75%, 3 → 50%, 4+ → 35% (garde-fou données)
    #  - volatilité du titre élevée (20j > VOL_SCALE_TRIGGER × 1 an) → moitié
    #    (volatility scaling, Barroso & Santa-Clara 2015)
    from config import RISK_PER_TRADE_PCT, MAX_POSITION_PCT, VOL_SCALE_TRIGGER
    import lessons

    budget_total = portfolio.get_autonomous_config().get("budget_total", 0.0) or available
    risk_eur = budget_total * RISK_PER_TRADE_PCT / 100

    factor = lessons.size_factor()
    if factor < 1.0:
        risk_eur *= factor
        send_fn(f"🛡️ Série de {lessons.loss_streak()} perte(s) → risque réduit à "
                f"{int(factor*100)}% ({risk_eur:.0f}€ max au SL) sur {ticker}.")

    tech_sizing = prices.get_technicals(ticker) or {}
    vol_r = tech_sizing.get("vol_ratio_20_250")
    if vol_r and vol_r > VOL_SCALE_TRIGGER:
        risk_eur *= 0.5
        send_fn(f"🌊 {ticker} : volatilité 20j à {vol_r:.1f}× sa normale annuelle "
                f"→ risque réduit de moitié ({risk_eur:.0f}€ max au SL).")

    # Corrélation avec les positions déjà gérées par le bot (07/2026) : un
    # deuxième pari sur le même thème (ex: AIR + SAF) n'apporte aucune
    # diversification, même si les deux scores quant sont indépendants.
    import correlation_risk
    held_tickers = [v.get("ticker", "") for v in portfolio.get_managed_positions().values()
                    if v.get("ticker")]
    corr_factor, corr_note, corr_veto = correlation_risk.size_factor(ticker, held_tickers)
    if corr_veto:
        send_fn(f"🚫 {ticker} : entrée bloquée — {corr_veto}.")
        return False
    if corr_factor < 1.0:
        risk_eur *= corr_factor
        send_fn(f"🔗 {ticker} : {corr_note} ({risk_eur:.0f}€ max au SL).")

    entry_eur = entry * fx
    sl_dist_eur = max((entry - sl) * fx, entry_eur * 0.005)  # garde division
    qty = int(risk_eur / sl_dist_eur)

    # Plafonds : % du budget et cash réellement disponible
    cost_cap = min(available, budget_total * MAX_POSITION_PCT / 100)
    if qty * entry_eur > cost_cap:
        qty = int(cost_cap / entry_eur)
    if qty < 1:
        send_fn(
            f"🚫 {ticker} : titre trop cher pour le budget de risque — "
            f"1 titre à {entry_eur:.0f}€ dépasse le plafond ({cost_cap:.0f}€) ou "
            f"le risque au SL ({risk_eur:.0f}€). Aucune entrée."
        )
        return False
    cost_eur = round(qty * entry_eur, 2)

    # ── Garde rentabilité : les frais A/R ne doivent pas manger le gain visé ──
    from config import BROKERAGE_FEE, MIN_NET_GAIN_FEE_RATIO
    roundtrip = 2 * BROKERAGE_FEE
    gross_tp_eur = qty * (tp - entry) * fx
    if gross_tp_eur <= 0 or gross_tp_eur < roundtrip * MIN_NET_GAIN_FEE_RATIO:
        send_fn(
            f"🚫 {ticker} : achat auto annulé — gain visé {gross_tp_eur:.0f}€ trop faible "
            f"vs frais A/R {roundtrip:.2f}€ (seuil {MIN_NET_GAIN_FEE_RATIO:.0f}×). "
            f"Position trop petite pour rentabiliser les frais."
        )
        print(f"[Auto] {ticker} : gain {gross_tp_eur:.0f}€ < {roundtrip*MIN_NET_GAIN_FEE_RATIO:.0f}€ — skip frais")
        return False
    net_tp = gross_tp_eur - roundtrip

    # Filet de sécurité boucle d'apprentissage : AUCUN ordre autonome ne part
    # sans contexte d'entrée mémorisé. Si aucun chemin amont ne l'a capturé,
    # on enregistre au minimum les indicateurs techniques du moment — sinon le
    # post-mortem à la clôture est aveugle ("perte sans signal d'alerte" à tort).
    if not portfolio.get_entry_context(ticker):
        try:
            pctx = prices.get_price_context(ticker) or {}
            portfolio.set_entry_context(ticker, {
                "source":      "autonome (capture filet de sécurité)",
                "thesis":      (reason or "")[:150],
                "rsi":         tech_sizing.get("rsi"),
                "momentum_1m": tech_sizing.get("momentum_1m"),
                "mom_12_1":    tech_sizing.get("mom_12_1"),
                "above_ma200": tech_sizing.get("above_ma200"),
                "atr_pct":     tech_sizing.get("atr_pct"),
                "vol_ratio":   tech_sizing.get("vol_ratio"),
                "perf_1y":     pctx.get("perf_1y"),
                "from_52w_low": pctx.get("from_52w_low"),
                "entry":       round(entry, 4),
                "tp_pct":      round((tp - entry) / entry * 100, 1) if entry else None,
            })
        except Exception as _cx:
            print(f"[Auto] capture contexte filet {ticker}: {_cx}")

    fx_note = f" (≈{cost_eur:.0f}€ au taux {sym}→€ {fx:.3f})" if quote_cur != "EUR" else ""
    print(f"[Auto] Entrée : {ticker} {qty}t @ {entry}{sym} SL={sl} TP={tp} ({quote_cur}, coût {cost_eur:.0f}€)")
    send_fn(
        f"🤖 MODE AUTONOME — Entrée en cours\n"
        f"{ticker} | {qty} titre{'s' if qty > 1 else ''} @ {entry}{sym}\n"
        f"SL : {sl}{sym} ({(entry - sl) / entry * 100:.1f}%) | "
        f"TP : {tp}{sym} (+{(tp - entry) / entry * 100:.1f}%)\n"
        f"Coût : {qty * entry:.0f}{sym}{fx_note} | Gain net au TP ≈ +{net_tp:.0f}€ "
        f"(frais {roundtrip:.2f}€) | {reason}"
    )
    cost = cost_eur

    try:
        import bourse_direct_orders as bd_orders

        order_data = playwright_session.run(
            lambda page, t=ticker, q=qty, e=entry, s=sl, tp_=tp:
                bd_orders.create_expert_buy_order(page, t, q, e, s, tp_, "max"),
            timeout=30,
        )
        if not order_data:
            raw    = bd_orders._last_raw
            status = raw.get("status", "?")
            detail = ""
            try:
                fields = raw.get("data", {}).get("fields") or {}
                if fields:
                    detail = " — " + "; ".join(f"{k}: {v[0] if isinstance(v, list) else v}"
                                                for k, v in fields.items())
            except Exception:
                pass
            send_fn(
                f"⚠️ {ticker} : ordre rejeté par BD (HTTP {status}){detail}\n"
                f"Commande manuelle :\n"
                f"/ordre acheter {ticker} {qty} expert {entry} {sl} {tp}"
            )
            return False

        # Prix ré-arrondis au pas de cotation par le retry BD (SL haussé, TP baissé)
        adj = order_data.get("_adjusted") or {}
        if adj:
            entry = adj.get("limit") or entry
            sl    = adj.get("stop_loss") or sl
            tp    = adj.get("take_profit") or tp
            send_fn(
                f"ℹ️ {ticker} : prix ajustés au pas de cotation BD → "
                f"entrée {entry}{sym} | SL {sl}{sym} | TP {tp}{sym}"
            )

        order_id = order_data.get("id") or order_data.get("order_id")
        if not order_id:
            send_fn(f"⚠️ {ticker} : order_id manquant")
            return False

        conf = playwright_session.run(
            lambda page, oid=order_id: bd_orders.confirm_order_auto(page, oid, True),
            timeout=30,
        )
        if not conf:
            raw = bd_orders._last_raw.get("data", {}) or {}
            send_fn(
                f"⚠️ {ticker} : confirmation échouée ({raw.get('message', '?')})\n"
                f"Commande manuelle :\n"
                f"/ordre acheter {ticker} {qty} expert {entry} {sl} {tp}"
            )
            return False

        # PAS de position tant que l'ordre n'est pas EXÉCUTÉ (incident 07/07 :
        # position créée à la confirmation → confondue avec une exécution).
        # On enregistre un ordre en attente : il compte dans le budget engagé,
        # et le sync créera la position (flag autonome) à l'exécution réelle.
        # order_id + expires_at : un ordre d'entrée non exécuté à la clôture du
        # marché du titre sera ANNULÉ AUTO (cancel_stale_entry_orders) — un
        # limite qui traîne ne se remplit que si le momentum s'est retourné.
        portfolio.add_auto_pending_order(
            ticker, qty, round(entry, 4), round(sl, 4), round(tp, 4),
            order_id=order_id,
            expires_at=portfolio.market_close_expiry(ticker).isoformat(),
        )

        blurb = _deal_summary(ticker, reason)
        blurb_block = f"\n\n💡 EN BREF\n{blurb}" if blurb else ""
        send_fn(
            f"✅ ORDRE AUTONOME PLACÉ SUR BD\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{ticker} | {qty} titre{'s' if qty > 1 else ''} @ {entry}{sym} (limite)\n"
            f"SL : {sl}{sym} | TP : {tp}{sym}\n"
            f"Coût : {cost:.0f}€ | Budget auto restant : {available - cost:.0f}€\n"
            f"Position créée automatiquement à l'exécution (sync)."
            + blurb_block
        )
        # Sync silencieux différé : aligne portefeuille + cash si l'ordre
        # est exécuté immédiatement (limite au cours).
        try:
            from telegram_bot import schedule_post_order_sync
            schedule_post_order_sync()
        except Exception as e:
            print(f"[Auto] post-order sync : {e}")
        return True

    except Exception as e:
        send_fn(f"⚠️ Entrée autonome {ticker} : {e}")
        print(f"[Auto] _place_order error {ticker}: {e}")
        return False


def run_entry_cycle(send_fn) -> None:
    """
    Cherche une opportunité et entre en position.

    CHEMIN 1 (prioritaire) : opportunités validées par le briefing ou /scan du jour.
      → Même analyse complète (news, graphique, web) que ce que tu reçois dans Telegram.
      → Vérifie que le cours actuel est toujours proche de l'entrée conseillée (±3%).

    CHEMIN 2 (fallback) : scan quantitatif propre sur SCAN_UNIVERSE.
      → Utilisé uniquement si aucune opportunité en attente n'est exploitable.

    Conditions communes : mode autonome activé + Playwright connecté + marché ouvert + budget dispo.
    """
    if not is_enabled():
        return
    if not _is_market_open():
        print("[Auto] Marché fermé — pas d'entrée")
        return
    if not bot_mode.is_playwright() or not playwright_session.is_connected():
        print("[Auto] Playwright non connecté — entrée impossible")
        return

    if not _entry_lock.acquire(blocking=False):
        print("[Auto] Cycle d'entrée déjà en cours — skip")
        return

    try:
        # Purge d'abord les ordres d'entrée périmés : libère le budget engagé
        # et supprime le risque d'exécution par anti-sélection.
        try:
            cancel_stale_entry_orders(send_fn)
        except Exception as e:
            print(f"[Auto] cancel stale orders: {e}")

        cfg      = portfolio.get_autonomous_config()
        max_pos  = cfg.get("max_positions", MAX_POSITIONS)
        auto_pos = portfolio.get_autonomous_positions()
        auto_pending = portfolio.get_auto_pending_orders()

        # Les ordres en attente comptent comme des positions (fonds réservés)
        if len(auto_pos) + len(auto_pending) >= max_pos:
            print(f"[Auto] Max positions atteint ({len(auto_pos)} positions "
                  f"+ {len(auto_pending)} ordres en attente / {max_pos})")
            return

        budget_info = get_budget_info()
        # Plafonné au cash RÉEL (synchronisé depuis BD) : les fonds réservés
        # par des ordres d'achat encore en attente ne sont pas réengageables —
        # le budget théorique seul ne les voit pas (ils ne sont pas des positions).
        available = min(budget_info["available"], portfolio.get_cash())
        if available < 50:
            print(f"[Auto] Budget insuffisant ({available:.0f}€ — "
                  f"budget {budget_info['available']:.0f}€, cash {portfolio.get_cash():.0f}€)")
            return

        all_pos = portfolio.load().get("positions", {})
        held    = {v.get("ticker", "").upper() for v in all_pos.values()}
        held   |= set(auto_pending.keys())  # pas de doublon sur un ordre en attente

        # ── Chemin 1 : opportunités validées par le briefing / scan ──────────
        pending = portfolio.get_pending_opportunities()
        if pending:
            print(f"[Auto] {len(pending)} opportunité(s) en attente du briefing/scan")
            # Résumé du cycle : ce qui va être tenté et ce qui attend son marché
            statuses, actionable = [], False
            for opp in pending:
                t = opp["ticker"]
                if t.upper() in held:
                    continue
                if market_open_for(t):
                    statuses.append(f"• {t} [{opp.get('source', '?')}] — marché ouvert, évaluation")
                    actionable = True
                else:
                    open_at = "15h35" if "." not in t else "9h05"
                    statuses.append(f"• {t} [{opp.get('source', '?')}] — attend l'ouverture ({open_at} Paris)")
            if actionable and statuses:
                send_fn("🤖 Cycle d'entrée auto — opportunités en attente :\n" + "\n".join(statuses))
            for opp in pending:
                ticker = opp["ticker"]
                if ticker.upper() in held:
                    portfolio.clear_pending_opportunity(ticker)
                    continue

                # Marché du TITRE ouvert ? (un ticker US ne se trade qu'à partir
                # de 15h35 Paris — BD rejette sinon). L'opportunité RESTE en
                # attente : le prochain cycle réessaiera quand le marché ouvre.
                if not market_open_for(ticker):
                    print(f"[Auto] {ticker} : marché fermé pour ce titre — "
                          f"opportunité conservée pour le prochain cycle")
                    continue

                # Garde-fou piloté par les données : ne pas re-rentrer sur un
                # titre qui vient de coûter une perte (< 10 jours). L'IA peut
                # le re-proposer par momentum ; les données disent d'attendre.
                import lessons
                loss = lessons.recent_loss(ticker, days=10)
                if loss:
                    send_fn(f"⏸️ {ticker} : écarté — perte récente le {loss.get('date')} "
                            f"({loss.get('pnl')}€). Cooldown 10 jours pour éviter de répéter.")
                    portfolio.clear_pending_opportunity(ticker)
                    continue

                quote = prices.get_quote(ticker)
                price = quote.get("price")
                if not price:
                    continue

                entry = opp["entry"]
                sl    = opp["sl"]
                tp    = opp["tp"]

                # Vérifie que le cours n'a pas trop dérivé depuis la validation
                drift = abs(price - entry) / entry
                if drift > 0.03:
                    print(f"[Auto] {ticker} : cours {price} trop loin de l'entrée {entry} "
                          f"({drift*100:.1f}% > 3%) — skip")
                    continue

                # Comparaison budget en EUR (cours converti si titre en devise)
                fx_opp = prices.fx_to_eur(quote.get("currency") or "EUR")
                if price * fx_opp > available:
                    print(f"[Auto] {ticker} : cours {price} ({price*fx_opp:.0f}€) "
                          f"> budget {available:.0f}€")
                    continue

                # ── Gate pré-achat — SOURCE DE DÉCISION UNIQUE ───────────────
                # Les opportunités court_terme viennent d'être validées à
                # l'instant (données fraîches) → passage direct. Les autres
                # (briefing/scan validées il y a des heures) repassent par le
                # MÊME validate_candidate en mode confirm : mêmes règles, mêmes
                # leçons, même parsing que le scan — plus de veto sur des
                # critères divergents.
                if opp.get("source") == "court_terme":
                    send_fn(f"⚡ {ticker} : validé à l'instant en gain réduit — passage direct à l'ordre.")
                else:
                    send_fn(f"🔍 Contrôle pré-achat autonome — {ticker}…")
                    try:
                        import analysis as _analysis
                        res = _analysis.validate_candidate(ticker, mode="confirm",
                                                           cash=portfolio.get_cash())
                    except Exception as e:
                        send_fn(f"⚠️ {ticker} : contrôle échoué ({e}) — achat annulé par précaution")
                        portfolio.clear_pending_opportunity(ticker)
                        continue
                    if res.get("verdict") != "ACHAT":
                        send_fn(
                            f"🔴 {ticker} : contrôle pré-achat = EXCLUS — achat annulé.\n"
                            f"« {res.get('reason', 'défaut disqualifiant')} »\n"
                            f"Opportunité supprimée. Lance /scan pour une nouvelle analyse."
                        )
                        portfolio.clear_pending_opportunity(ticker)
                        continue
                    send_fn(f"✅ {ticker} : contrôle pré-achat confirme ACHAT — passage de l'ordre…")
                    # Boucle d'apprentissage : mémorise le contexte FRAIS du
                    # contrôle (RSI/momentum au moment réel de l'achat, pas de
                    # la validation d'il y a des heures), en gardant la source
                    # d'origine. Sans ça, un trade peut se clôturer avec un
                    # contexte vide → post-mortem aveugle (cas AF.PA 07/2026).
                    try:
                        fresh_ctx = dict(res.get("context") or {})
                        if fresh_ctx:
                            fresh_ctx["source"] = opp.get("source", "briefing")
                            portfolio.set_entry_context(ticker, fresh_ctx)
                    except Exception as _cx:
                        print(f"[Auto] set_entry_context {ticker}: {_cx}")

                # Re-vérifie le prix après le research (~30-60s se sont écoulés)
                quote2 = prices.get_quote(ticker)
                price2 = quote2.get("price")
                if not price2:
                    portfolio.clear_pending_opportunity(ticker)
                    continue
                drift2 = abs(price2 - entry) / entry
                if drift2 > 0.03:
                    send_fn(
                        f"⚠️ {ticker} : prix dérivé pendant le research "
                        f"({price2} vs {entry} — {drift2*100:.1f}%) — achat annulé"
                    )
                    portfolio.clear_pending_opportunity(ticker)
                    continue

                # Limite MARCHANDE : légèrement AU-DESSUS du cours pour une
                # exécution immédiate. Une limite posée sous/au cours ne se
                # remplit que si le prix retombe dessus — c'est-à-dire quand le
                # momentum a déjà tourné (anti-sélection, cas AF.PA 07/2026).
                # Le surcoût max est de 0.3% ; l'ordre non exécuté est annulé
                # à la clôture par cancel_stale_entry_orders.
                actual_entry = round(price2 * 1.003, 3)
                source_tag   = f"[{opp.get('source','briefing')}] " + opp.get("reason", "")[:80]

                success = _place_order(ticker, actual_entry, sl, tp, available, source_tag, send_fn)
                portfolio.clear_pending_opportunity(ticker)
                if success:
                    return  # Une seule entrée par cycle

        # ── Fallback GAIN RÉDUIT ─────────────────────────────────────────────
        # Fin de cycle SANS entrée : tout a été vetoé par le research, dérivé,
        # ou attend l'ouverture de son marché. Plutôt que de finir sur "rien à
        # faire", cherche un trade COURT (TP +3-8%) sur les meilleurs candidats
        # quant dont le marché est OUVERT maintenant. Analyses complètes
        # conservées : validation IA gain réduit PUIS research pré-achat.
        global _last_smallgain_ts
        try:
            from config import SMALL_GAIN_MODE
            if not SMALL_GAIN_MODE:
                print("[Auto] Aucune opportunité exploitable — gain réduit désactivé "
                      "(SMALL_GAIN_MODE=off) : zéro trade est un résultat acceptable.")
                return
            pending_now = portfolio.get_pending_opportunities()
            has_ct = any(o.get("source") == "court_terme" for o in pending_now)
            if has_ct or time.time() - _last_smallgain_ts < SMALLGAIN_COOLDOWN:
                print("[Auto] Aucune opportunité exploitable — fallback gain réduit "
                      "déjà tenté récemment ou en attente.")
                return
            _last_smallgain_ts = time.time()

            import analysis
            regime_data = prices.get_market_regime()
            if regime_data["label"] == "CRISIS":
                print("[Auto] Régime CRISIS — pas de fallback gain réduit.")
                return
            quant = analysis._quant_screen(
                analysis.SCAN_UNIVERSE, held,
                regime_data["label"], regime_data.get("index_mom_avg", 0.0) or 0.0,
            )
            quant = [c for c in quant if market_open_for(c["ticker"])]
            if not quant:
                print("[Auto] Fallback gain réduit : aucun candidat sur un marché ouvert.")
                return

            send_fn(
                "⚡ Mode auto : rien d'achetable à +10% pour l'instant "
                "(vetoé ou marché fermé) — recherche de trades courts (gain réduit)…"
            )
            opps, rej = analysis._small_gain_pass(
                analysis.get_provider(), quant, portfolio.get_cash(),
                analysis._trading_context(),
                datetime.now(PARIS).strftime("%d/%m/%Y"),
            )
            if opps:
                send_fn("⚡ OPPORTUNITÉS COURT TERME (gain réduit, 1-5 jours)\n\n"
                        + "\n\n".join(opps))
                # Nouveau cycle différé pour les traiter (le lock actuel sera libéré)
                threading.Timer(3.0, run_entry_cycle, args=(send_fn,)).start()
            else:
                msg = "⚡ Gain réduit : aucun candidat validé non plus."
                if rej:
                    msg += "\n" + "\n".join(rej)
                send_fn(msg)
        except Exception as e:
            print(f"[Auto] fallback gain réduit : {e}")

    finally:
        _entry_lock.release()


# ─── Trailing stop réel sur BD (positions auto ET manuelles) ────────────────

def trailing_stop_cycle(send_fn) -> None:
    """
    Remonte le SL au PRU (breakeven) DIRECTEMENT SUR BD pour toute position —
    autonome (+BREAKEVEN_PCT%) ou manuelle (+BREAKEVEN_THRESHOLD%) — protégée
    par un ordre Expert vente actif. Move purement protecteur : le SL ne peut
    que MONTER, le TP n'est jamais modifié.

    Les positions historiques SANS ordre Expert sur BD (ILMN, GVN, MCPHY…)
    ne sont jamais touchées : pas d'ordre à modifier = pas d'action.
    """
    from config import BREAKEVEN_THRESHOLD

    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        return
    positions = portfolio.load().get("positions", {})
    if not positions:
        return

    # 1. Positions au-dessus de leur seuil de breakeven
    candidates = []
    for name, pos in positions.items():
        if pos.get("hold"):
            continue  # HOLD long terme — hors gestion bot
        entry = pos.get("entry_price")
        if not entry or not pos.get("qty"):
            continue
        price = prices.get_quote(pos["ticker"]).get("price")
        if not price:
            continue
        change_pct = (price - entry) / entry * 100
        threshold = BREAKEVEN_PCT if pos.get("autonomous") else BREAKEVEN_THRESHOLD
        if change_pct >= threshold:
            candidates.append((name, pos, change_pct))
    if not candidates:
        return

    # 2. Ordres Expert vente actifs sur BD (une seule lecture)
    import bourse_direct_reader as reader
    import bourse_direct_orders as bd_orders
    try:
        bd = playwright_session.run(lambda page: reader.get_portfolio(page), timeout=60)
    except Exception as e:
        print(f"[Trailing] lecture BD : {e}")
        return
    if not bd:
        return
    # Ordres de PROTECTION actifs = tout ordre En cours portant un seuil SL
    # ou un profit TP, avec un order_id. On ne filtre PAS sur sens=="Vente" :
    # BD attache souvent le bracket TP/SL à l'ordre d'ACHAT exécuté (sens lu
    # "Achat"), ce qui excluait à tort ces protections du trailing (cas UNA).
    sell_orders = [o for o in bd.get("orders", [])
                   if o.get("statut") == "En cours" and o.get("order_id")
                   and (o.get("seuil") or o.get("profit"))]

    for name, pos, change_pct in candidates:
        base = pos["ticker"].upper().split(".")[0]
        target = next(
            (o for o in sell_orders
             if (o.get("bd_ticker") or "").upper() == base
             or base in (o.get("name") or "").upper()),
            None,
        )
        if not target:
            continue  # pas d'ordre Expert actif → position historique, on ne touche pas

        entry  = pos["entry_price"]
        cur_sl = target.get("seuil")
        # Le SL ne peut que MONTER : déjà au PRU ou au-dessus → rien à faire
        if cur_sl is not None and cur_sl >= entry:
            continue
        tp     = target.get("profit") or pos.get("target_high")
        new_sl = round(entry, 4)
        if not tp:
            continue

        try:
            ok_cancel = playwright_session.run(
                lambda page, oid=target["order_id"]: bd_orders.cancel_order(page, oid),
                timeout=30,
            )
            if not ok_cancel:
                send_fn(f"⚠️ Trailing {name} : annulation de l'ancien Expert impossible — SL inchangé sur BD.")
                continue

            od = playwright_session.run(
                lambda page, t=pos["ticker"], q=pos["qty"], s=new_sl, tp_=tp:
                    bd_orders.create_expert_order(page, t, q, s, tp_, "max"),
                timeout=30,
            )
            oid = od and (od.get("id") or od.get("order_id"))
            conf = None
            if oid:
                conf = playwright_session.run(
                    lambda page, o=oid: bd_orders.confirm_order_auto(page, o, False),
                    timeout=30,
                )
            if conf:
                adj    = (od.get("_adjusted") or {})
                new_sl = adj.get("stop_loss") or new_sl
                tp_f   = adj.get("take_profit") or tp
                data = portfolio.load()
                if name in data.get("positions", {}):
                    data["positions"][name]["target_low"] = new_sl
                    data["positions"][name]["auto_breakeven_notified"] = True
                    portfolio.save(data)
                tag = "🤖" if pos.get("autonomous") else "🛡️"
                send_fn(
                    f"{tag} BREAKEVEN AUTO — {name} à +{change_pct:.1f}%\n"
                    f"SL remonté au PRU sur BD : {cur_sl}€ → {new_sl}€ (TP {tp_f}€ inchangé)\n"
                    f"Perte impossible sur cette position désormais."
                )
            else:
                # L'ancien ordre est annulé mais le nouveau n'est pas passé :
                # position SANS protection → alerte forte + commande de secours.
                send_fn(
                    f"🚨 Trailing {name} : ancien Expert annulé mais NOUVEL ORDRE NON CONFIRMÉ.\n"
                    f"⚠️ POSITION SANS PROTECTION SUR BD — replace immédiatement :\n"
                    f"/ordre vendre {pos['ticker']} {pos['qty']} expert {new_sl} {tp}"
                )
        except Exception as e:
            print(f"[Trailing] {name} : {e}")
            send_fn(f"⚠️ Trailing {name} : erreur {e}")


# ─── Surveillance des positions autonomes ────────────────────────────────────

def check_autonomous_positions(send_fn) -> None:
    """
    Appelé depuis monitor.check_positions à chaque check planifié.
    Surveille : breakeven +3%, sorties SL/TP.
    """
    auto_pos = portfolio.get_autonomous_positions()
    if not auto_pos:
        return

    data    = portfolio.load()
    cfg     = data.get("autonomous_config", {})
    be_pct  = cfg.get("breakeven_pct", BREAKEVEN_PCT)
    changed = False

    for name, pos in auto_pos.items():
        quote = prices.get_quote(pos["ticker"])
        price = quote.get("price")
        if not price:
            continue

        entry      = pos["entry_price"]
        sl         = pos["target_low"]
        tp         = pos["target_high"]
        qty        = pos["qty"]
        change_pct = (price - entry) / entry * 100
        pnl        = (price - entry) * qty

        rng   = prices.get_intraday_range(pos["ticker"], hours=4) or {}
        low4h  = rng.get("low", price)
        high4h = rng.get("high", price)

        auto_tag = "🤖"

        # Sortie SL
        if low4h <= sl and not pos.get("auto_sl_exit_notified"):
            data["positions"][name]["auto_sl_exit_notified"] = True
            changed = True
            send_fn(
                f"{auto_tag} AUTO STOP-LOSS — {name}\n"
                f"Cours touché : {price}€ ≤ SL {sl}€\n"
                f"P&L estimé : {pnl:+.0f}€\n"
                f"L'Expert BD s'est exécuté. Faire /sync pour confirmer puis /remove {name}."
            )

        # Sortie TP
        elif high4h >= tp and not pos.get("auto_tp_exit_notified"):
            data["positions"][name]["auto_tp_exit_notified"] = True
            changed = True
            send_fn(
                f"{auto_tag} AUTO TAKE-PROFIT 🎯 — {name}\n"
                f"Cours touché : {price}€ ≥ TP {tp}€\n"
                f"P&L estimé : {pnl:+.0f}€\n"
                f"L'Expert BD s'est exécuté. Faire /sync pour confirmer puis /remove {name}."
            )

        # Trailing : SL → PRU à +3%.
        # Playwright connecté → trailing_stop_cycle() modifie l'ordre SUR BD ;
        # ici on n'agit qu'en mode déconnecté (alerte + commande manuelle).
        elif (change_pct >= be_pct
              and sl < entry
              and not pos.get("auto_breakeven_notified")):
            if bot_mode.is_playwright() and playwright_session.is_connected():
                continue  # géré sur BD par trailing_stop_cycle
            data["positions"][name]["target_low"]           = round(entry, 4)
            data["positions"][name]["auto_breakeven_notified"] = True
            changed = True
            send_fn(
                f"{auto_tag} AUTO BREAKEVEN — {name}\n"
                f"Position à {change_pct:+.1f}% au-dessus du PRU ({entry}€)\n"
                f"SL relevé au PRU dans le bot. P&L garanti ≥ 0.\n"
                f"Passe un nouvel Expert (SL={entry}€, TP={tp}€) via :\n"
                f"/ordre vendre {pos['ticker']} {qty} expert {entry} {tp}"
            )

    if changed:
        portfolio.save(data)
