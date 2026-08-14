"""
Trailing stop — remontée du SL sur une position gagnante.

Deux paliers (voir `trailing_target`), une source unique pour les deux chemins
qui les appliquent : le trailing RÉEL sur Bourse Direct et l'alerte en mode
déconnecté. Un écart entre les deux ferait replacer sur BD un stop différent de
celui annoncé sur Telegram.

Séparé de `autonomous_engine`, qui décide des ENTRÉES : entrer et protéger sont
deux métiers, et le trailing pesait à lui seul près d'un tiers du module.
"""
import time

import bot_mode
import playwright_session
import portfolio
import prices
from config import AUTO_BREAKEVEN_PCT

BREAKEVEN_PCT = AUTO_BREAKEVEN_PCT   # seuil du palier 1 en mode autonome

# Positions dont une annulation de protection a échoué : on ne réessaie pas en
# boucle (chaque tentative ratée laisse une fenêtre sans protection).
_trailing_cancel_failed: set[str] = set()


def rearm_notifications() -> None:
    """Oublie les échecs déjà signalés, pour que le prochain cycle les redise.

    Appelé par `/trailing` : l'utilisateur qui demande explicitement un état
    doit le recevoir en entier, même si le même échec a déjà été annoncé lors
    d'un cycle automatique.

    Existe pour que personne n'ait à toucher `_trailing_cancel_failed` depuis
    un autre module. C'est exactement ce que faisait `telegram_bot`, en allant
    le chercher sur `autonomous_engine` — et quand cet état a déménagé ici
    (13/08/2026), `/trailing` s'est mis à répondre « module
    'autonomous_engine' has no attribute '_trailing_cancel_failed' » sans même
    lancer le cycle. Une fonction publique ne peut pas casser en silence de
    cette façon : elle est importée, donc vérifiable.
    """
    _trailing_cancel_failed.clear()

def tp_progress(entry: float, tp: float | None, price: float) -> float | None:
    """Part du chemin PRU → TP déjà parcourue (0 = au PRU, 1 = au TP)."""
    if not entry or not tp or tp <= entry:
        return None
    return (price - entry) / (tp - entry)


def trailing_target(pos: dict, price: float, tp: float | None,
                    atr_pct: float | None = None) -> tuple[float | None, str, str]:
    """
    SL visé pour cette position — SOURCE UNIQUE des deux paliers de trailing,
    partagée par le trailing réel sur BD et par l'alerte en mode déconnecté.

    Deux paliers, le PLUS HAUT l'emporte :

    1. BREAKEVEN — le cours dépasse le seuil (+6% autonome / +5% manuel) :
       SL au PRU. Protège le capital, pas le gain.
    2. SÉCURISATION — le cours a parcouru au moins TRAIL_LOCK_TRIGGER_PCT du
       chemin PRU→TP : SL AU-DESSUS du PRU, à une fraction du gain déjà acquis.
       La fraction grandit avec la progression (TRAIL_LOCK_MIN_RATIO au
       déclenchement → TRAIL_LOCK_MAX_RATIO au contact du TP) : plus le TP est
       proche, moins il reste de raisons de laisser filer le gain acquis.

    Le SL sécurisé garde toujours une marge sous le cours — le plus large de
    TRAIL_MIN_BUFFER_PCT et 1×ATR. Sans elle, un stop collé au cours se ferait
    sortir par le bruit ordinaire juste avant le TP, ce que ce palier cherche
    précisément à éviter.

    Retourne (sl_visé | None, code_palier, libellé_humain).
    """
    from config import (BREAKEVEN_THRESHOLD, TRAIL_LOCK_TRIGGER_PCT,
                        TRAIL_LOCK_MIN_RATIO, TRAIL_LOCK_MAX_RATIO,
                        TRAIL_MIN_BUFFER_PCT)
    entry = pos.get("entry_price") or 0
    if not entry or not price:
        return None, "", ""

    target, step, label = None, "", ""

    # Palier 1 — breakeven
    threshold = BREAKEVEN_PCT if pos.get("autonomous") else BREAKEVEN_THRESHOLD
    if (price - entry) / entry * 100 >= threshold:
        target, step, label = entry, "breakeven", "SL au PRU"

    # Palier 2 — sécurisation du gain
    prog = tp_progress(entry, tp, price)
    trigger = TRAIL_LOCK_TRIGGER_PCT / 100
    if prog is not None and prog >= trigger and prog < 1.0:
        # Fraction verrouillée, interpolée entre le déclenchement et le TP
        span  = max(1e-9, 1.0 - trigger)
        ratio = (TRAIL_LOCK_MIN_RATIO
                 + (TRAIL_LOCK_MAX_RATIO - TRAIL_LOCK_MIN_RATIO)
                 * (prog - trigger) / span) / 100
        locked = entry + ratio * (price - entry)
        # Marge de respiration sous le cours
        buffer_pct = max(TRAIL_MIN_BUFFER_PCT, atr_pct or 0)
        locked = min(locked, price * (1 - buffer_pct / 100))
        if tp:
            locked = min(locked, tp * 0.999)     # jamais au niveau du TP
        if locked > (target or 0):
            gain_pct = (locked / entry - 1) * 100
            target, step = locked, "lock"
            label = (f"SL à {gain_pct:+.1f}% du PRU — {ratio * 100:.0f}% du gain "
                     f"verrouillé ({prog * 100:.0f}% du chemin vers le TP)")

    return (round(target, 4) if target else None), step, label


def trailing_stop_cycle(send_fn, verbose: bool = False) -> None:
    """
    Remonte le SL au PRU (breakeven) DIRECTEMENT SUR BD pour toute position —
    autonome (+BREAKEVEN_PCT%) ou manuelle (+BREAKEVEN_THRESHOLD%) — protégée
    par un ordre Expert vente actif. Move purement protecteur : le SL ne peut
    que MONTER, le TP n'est jamais modifié.

    Les positions historiques SANS ordre Expert sur BD (ILMN, GVN, MCPHY…)
    ne sont jamais touchées : pas d'ordre à modifier = pas d'action.

    `verbose` (commande /trailing) : rend compte de CHAQUE position évaluée et
    de la raison d'un non-déclenchement. En cycle automatique le silence est
    voulu — ici l'utilisateur a demandé, il doit obtenir une réponse.
    """
    from config import (BREAKEVEN_THRESHOLD, BREAKEVEN_TOLERANCE_PCT,
                        TRAIL_LOCK_TRIGGER_PCT, TRAIL_MIN_STEP_PCT)

    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        if verbose:
            send_fn("🔒 Trailing impossible : session Bourse Direct non connectée.\n"
                    "/connect pour l'activer.")
        return
    positions = portfolio.load().get("positions", {})
    if not positions:
        if verbose:
            send_fn("🔒 Trailing : aucune position en portefeuille.")
        return

    # 1. Positions au-dessus de leur seuil de breakeven
    candidates = []
    skipped = []
    for name, pos in positions.items():
        if pos.get("hold"):
            skipped.append(f"  🔒 {name} : HOLD long terme — hors gestion bot")
            continue
        entry = pos.get("entry_price")
        if not entry or not pos.get("qty"):
            skipped.append(f"  ⚠️ {name} : PRU ou quantité manquant")
            continue
        # Cours retenu : yfinance s'il est frais, sinon le relevé BD. Un cours
        # périmé fait rater un palier — le 04/08 AIR était à 211.40 chez BD
        # (72% du chemin vers le TP, palier 2 mérité) alors que yfinance
        # servait encore 208.00 (55%, aucun palier).
        _best = portfolio.best_price(pos)
        price = _best["price"]
        if not price:
            skipped.append(f"  ⚠️ {name} : cours indisponible")
            continue
        if _best["source"] != "yf":
            print(f"[Trailing] {name} : {_best['note']}")
        change_pct = (price - entry) / entry * 100
        threshold = BREAKEVEN_PCT if pos.get("autonomous") else BREAKEVEN_THRESHOLD
        # Deux portes d'entrée : le seuil de breakeven, OU la progression vers
        # le TP (palier de sécurisation). Sur un TP étroit la seconde s'ouvre
        # AVANT la première — une position à +5% d'un TP à +8% a déjà fait 62%
        # du chemin et mérite un stop au-dessus du PRU.
        prog = tp_progress(entry, pos.get("target_high"), price)
        if change_pct >= threshold or (prog is not None
                                       and prog * 100 >= TRAIL_LOCK_TRIGGER_PCT):
            candidates.append((name, pos, change_pct, price))
        else:
            need = entry * (1 + threshold / 100)
            prog_note = f", {prog * 100:.0f}% du chemin vers le TP" if prog is not None else ""
            skipped.append(
                f"  ⏳ {name} : {change_pct:+.2f}% — seuil +{threshold:.0f}% "
                f"non atteint (il faut {need:.2f}{prog_note})"
            )
    if verbose:
        head = [f"🔒 TRAILING — vérification à la demande",
                f"Palier 1 BREAKEVEN — SL au PRU dès "
                f"+{BREAKEVEN_THRESHOLD:.0f}% (manuel) / +{BREAKEVEN_PCT:.0f}% (autonome)",
                f"Palier 2 SÉCURISATION — SL au-dessus du PRU dès "
                f"{TRAIL_LOCK_TRIGGER_PCT:.0f}% du chemin parcouru vers le TP",
                f"\nLe bot ne peut remonter QUE les protections posées en ordre "
                f"de VENTE (celles qu'il voit au carnet, avec une référence "
                f"annulable). Une protection portée par un Expert d'ACHAT reste "
                f"active sur BD mais hors de sa portée."]
        if candidates:
            head.append(f"\n{len(candidates)} position(s) au-dessus du seuil : "
                        + ", ".join(n for n, _, _, _ in candidates))
        if skipped:
            head.append("\nNon concernées :")
            head.extend(skipped)
        if not candidates:
            head.append("\n✅ Rien à remonter — aucune action.")
        send_fn("\n".join(head))
    if not candidates:
        return

    # 2. Carnet d'ordres LEGACY — SEULE source listant chaque protection
    #    séparément avec un identifiant annulable (ref/refbo). La page
    #    portefeuille moderne ne montre que l'ordre d'achat parent pour les
    #    protections issues d'un Expert d'achat (cas UNA/GLE) : son id n'est
    #    pas annulable (403). Voir bourse_direct_reader.parse_order_book_html.
    import bourse_direct_reader as reader
    import bourse_direct_orders as bd_orders
    try:
        rows = playwright_session.run(lambda page: reader.read_order_book(page), timeout=90)
    except Exception as e:
        print(f"[Trailing] lecture carnet : {e}")
        if verbose:
            send_fn("⚠️ Lecture du carnet d'ordres impossible — aucune action.")
        return
    if not rows:
        if verbose:
            send_fn("⚠️ Carnet d'ordres vide ou illisible — aucune action.")
        return

    for name, pos, change_pct, price in candidates:
        entry  = pos["entry_price"]
        qty_pos = abs(pos.get("qty") or 0)
        sl_ord = reader.find_stop_loss_order(rows, pos["ticker"], entry)
        tp_ord = reader.find_take_profit_order(rows, pos["ticker"], entry)

        # ── Cas RÉCUPÉRATION : plus de SL mais un TP encore actif ────────────
        # Position SANS PROTECTION (peut résulter d'une annulation partielle :
        # SL annulé, TP survivant — incident UNA 28/07/2026). Le trailing doit
        # rétablir un stop, pas s'abstenir.
        if not sl_ord:
            if tp_ord:
                print(f"[Trailing] {name} : SL absent, TP actif → RÉCUPÉRATION")
                send_fn(
                    f"🚨 {name} : POSITION SANS STOP LOSS sur BD "
                    f"(un Take Profit à {tp_ord['limit']} est encore actif).\n"
                    f"Tentative de rétablissement automatique du stop au PRU…"
                )
            elif pos.get("protection_ids"):
                # Protection absente du carnet MAIS dont on connaît les ids :
                # ce sont les `children` renvoyés à la création de l'Expert.
                # Capture réseau du 05/08/2026 : l'annulation manuelle poste
                # {"order_id": "<id enfant>"} sur /order/cancel — exactement ce
                # que sait faire bd_orders.cancel_order. On peut donc remonter
                # le stop d'une position achetée en Expert, ce qui était
                # impossible jusqu'ici.
                target, step, step_label = trailing_target(pos, price, tp, atr_pct)
                if not target or target <= (pos.get("target_low") or 0) + entry * TRAIL_MIN_STEP_PCT / 100:
                    if verbose:
                        send_fn(f"  ↳ {name} : protégé (hors carnet), palier "
                                f"non atteint — rien à faire")
                    continue
                oids = list(pos.get("protection_ids") or [])
                send_fn(f"🔁 {name} : remontée du stop {pos.get('target_low')} → "
                        f"{target} (protection d'ordre d'achat, {len(oids)} jambe(s) "
                        f"à annuler)…")
                failed = []
                for oid in oids:
                    try:
                        ok = playwright_session.run(
                            lambda page, o=oid: bd_orders.cancel_order(page, o),
                            timeout=30)
                    except Exception as _ce:
                        print(f"[Trailing] {name} cancel {oid} : {_ce}")
                        ok = None
                    if not ok:
                        failed.append(oid)
                # BD répond « en cours d'annulation » : c'est ASYNCHRONE. On
                # laisse le temps à la bascule avant de vérifier, et on ne
                # repose RIEN tant que la protection est encore là — reposer
                # sur une annulation non aboutie créerait un doublon de vente.
                time.sleep(5)
                still = playwright_session.run(
                    lambda page: reader.get_portfolio(page, send_fn=None), timeout=90) or {}
                base_n = pos["ticker"].upper().split(".")[0]
                # « Plus d'ordre au portefeuille » ne vaut disparition QUE si
                # l'onglet ordres a réellement été lu : une lecture ratée rend
                # la même liste vide, et reposer un ordre là-dessus créerait le
                # doublon de vente que ce garde-fou existe pour empêcher.
                gone = still.get("orders_read", False) and not any(
                    (o.get("bd_ticker") or "").upper().split(".")[0] == base_n
                    and o.get("seuil") and o.get("statut") == "En cours"
                    for o in still.get("orders", []))
                if failed or not gone:
                    send_fn(
                        f"⚠️ {name} : annulation NON confirmée "
                        f"({len(failed)} échec(s)) — aucun nouvel ordre posé.\n"
                        f"✅ La protection actuelle ({pos.get('target_low')}) reste active."
                    )
                    continue
                od = playwright_session.run(
                    lambda page, t=pos["ticker"], q=qty_pos, sn=round(target, 4), tp_=tp:
                        bd_orders.create_expert_order(page, t, q, sn, tp_, "max"),
                    timeout=30)
                oid2 = od and (od.get("id") or od.get("order_id"))
                conf = playwright_session.run(
                    lambda page, o=oid2: bd_orders.confirm_order_auto(page, o, False),
                    timeout=30) if oid2 else None
                if conf:
                    dd = portfolio.load()
                    if name in dd.get("positions", {}):
                        dd["positions"][name]["target_low"] = round(target, 4)
                        dd["positions"][name]["protection_ids"] = [
                            c for c in (od.get("children") or []) if c]
                        dd["positions"][name].pop("pending_sl", None)
                        portfolio.save(dd)
                    send_fn(f"🤖 GAIN SÉCURISÉ — {name}\n"
                            f"Stop remonté sur BD : {pos.get('target_low')} → {target}\n"
                            f"{step_label}")
                else:
                    send_fn(f"🚨 {name} : ancienne protection annulée mais NOUVEL "
                            f"ORDRE NON CONFIRMÉ — position à nu.\n"
                            f"/ordre vendre {pos['ticker']} {qty_pos} expert "
                            f"{target} {tp}")
                continue
            elif pos.get("protected"):
                # ABSENT DU CARNET ≠ SANS PROTECTION. Les deux pages BD sont
                # COMPLÉMENTAIRES, pas redondantes :
                #   · page portefeuille (lue par le sync) : montre TOUTES les
                #     protections actives, y compris celles portées par un
                #     Expert d'ACHAT exécuté — mais sans identifiant annulable ;
                #   · carnet legacy (lu ici) : ne liste que les ordres de vente
                #     AUTONOMES, avec leur ref annulable.
                # NVDA, protégé par son Expert d'achat (SL 187.40 / TP 225),
                # n'apparaît donc PAS au carnet — et a été annoncé « à nu » à
                # tort le 05/08. Le trailing ne peut pas le remonter : il n'a
                # rien à annuler. C'est une limite réelle, pas un défaut de
                # lecture, et elle se dit telle quelle.
                if verbose:
                    tgt, _st, _lb = trailing_target(pos, price, tp, atr_pct)
                    gain = f" (verrouillerait +{(tgt - entry) * qty_pos:.0f}€)" if tgt else ""
                    send_fn(
                        f"  ↳ {name} : protégé sur BD (SL {pos.get('target_low')} / "
                        f"TP {pos.get('target_high')}) mais la protection est SOUDÉE "
                        f"à l'ordre d'ACHAT exécuté — BD n'expose pas d'id annulable "
                        f"pour elle, le bot ne peut donc pas la remonter.\n"
                        f"     Palier visé : {tgt or '—'}{gain}. Pour l'appliquer : "
                        f"annule l'Expert depuis l'interface BD, puis\n"
                        f"     /ordre vendre {pos['ticker']} {qty_pos} expert "
                        f"{tgt or pos.get('target_low')} {pos.get('target_high')}"
                    )
                continue
            else:
                # Ni SL ni TP au carnet ET le dernier sync ne voyait aucune
                # protection : là, la position est vraiment à nu. Ce cas était
                # un « rien à faire » silencieux — c'est ainsi que BAC est resté
                # sans protection du 31/07 au 05/08 sans un mot.
                _trailing_naked_notified = globals().setdefault("_trailing_naked", set())
                if name not in _trailing_naked_notified:
                    _trailing_naked_notified.add(name)
                    send_fn(
                        f"🚨 {name} : AUCUNE PROTECTION — ni au carnet, ni vue par "
                        f"le dernier sync.\n"
                        f"À replacer : /ordre vendre {pos['ticker']} {qty_pos} expert "
                        f"{pos.get('target_low')} {pos.get('target_high')}"
                    )
                elif verbose:
                    send_fn(f"  ↳ {name} : toujours aucune protection")
                continue

        cur_sl = sl_ord["limit"] if sl_ord else None

        # Le TP est nécessaire AVANT le calcul de la cible : c'est lui qui situe
        # la position sur le chemin PRU→TP, donc quel palier s'applique.
        tp = (tp_ord or {}).get("limit") or pos.get("target_high")
        if not tp:
            if verbose:
                send_fn(f"  ↳ {name} : Take Profit introuvable — abstention "
                        f"(reposer un Expert sans lui créerait un doublon)")
            continue

        atr_pct = (prices.get_technicals(pos["ticker"]) or {}).get("atr_pct")
        target, step, step_label = trailing_target(pos, price, tp, atr_pct)
        if not target:
            if verbose:
                send_fn(f"  ↳ {name} : aucun palier atteint — rien à faire")
            continue

        # Le SL ne peut que MONTER, et seulement si ça vaut le risque. CHAQUE
        # remontée annule les 2 ordres BD et en repose un — fenêtre pendant
        # laquelle la position est à nu (incident UNA 28/07/2026). Deux garde-fous :
        #   · tolérance BD au breakeven : un SL à 196.84 pour un PRU de 196.90 est
        #     déjà au PRU à 0.03% près, annuler/reposer pour ces centimes ne
        #     rapporte rien ;
        #   · pas minimal ailleurs : ratcheter de 0.2% n'en vaut pas la peine.
        if cur_sl is not None:
            at_breakeven = cur_sl >= entry * (1 - BREAKEVEN_TOLERANCE_PCT / 100)
            if step == "breakeven" and at_breakeven:
                if verbose:
                    send_fn(f"  ↳ {name} : SL déjà au PRU ({cur_sl} vs PRU {entry}, "
                            f"tolérance {BREAKEVEN_TOLERANCE_PCT}%) — rien à faire ✅")
                continue
            min_step = entry * TRAIL_MIN_STEP_PCT / 100
            if target <= cur_sl + min_step:
                if verbose:
                    send_fn(f"  ↳ {name} : cible {target} trop proche du SL actuel "
                            f"({cur_sl}) — moins de {TRAIL_MIN_STEP_PCT}% de gain, "
                            f"le risque d'annuler/reposer n'en vaut pas la peine")
                continue

        new_sl = round(target, 4)
        qty    = abs((sl_ord or tp_ord or {}).get("qty") or pos.get("qty") or 0)
        if qty < 1:
            continue

        try:
            # ── Annulation UN PAR UN, chacune vérifiée ───────────────────────
            # La page legacy répond 200 même quand rien n'est annulé, et une
            # annulation peut n'aboutir que partiellement (incident UNA
            # 28/07 : SL annulé, TP non → position à nu alors que le bot
            # annonçait « position protégée »). On vérifie donc APRÈS CHAQUE
            # annulation, avec un délai (BD ne répercute pas instantanément).
            def _cancel_verified(o, tries: int = 3):
                last = rows
                for i in range(tries):
                    playwright_session.run(
                        lambda page, r=o["ref"], rb=o["refbo"]:
                            bd_orders.cancel_legacy_order(page, r, rb),
                        timeout=30,
                    )
                    time.sleep(3)
                    last = playwright_session.run(
                        lambda page: reader.read_order_book(page), timeout=90
                    ) or []
                    if not any(a.get("ref") == o["ref"] for a in last):
                        return True, last
                    print(f"[Trailing] {name} : ref {o['ref']} encore présente "
                          f"(tentative {i + 1}/{tries})")
                return False, last

            to_cancel = ([sl_ord] if sl_ord else []) + ([tp_ord] if tp_ord else [])
            failed, after = [], rows
            for o in to_cancel:
                ok, after = _cancel_verified(o)
                if not ok:
                    failed.append(o["ref"])

            if failed:
                sl_gone = (sl_ord is None) or not any(
                    a.get("ref") == sl_ord["ref"] for a in after)
                _trailing_cancel_failed.add(name)
                if sl_gone:
                    # Le stop n'existe plus mais le TP a survécu : on ne peut
                    # pas reposer un Expert (doublon de vente) et la position
                    # est RÉELLEMENT à nu. Alerte maximale, pas de faux calme.
                    print(f"[Trailing] {name} : SL annulé, TP restant {failed} — POSITION À NU")
                    send_fn(
                        f"🚨🚨 {name} : POSITION SANS STOP LOSS SUR BD.\n"
                        f"Le stop a été annulé mais le Take Profit n'a PAS pu l'être "
                        f"({', '.join(failed)}), donc aucun nouvel ordre n'a pu être posé.\n\n"
                        f"À FAIRE MAINTENANT :\n"
                        f"1. Annule à la main l'ordre {', '.join(failed)} "
                        f"(Bourse Direct › Ordres en carnet)\n"
                        f"2. Puis colle : /ordre vendre {pos['ticker']} {qty} expert {new_sl} {tp}"
                    )
                else:
                    # Le SL est toujours là : rien n'a bougé, position protégée.
                    print(f"[Trailing] {name} : annulation non confirmée {failed} — SL intact")
                    send_fn(
                        f"⚠️ Trailing {name} : annulation non confirmée — SL inchangé.\n"
                        f"✅ La position reste protégée par son stop actuel ({cur_sl}).\n\n"
                        f"Ordres encore présents : {', '.join(failed)}"
                    )
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
                if step == "lock":
                    locked_eur = (new_sl - entry) * qty
                    send_fn(
                        f"{tag} GAIN SÉCURISÉ — {name} à +{change_pct:.1f}%\n"
                        f"SL remonté AU-DESSUS du PRU sur BD : "
                        f"{cur_sl if cur_sl is not None else '—'}€ → {new_sl}€ "
                        f"(TP {tp_f}€ inchangé)\n"
                        f"{step_label}\n"
                        f"Sortie au pire à +{locked_eur:.0f}€ désormais, plus à zéro."
                    )
                else:
                    send_fn(
                        f"{tag} BREAKEVEN AUTO — {name} à +{change_pct:.1f}%\n"
                        f"SL remonté au PRU sur BD : {cur_sl}€ → {new_sl}€ (TP {tp_f}€ inchangé)\n"
                        f"Perte impossible sur cette position désormais."
                    )
            else:
                # SL ET TP ont été annulés (vérifié) mais le nouvel Expert
                # n'est pas confirmé : la position est réellement À NU.
                # Alerte maximale + commande de secours prête à coller.
                print(f"[Trailing] {name} : POSITION SANS PROTECTION — recréation échouée")
                send_fn(
                    f"🚨 Trailing {name} : anciennes protections annulées mais "
                    f"NOUVEL ORDRE NON CONFIRMÉ.\n"
                    f"⚠️ POSITION SANS PROTECTION SUR BD — replace immédiatement :\n"
                    f"/ordre vendre {pos['ticker']} {qty} expert {new_sl} {tp}"
                )
        except Exception as e:
            print(f"[Trailing] {name} : {e}")
            send_fn(f"⚠️ Trailing {name} : erreur {e}")