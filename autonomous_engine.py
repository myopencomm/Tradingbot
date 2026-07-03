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
BREAKEVEN_PCT = 3.0   # +3% → trailing stop au PRU
MAX_POSITIONS = 2     # Positions autonomes simultanées max

_entry_lock = threading.Lock()
# Anti-spam fallback gain réduit : max 1 recherche toutes les 2h
_last_smallgain_ts = 0.0
SMALLGAIN_COOLDOWN = 2 * 3600


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



# ─── Cycle d'entrée ─────────────────────────────────────────────────────────

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

    entry_eur = entry * fx
    qty  = max(1, int(available / entry_eur))
    cost_eur = qty * entry_eur
    if cost_eur > available * 1.01:
        qty -= 1
    if qty < 1:
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
            f"{ticker} | {qty} titre{'s' if qty > 1 else ''} @ {entry}{sym}\n"
            f"SL : {sl}{sym} | TP : {tp}{sym}\n"
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

                # Marché du TITRE ouvert ? (un ticker US ne se trade qu'à partir
                # de 15h35 Paris — BD rejette sinon). L'opportunité RESTE en
                # attente : le prochain cycle réessaiera quand le marché ouvre.
                if not market_open_for(ticker):
                    print(f"[Auto] {ticker} : marché fermé pour ce titre — "
                          f"opportunité conservée pour le prochain cycle")
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

                # ── Parse du verdict research ────────────────────────────────
                # PRIORITÉ à la ligne "SIGNAL : X" (format imposé au prompt).
                # Ne JAMAIS chercher "ACHAT" en substring libre : une réponse
                # NEUTRE contient souvent "Pourquoi pas ACHAT :" → faux positif.
                import re as _re
                full_text = "\n".join(research_lines)
                verdict = None
                verdict_line = ""
                for line in full_text.splitlines():
                    m = _re.search(r"SIGNAL\s*[:\-]?\s*.{0,3}(ACHAT|NEUTRE|ÉVITER|EVITER)",
                                   line.upper())
                    if m:
                        verdict = m.group(1).replace("EVITER", "ÉVITER")
                        verdict_line = line.strip()[:120]
                        break
                if verdict is None:
                    # Fallback : premier mot-clé des 8 premières lignes, ordre
                    # de priorité PRUDENT (éviter > neutre > achat).
                    head = " ".join(full_text.upper().splitlines()[:8])
                    for kw in ("ÉVITER", "EVITER", "NEUTRE", "ACHAT"):
                        if kw in head:
                            verdict = kw.replace("EVITER", "ÉVITER")
                            break

                if verdict != "ACHAT":
                    send_fn(
                        f"🔴 {ticker} : research dit {verdict or 'verdict illisible'} — "
                        f"achat autonome annulé.\n"
                        + (f"« {verdict_line} »\n" if verdict_line else "")
                        + f"Opportunité supprimée. Lance /scan pour une nouvelle analyse."
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

                send_fn(
                    f"✅ {ticker} : research confirme ACHAT — passage de l'ordre…"
                    + (f"\n« {verdict_line} »" if verdict_line else "")
                )
                # ────────────────────────────────────────────────────────────────────

                # Adapte l'entrée au cours réel post-research
                actual_entry = round(price2, 3)
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
