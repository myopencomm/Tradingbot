"""
Mode autonome : scan → entrée expert → suivi → sortie.
Le bot gère en totale autonomie un budget isolé.

- Entrée : ordre Expert achat (SL+TP sur BD) quand Playwright connecté
- Breakeven : SL relevé au PRU à +3% (vs +5% pour positions manuelles)
- Sorties : détectées via surveillance prix, exécutées par l'Expert BD
- Notifications Telegram pour chaque action
"""
import threading
from datetime import datetime
import pytz

import portfolio
import prices
import bot_mode
import playwright_session

PARIS = pytz.timezone("Europe/Paris")
BREAKEVEN_PCT = 3.0   # +3% → trailing stop au PRU
MAX_POSITIONS = 2     # Positions autonomes simultanées max

_entry_lock = threading.Lock()


# ─── Config ─────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return bool(portfolio.get_autonomous_config().get("enabled"))


def get_budget_info() -> dict:
    """Retourne {total, engaged, available}."""
    cfg     = portfolio.get_autonomous_config()
    total   = cfg.get("budget_total", 0.0)
    auto_pos = portfolio.get_autonomous_positions()
    engaged = sum(p.get("entry_price", 0) * p.get("qty", 0) for p in auto_pos.values())
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
    now = datetime.now(PARIS)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 5 <= mins <= 17 * 60 + 35



# ─── Cycle d'entrée ─────────────────────────────────────────────────────────

def _place_order(ticker: str, entry: float, sl: float, tp: float,
                 available: float, reason: str, send_fn) -> bool:
    """
    Place un ordre Expert achat sur BD et enregistre la position.
    Retourne True si réussi. Facteur commun aux deux chemins d'entrée.
    """
    qty  = max(1, int(available / entry))
    cost = qty * entry
    if cost > available * 1.01:
        qty -= 1
    if qty < 1:
        return False
    cost = round(qty * entry, 2)

    # ── Garde rentabilité : les frais A/R ne doivent pas manger le gain visé ──
    from config import BROKERAGE_FEE, MIN_NET_GAIN_FEE_RATIO
    roundtrip = 2 * BROKERAGE_FEE
    gross_tp  = qty * (tp - entry)
    if gross_tp <= 0 or gross_tp < roundtrip * MIN_NET_GAIN_FEE_RATIO:
        send_fn(
            f"🚫 {ticker} : achat auto annulé — gain visé {gross_tp:.0f}€ trop faible "
            f"vs frais A/R {roundtrip:.2f}€ (seuil {MIN_NET_GAIN_FEE_RATIO:.0f}×). "
            f"Position trop petite pour rentabiliser les frais."
        )
        print(f"[Auto] {ticker} : gain {gross_tp:.0f}€ < {roundtrip*MIN_NET_GAIN_FEE_RATIO:.0f}€ — skip frais")
        return False
    net_tp = gross_tp - roundtrip

    print(f"[Auto] Entrée : {ticker} {qty}t @ {entry} SL={sl} TP={tp}")
    send_fn(
        f"🤖 MODE AUTONOME — Entrée en cours\n"
        f"{ticker} | {qty} titre{'s' if qty > 1 else ''} @ {entry}€\n"
        f"SL : {sl}€ ({(entry - sl) / entry * 100:.1f}%) | "
        f"TP : {tp}€ (+{(tp - entry) / entry * 100:.1f}%)\n"
        f"Coût : {cost:.0f}€ | Gain net au TP ≈ +{net_tp:.0f}€ (frais {roundtrip:.2f}€) | {reason}"
    )

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

        order_id = order_data.get("id") or order_data.get("order_id")
        if not order_id:
            send_fn(f"⚠️ {ticker} : order_id manquant")
            return False

        conf = playwright_session.run(
            lambda page, oid=order_id: bd_orders.execute_strategy(page, oid),
            timeout=30,
        )
        if not conf:
            send_fn(f"⚠️ {ticker} : confirmation échouée")
            return False

        name = ticker.split(".")[0].upper()
        data = portfolio.load()
        data.setdefault("positions", {})[name] = {
            "ticker":      ticker,
            "qty":         qty,
            "entry_price": round(entry, 4),
            "target_high": round(tp, 4),
            "target_low":  round(sl, 4),
            "autonomous":  True,
            "auto_reason": reason,
        }
        portfolio.save(data)

        send_fn(
            f"✅ ACHAT AUTONOME CONFIRMÉ\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{ticker} | {qty} titre{'s' if qty > 1 else ''} @ {entry}€\n"
            f"SL : {sl}€ | TP : {tp}€\n"
            f"Coût : {cost:.0f}€ | Budget restant : {available - cost:.0f}€"
        )
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
        cfg      = portfolio.get_autonomous_config()
        max_pos  = cfg.get("max_positions", MAX_POSITIONS)
        auto_pos = portfolio.get_autonomous_positions()

        if len(auto_pos) >= max_pos:
            print(f"[Auto] Max positions atteint ({len(auto_pos)}/{max_pos})")
            return

        budget_info = get_budget_info()
        available   = budget_info["available"]
        if available < 50:
            print(f"[Auto] Budget insuffisant ({available:.0f}€)")
            return

        all_pos = portfolio.load().get("positions", {})
        held    = {v.get("ticker", "").upper() for v in all_pos.values()}

        # ── Chemin 1 : opportunités validées par le briefing / scan ──────────
        pending = portfolio.get_pending_opportunities()
        if pending:
            print(f"[Auto] {len(pending)} opportunité(s) en attente du briefing/scan")
            for opp in pending:
                ticker = opp["ticker"]
                if ticker.upper() in held:
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

                if price > available:
                    print(f"[Auto] {ticker} : cours {price} > budget {available:.0f}€")
                    continue

                # ── Research pré-achat : confirmation indépendante avant ordre réel ──
                send_fn(f"🔍 Vérification research avant achat autonome — {ticker}…")
                research_lines: list[str] = []

                def _capture(msg, _buf=research_lines):
                    _buf.append(msg)
                    send_fn(msg)

                # Trade court terme (gain réduit) : le critère TP +10% ne
                # s'applique pas — le research juge la faisabilité du TP réel.
                tp_pct = round((tp - entry) / entry * 100, 1)
                question = ""
                min_tp = None
                if opp.get("source") == "court_terme":
                    min_tp = tp_pct
                    question = (
                        f"Trade COURT TERME (1-5 jours) visant seulement +{tp_pct}% "
                        f"(TP {tp}). Le momentum court terme rend-il cet objectif "
                        f"très probable ? Le critère TP +10% ne s'applique PAS ici."
                    )

                try:
                    import analysis as _analysis
                    _analysis.research_ticker(_capture, ticker, question, min_tp_pct=min_tp)
                except Exception as e:
                    send_fn(f"⚠️ {ticker} : research échoué ({e}) — achat annulé par précaution")
                    portfolio.clear_pending_opportunity(ticker)
                    continue

                # Parse le verdict dans les premières lignes (format : ACHAT / NEUTRE / ÉVITER)
                full_upper = "\n".join(research_lines).upper()
                head       = " ".join(full_upper.splitlines()[:8])
                is_buy     = "ACHAT" in head
                is_bad     = "ÉVITER" in head or "EVITER" in head or (
                    "NEUTRE" in head and not is_buy
                )
                if is_bad or not is_buy:
                    verdict = "NEUTRE/ÉVITER" if is_bad else "ambigu"
                    send_fn(
                        f"🔴 {ticker} : research dit {verdict} — achat autonome annulé.\n"
                        f"Opportunité supprimée. Lance /scan pour une nouvelle analyse."
                    )
                    portfolio.clear_pending_opportunity(ticker)
                    continue

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

                send_fn(f"✅ {ticker} : research confirme ACHAT — passage de l'ordre…")
                # ────────────────────────────────────────────────────────────────────

                # Adapte l'entrée au cours réel post-research
                actual_entry = round(price2, 3)
                source_tag   = f"[{opp.get('source','briefing')}] " + opp.get("reason", "")[:80]

                success = _place_order(ticker, actual_entry, sl, tp, available, source_tag, send_fn)
                portfolio.clear_pending_opportunity(ticker)
                if success:
                    return  # Une seule entrée par cycle

        # Aucune opportunité validée disponible → on n'agit pas.
        # Le moteur autonome n'entre en position QUE sur des analyses complètes
        # (briefing 9h05 ou /scan manuel). Pas de prise de risque sur analyse légère.
        print("[Auto] Aucune opportunité validée en attente — rien à faire.")

    finally:
        _entry_lock.release()


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

        # Trailing : SL → PRU à +3%
        elif (change_pct >= be_pct
              and sl < entry
              and not pos.get("auto_breakeven_notified")):
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
