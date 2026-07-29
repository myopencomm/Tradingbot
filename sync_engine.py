"""
Synchronisation Bourse Direct → positions.json
Appelé par /sync. Met à jour cash + détecte les écarts de positions.
"""
import portfolio
import bourse_direct_reader as reader

# MIC (place BD) → suffixe yfinance. US (XNAS/XNYS) = pas de suffixe.
MIC_SUFFIX = {
    "XPAR": ".PA", "XAMS": ".AS", "XBRU": ".BR",
    "XLON": ".L",  "XETR": ".DE",
    "XNAS": "",     "XNYS": "",
}


# Devise de cotation par place. Sert à détecter le piège du PRU : sur l'onglant
# « Mes positions » BD convertit le PRU des valeurs US en EUR, alors que le bot
# stocke entry_price/SL/TP dans la devise de cotation (stats.py convertit
# ensuite via fx_to_eur). Écraser entry_price avec le PRU en EUR d'une position
# en USD fausse le P&L latent et le suivi SL/TP.
MIC_CURRENCY = {
    "XPAR": "EUR", "XAMS": "EUR", "XBRU": "EUR", "XETR": "EUR",
    "XNAS": "USD", "XNYS": "USD", "XNGS": "USD", "XNMS": "USD", "ARCX": "USD",
    "XLON": "GBP",
}


def _pru_in_quote_currency(bd_pru, pru_cur: str, pos_cur: str):
    """
    PRU BD ramené dans la devise de cotation du titre.
    Retourne (valeur, note) ; (None, raison) si la conversion est impossible —
    dans ce cas seulement on renonce à écrire le PRU.

    BD affiche le PRU des valeurs US converti en EUR : le convertir dans l'autre
    sens (fx du jour) est la seule façon de le comparer au cours, au SL et au TP,
    tous en devise de cotation.
    """
    if not bd_pru:
        return None, ""
    if pru_cur == pos_cur:
        return bd_pru, ""
    import prices
    fx = prices.fx_to_eur(pos_cur)      # 1 unité devise → EUR
    if not fx or (fx == 1.0 and pos_cur != "EUR"):
        # fx_to_eur renvoie 1.0 en repli quand le taux est indisponible :
        # l'appliquer donnerait un PRU faux de ~14 % sur l'USD.
        return None, f"taux {pos_cur}→EUR indisponible"
    return round(bd_pru / fx, 4), f"PRU BD {bd_pru} {pru_cur} converti au taux {round(fx, 4)}"


def _yf_ticker(bd_ticker: str, mic: str) -> str:
    """Reconstruit le ticker yfinance depuis le mnémo BD + la place (MIC)."""
    bd_ticker = (bd_ticker or "").upper()
    suffix = MIC_SUFFIX.get((mic or "").upper(), ".PA")  # défaut Paris
    return f"{bd_ticker}{suffix}" if bd_ticker else ""


def sync(page, send_fn, silent: bool = False, progress_fn=None) -> bool:
    """
    Lit le portefeuille depuis BD et met à jour positions.json.
    `page` fourni par playwright_session.run() (thread worker).
    silent=True (check horaire auto) : aucun message sauf si une vente ou un
    achat exécuté est détecté.
    `progress_fn` (optionnel) reçoit les messages d'ÉTAPE — « Synchronisation
    en cours », erreurs de lecture — que l'appelant peut rendre éphémères. Le
    RÉSULTAT part toujours par `send_fn`. Sans progress_fn : tout par send_fn.
    Retourne True si sync réussie.
    """
    prog = progress_fn or send_fn
    if not silent:
        prog("Synchronisation avec Bourse Direct...")

    bd = reader.get_portfolio(page, send_fn=None if silent else prog)
    if not bd:
        return False

    data = portfolio.load()
    local = data.get("positions", {})
    lines = []
    changed = False

    # ── Cash ────────────────────────────────────────────────────────────────
    if bd["cash"] is not None:
        old_cash = data.get("cash_available", 0)
        if abs(bd["cash"] - old_cash) >= 0.01:
            data["cash_available"] = bd["cash"]
            changed = True
            lines.append(f"Cash : {old_cash}€ → {bd['cash']}€")
        else:
            lines.append(f"Cash : {bd['cash']}€ (inchangé)")

    # ── Positions BD → local ─────────────────────────────────────────────
    def _local_base(cfg):
        return cfg["ticker"].upper().split(".")[0]

    def _match_local(bd_ticker, bd_name):
        """Trouve la clé locale par ticker, nom complet (bd_name), ou substring."""
        bd_ticker = (bd_ticker or "").upper()
        bd_name_u = (bd_name or "").upper()
        for k, cfg in local.items():
            local_bd_name = (cfg.get("bd_name") or "").upper()
            if bd_ticker and _local_base(cfg) == bd_ticker:
                return k
            if bd_name_u and local_bd_name and bd_name_u == local_bd_name:
                return k
            if k.upper() == bd_name_u or k.upper() == bd_name_u.replace(" ", ""):
                return k
            # Substring : "MCPHY" ⊂ "MCPHY ENERGY", ou bd_name ⊂ alias
            if bd_name_u and (k.upper() in bd_name_u or
                              (local_bd_name and (local_bd_name in bd_name_u or bd_name_u in local_bd_name))):
                return k
        return None

    # ── Pré-passe : protections SL/TP issues des ordres actifs ───────────
    # Permet d'auto-créer une nouvelle position avec ses vrais SL/TP (lus sur
    # l'ordre Expert en cours) plutôt que de demander un /add manuel.
    order_protect = {}  # clé (bd_ticker ou name, upper) → {seuil, profit, mic}
    for o in bd.get("orders", []):
        if o.get("statut") != "En cours":
            continue
        prot = {"seuil": o.get("seuil"), "profit": o.get("profit"), "mic": o.get("mic", ""),
                "currency": o.get("currency") or "EUR", "exec_price": o.get("exec_price")}
        if o.get("bd_ticker"):
            order_protect[o["bd_ticker"].upper()] = prot
        if o.get("name"):
            order_protect[o["name"].upper()] = prot

    matched_local_keys = set()
    added_keys = []
    sold_keys = []

    for pos in bd["positions"]:
        bd_ticker = (pos.get("bd_ticker") or "").upper()
        bd_name   = pos["name"].upper()
        bd_qty    = pos["qty"]
        bd_pru    = pos["pru"]

        # Devise réelle du titre (place de cotation) vs devise du PRU affiché
        # par BD, qui convertit en EUR le PRU des valeurs US (relevé : ILMN
        # coté 192.52 USD, PRU affiché 317.1087 € — et le -46.74 % de BD ne
        # tombe juste QUE si les deux termes sont en EUR). Le bot, lui, stocke
        # entry_price dans la devise de cotation : on CONVERTIT le PRU, on ne
        # le jette pas.
        local_key = _match_local(bd_ticker, bd_name)

        prot_pre  = order_protect.get(bd_ticker) or order_protect.get(bd_name) or {}
        pos_mic   = (prot_pre.get("mic") or pos.get("mic") or "").upper()
        pos_cur   = prot_pre.get("currency") or MIC_CURRENCY.get(pos_mic)
        if not pos_cur and local_key:
            # Repli : un ticker local sans suffixe de place est une valeur US.
            pos_cur = "USD" if "." not in local[local_key]["ticker"] else "EUR"
        pos_cur   = pos_cur or "EUR"
        pru_cur   = (pos.get("pru_currency") or "EUR").upper()
        pru_native, pru_conv_note = _pru_in_quote_currency(bd_pru, pru_cur, pos_cur)

        if local_key:
            matched_local_keys.add(local_key)
            loc = local[local_key]
            sub = []
            if loc["qty"] != bd_qty:
                sub.append(f"qté {loc['qty']} → {bd_qty}")
                data["positions"][local_key]["qty"] = bd_qty
                changed = True
            if pru_native:
                same_cur = (pru_cur == pos_cur)
                # PRU converti : le taux bouge tous les jours alors que le PRU
                # BD, lui, ne bouge qu'en cas de renfort ou de correction. On
                # mémorise donc la valeur BRUTE de BD et on ne reconvertit que
                # si ELLE a changé — sinon entry_price dériverait au rythme du
                # fx, et avec lui le P&L et les distances au SL/TP.
                raw_ref   = round(bd_pru, 5)
                prev_ref  = loc.get("bd_pru_raw")
                ref_moved = (prev_ref is None
                             or abs(raw_ref - prev_ref) > 0.001 * max(prev_ref, 0.001))
                gap = abs((pru_native - loc["entry_price"]) / max(loc["entry_price"], 0.001))
                if gap > 0.001 and (same_cur or ref_moved):
                    note = f" ({pru_conv_note})" if pru_conv_note else ""
                    sub.append(f"PRU {loc['entry_price']} → {pru_native}{note}")
                    data["positions"][local_key]["entry_price"] = round(pru_native, 5)
                    changed = True
                if not same_cur and prev_ref != raw_ref:
                    data["positions"][local_key]["bd_pru_raw"] = raw_ref
                    changed = True
            elif bd_pru and not loc.get("hold"):
                lines.append(
                    f"    ⚠️ PRU BD non repris pour {local_key} ({bd_pru} {pru_cur} "
                    f"vs cotation en {pos_cur}) : {pru_conv_note}."
                )
            if sub:
                lines.append(f"{local_key} mis à jour : {', '.join(sub)}")
            else:
                lines.append(f"{local_key} OK — {bd_qty}t")
        else:
            # ── Auto-création : la position existe sur BD mais pas dans le bot ──
            prot = order_protect.get(bd_ticker) or order_protect.get(bd_name) or {}
            mic  = prot.get("mic") or pos.get("mic") or ""
            yf_t = _yf_ticker(bd_ticker, mic)
            new_key = bd_ticker or bd_name.replace(" ", "_")[:20]

            # Ordre autonome en attente correspondant ? → la position naît
            # avec le flag autonome et ses SL/TP d'origine, et l'engagement
            # "en attente" est consommé.
            auto_rec = (data.get("auto_pending_orders", {}).get(yf_t.upper())
                        or data.get("auto_pending_orders", {}).get(bd_ticker))
            sl = prot.get("seuil") or (auto_rec or {}).get("sl")
            tp = prot.get("profit") or (auto_rec or {}).get("tp")

            # Prix d'entrée DANS LA DEVISE DE COTATION. Le PRU BD est la
            # meilleure source (frais inclus), converti si BD l'affiche en EUR
            # pour une valeur US. Repli si la conversion échoue : prix
            # d'exécution de l'ordre BD, puis prix de l'ordre autonome.
            entry = pru_native or prot.get("exec_price") or (auto_rec or {}).get("entry")
            entry_src = ""
            if pru_native and pru_conv_note:
                entry_src = f" ({pru_conv_note})"
            elif not pru_native and entry:
                entry_src = f" (prix d'exécution BD — {pru_conv_note or 'PRU BD inutilisable'})"

            # Sans SL/TP connus (aucun ordre Expert actif) : valeurs par défaut -7%/+10%
            if not sl and entry:
                sl = round(entry * 0.93, 4)
            if not tp and entry:
                tp = round(entry * 1.10, 4)

            if not yf_t or bd_qty is None or not entry:
                # Données insuffisantes → on garde le fallback manuel
                lines.append(
                    f"⚠️ {pos['name']} ({bd_ticker}) sur BD : {bd_qty}t @ {bd_pru}€\n"
                    f"   → impossible d'auto-ajouter (données incomplètes). "
                    f"/add NOM {bd_ticker or '?'}{MIC_SUFFIX.get(mic.upper(), '.PA')} {bd_qty} {bd_pru} <SL> <TP>"
                )
            else:
                data.setdefault("positions", {})[new_key] = {
                    "ticker":      yf_t,
                    "qty":         bd_qty,
                    "entry_price": round(entry, 5),
                    "target_high": round(tp, 4),
                    "target_low":  round(sl, 4),
                    "bd_name":     pos["name"],
                }
                if pru_native and pru_cur != pos_cur:
                    # Référence brute BD : sert à ne reconvertir que si BD
                    # change son PRU (cf. mise à jour ci-dessus).
                    data["positions"][new_key]["bd_pru_raw"] = round(bd_pru, 5)
                if auto_rec:
                    data["positions"][new_key]["autonomous"] = True
                    # Consomme l'engagement "ordre en attente" (exécuté)
                    for k in (yf_t.upper(), bd_ticker):
                        data.get("auto_pending_orders", {}).pop(k, None)
                local[new_key] = data["positions"][new_key]
                matched_local_keys.add(new_key)
                added_keys.append(new_key)
                changed = True
                src = "SL/TP lus sur l'ordre BD" if prot.get("seuil") or prot.get("profit") else \
                      ("SL/TP de l'ordre autonome" if auto_rec else "SL/TP par défaut -7%/+10%")
                tag = "🤖 " if auto_rec else ""
                import prices
                psym = prices.currency_symbol(pos_cur)
                lines.append(
                    f"➕ {tag}{new_key} ({yf_t}) AJOUTÉ auto : {bd_qty}t @ {entry}{psym}{entry_src} | "
                    f"SL {sl}{psym} TP {tp}{psym} ({src})"
                )

    # ── Positions locales absentes de BD → vendues : clôture AUTOMATIQUE ──
    # L'ordre de vente exécuté sur BD donne le prix réel de sortie (volet TP
    # ou SL). Le cash BD, déjà synchronisé plus haut, inclut le produit de la
    # vente — on ne l'ajoute donc PAS une deuxième fois.
    # Protections EXÉCUTÉES = tout ordre au statut "Exécuté" portant un prix
    # d'exécution ou un seuil/profit. On ne filtre PAS sur sens=="Vente" : BD
    # attache souvent le bracket TP/SL à l'ordre d'ACHAT (sens lu "Achat"), donc
    # un SL/TP déclenché apparaît sur un ordre "Achat" — c'est bien une SORTIE.
    # Le garde-fou anti-fausse-clôture (07/07) tient : un achat rempli dont les
    # protections sont encore actives a le statut "En cours", pas "Exécuté".
    executed_sells = [o for o in bd.get("orders", [])
                      if o.get("statut") == "Exécuté"
                      and (o.get("exec_price") or o.get("seuil") or o.get("profit"))]

    for local_key in list(local):
        if local_key in matched_local_keys:
            continue
        cfg = local[local_key]
        base = _local_base(cfg)

        exec_order = next(
            (o for o in executed_sells
             if (o.get("bd_ticker") or "").upper() == base
             or _match_local(o.get("bd_ticker"), o.get("name")) == local_key),
            None,
        )
        if not exec_order:
            # AUCUNE PREUVE de vente (pas d'ordre Vente exécuté sur BD) →
            # clôture REFUSÉE. Cas typique : position achetée il y a quelques
            # secondes, pas encore affichée dans l'onglet positions BD.
            # Incident du 07/07 : AF.PA acheté puis « vendu » fictivement au TP
            # 8s plus tard par le sync post-ordre, budget libéré à tort →
            # cascade d'achats. On ne clôture QUE sur preuve d'exécution.
            lines.append(
                f"⚠️ {local_key} absent des positions BD mais aucun ordre de vente "
                f"exécuté trouvé — clôture refusée (affichage BD en retard ?).\n"
                f"   Si la vente est réelle : /vendu {local_key} PRIX"
            )
            continue

        exit_price = (exec_order.get("exec_price")
                      or exec_order.get("profit") or exec_order.get("seuil")
                      or cfg.get("target_high"))
        price_src = ("ordre exécuté BD" if exec_order.get("exec_price")
                     else "niveau de l'ordre exécuté BD")
        if not exit_price:
            lines.append(
                f"⚠️ {local_key} absent de BD, prix de sortie introuvable.\n"
                f"   → Clôture manuelle : /vendu {local_key} PRIX"
            )
            continue

        import stats
        pnl = stats.record_close(local_key, cfg["ticker"], cfg["qty"],
                                 cfg["entry_price"], exit_price)
        pct = ((exit_price - cfg["entry_price"]) / cfg["entry_price"]) * 100
        portfolio.clear_gmail_triggered(local_key)
        data["positions"].pop(local_key, None)
        changed = True
        sold_keys.append(local_key)
        tag = "WIN ✅" if pnl > 0 else "LOSS 🔻"
        lines.append(
            f"💰 {local_key} VENDU — clôturé automatiquement ({tag})\n"
            f"   {cfg['qty']}t @ {exit_price}€ (PRU {cfg['entry_price']}€) | "
            f"P&L {pnl:+.0f}€ ({pct:+.1f}%)\n"
            f"   Prix : {price_src}"
        )

    # ── Ordres en cours sur BD → mettent à jour les SL/TP des positions ──
    # Les ordres BD (Take Profit, Stop Loss) reflètent les vraies protections
    # placées. On aligne target_low (Seuil/SL) et target_high (Profit/TP) dessus.
    orders = bd.get("orders", [])
    # Seuls les ordres actifs ("En cours") sont affichés et synchronisés.
    # Les ordres annulés/exécutés sont déjà filtrés par bourse_direct_reader.
    active_orders = [o for o in orders if o.get("statut") == "En cours"]
    if active_orders:
        lines.append("\nORDRES EN COURS SUR BD")
        for o in active_orders:
            typ    = o.get("type", "?")
            sens   = o.get("sens", "")
            seuil  = o.get("seuil")
            profit = o.get("profit")
            statut = o.get("statut", "")
            o_ticker = (o.get("bd_ticker") or "").upper()
            o_name   = (o.get("name") or "").upper()

            import prices
            csym = prices.currency_symbol(o.get("currency") or "EUR")

            detail = []
            if seuil:
                detail.append(f"SL {seuil}{csym}")
            if profit:
                detail.append(f"TP {profit}{csym}")
            if not detail:
                detail.append("⚠️ SL/TP non lus — voir logs")
            lines.append(f"  {o.get('name','?')} : {sens} {typ} {' | '.join(detail)} ({statut})")

            # Met à jour la position locale correspondante (seulement si elle existe)
            lk = _match_local(o_ticker, o_name)
            if lk:
                pos_cfg = data["positions"][lk]
                if seuil and abs(seuil - pos_cfg.get("target_low", 0)) >= 0.01:
                    lines.append(f"    → SL {lk} : {pos_cfg.get('target_low')} → {seuil}")
                    pos_cfg["target_low"] = seuil
                    changed = True
                if profit and abs(profit - pos_cfg.get("target_high", 0)) >= 0.01:
                    lines.append(f"    → TP {lk} : {pos_cfg.get('target_high')} → {profit}")
                    pos_cfg["target_high"] = profit
                    changed = True

    # ── Réconciliation des ordres autonomes en attente ────────────────────
    # Un enregistrement sans ordre d'achat actif sur BD ni position créée =
    # ordre annulé/expiré → engagement libéré.
    active_buys = {(o.get("bd_ticker") or "").upper()
                   for o in bd.get("orders", [])
                   if o.get("statut") == "En cours" and o.get("sens") == "Achat"}
    for t in list(data.get("auto_pending_orders", {})):
        base = t.split(".")[0].upper()
        has_position = any(_local_base(c) == base for c in data.get("positions", {}).values())
        if base not in active_buys and not has_position:
            data["auto_pending_orders"].pop(t, None)
            changed = True
            lines.append(f"♻️ Ordre autonome {t} disparu de BD (annulé/expiré) — budget libéré.")

    if changed:
        portfolio.save(data)
        lines.append("\npositions.json mis à jour.")
    else:
        lines.append("\nAucune modification détectée.")

    # ── Investissements programmés ───────────────────────────────────────
    programmed = bd.get("programmed", [])
    if programmed:
        lines.append("\nINVESTISSEMENTS PROGRAMMÉS")
        for p in programmed:
            lines.append(f"  {p[:120]}")
    else:
        lines.append("\nInvest. programmés : aucun")

    # Mode silencieux (check horaire) : message uniquement sur événement
    # significatif (vente clôturée ou position achetée détectée).
    if not silent:
        send_fn("✅ Sync BD terminée\n\n" + "\n".join(lines))
    elif sold_keys or added_keys:
        send_fn("🔄 Sync auto BD — exécution détectée\n\n" + "\n".join(lines))
    else:
        print("[sync auto] RAS — portefeuille aligné avec BD")

    # Après une vente : cash libéré → relance immédiate du moteur autonome
    # (s'il est actif) pour chercher où réinvestir sans attendre le prochain check.
    if sold_keys:
        try:
            from analysis import _trigger_autonomous
            _trigger_autonomous(send_fn)
        except Exception as e:
            print(f"[sync] trigger autonome après vente : {e}")

    return True
