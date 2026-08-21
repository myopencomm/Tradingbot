"""
Synchronisation Bourse Direct → positions.json
Appelé par /sync. Met à jour cash + détecte les écarts de positions.
"""
import market
import portfolio
import bourse_direct_reader as reader

# Places BD (MIC) → suffixe Yahoo et devise : source unique dans market.py.
# La table vivait ici ; elle avait un jumeau dans config.py, et leur divergence
# (XNGS présent d'un côté, absent de l'autre) a produit « NVDA.PA ».
MIC_SUFFIX   = market.MIC_SUFFIX
MIC_CURRENCY = {mic: market.currency(f"X{sfx}") for mic, sfx in MIC_SUFFIX.items()}


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


def _yf_ticker(bd_ticker: str, mic: str, currency: str = "") -> str:
    """Ticker Yahoo depuis le mnémo BD + la place — source unique : market.py."""
    return market.yf_ticker(bd_ticker, mic, currency,
                            on_unknown=lambda m: print(f"[sync] {m}"))


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

    # ── Relecture de confirmation — décidée et faite AVANT de charger l'état ──
    # Le sync est le plus long lecteur-modificateur-écrivain de positions.json.
    # Toute lecture BD faite ENTRE le `load()` et le `save()` allonge d'autant
    # la fenêtre pendant laquelle un /sl, un /hold ou le trailing peut écrire —
    # écriture que le `save()` final réverterait sans un mot.
    #
    # Le déclencheur ne regarde donc QUE la lecture BD (positions détenues chez
    # le courtier sans ordre à seuil), plus la liste des HOLD, lue à part et
    # sans verrou : un drapeau `hold` périmé ne peut causer qu'une relecture
    # inutile, jamais une conclusion fausse.
    bd2 = None
    if bd.get("orders_read", True):
        hold_bases = {(c.get("ticker") or "").upper().split(".")[0]
                      for c in portfolio.load().get("positions", {}).values()
                      if c.get("hold")}
        protégés = {(o.get("bd_ticker") or "").upper().split(".")[0]
                    for o in bd.get("orders", [])
                    if o.get("statut") == "En cours" and o.get("seuil")}
        suspects = [p for p in bd.get("positions", [])
                    if (p.get("bd_ticker") or "").upper() not in protégés
                    and (p.get("bd_ticker") or "").upper() not in hold_bases
                    and p.get("qty")]
        if suspects:
            bd2 = reader.get_portfolio(page, send_fn=None)

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
    meta_changed = False   # valorisations BD : sauvegardées, mais jamais
                           # annoncées comme « modification » (elles bougent
                           # à chaque cotation, ce n'est pas un événement)

    def _bd_snapshot(cfg: dict, pos: dict) -> bool:
        """Mémorise cours/valorisation/±value tels que BD les affiche.
        C'est la SEULE source chiffrée pour les titres que yfinance ne cote
        plus (GVN, MCPHY : faillite, cotation suspendue) — sans elle leur P&L
        reste un trou dans le dashboard. Retourne True si quelque chose a bougé."""
        snap = {
            "bd_price":          pos.get("price"),
            "bd_price_currency": pos.get("price_currency"),
            "bd_value_eur":      pos.get("value_eur"),
            "bd_pnl_eur":        pos.get("pnl_eur"),
        }
        moved = False
        for k, v in snap.items():
            if v is not None and cfg.get(k) != v:
                cfg[k] = v
                moved = True

        # Horodatage du relevé : sans lui, impossible de savoir si le cours BD
        # mémorisé date de l'heure passée (sync horaire) ou d'une semaine
        # (session Playwright déconnectée). C'est ce qui décide s'il peut servir
        # de repli quand yfinance saute une séance.
        if pos.get("price") is not None:
            import pytz as _pytz
            from datetime import datetime as _dt
            cfg["bd_price_at"] = _dt.now(_pytz.timezone("Europe/Paris")).isoformat(timespec="minutes")

        # Titre acté sans valeur : BD valorise la ligne à ~0 alors que le PRU
        # dit ce qu'elle a coûté (GVN : 0.26 € pour 133 € investis). Le drapeau
        # permet au dashboard de chiffrer la perte SANS aucun cours — donc dès
        # maintenant, sans attendre un relevé. Réversible si la valeur revient.
        val, pru, qty = pos.get("value_eur"), pos.get("pru"), pos.get("qty")
        if (val is not None and pru and qty
                and (pos.get("pru_currency") or "EUR") == "EUR"):
            worthless = val < 0.01 * pru * qty
            if bool(cfg.get("worthless")) != worthless:
                if worthless:
                    cfg["worthless"] = True
                else:
                    cfg.pop("worthless", None)
                moved = True
        return moved

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
            if _bd_snapshot(data["positions"][local_key], pos):
                meta_changed = True
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
            yf_t = _yf_ticker(bd_ticker, mic, pos_cur)
            new_key = bd_ticker or bd_name.replace(" ", "_")[:20]

            # Ordre autonome en attente correspondant ? → la position naît
            # avec le flag autonome et ses SL/TP d'origine, et l'engagement
            # "en attente" est consommé.
            pending  = data.get("auto_pending_orders") or {}
            auto_key = next((k for k in (yf_t.upper(), (bd_ticker or "").upper())
                             if k and k in pending), None)
            auto_rec = pending.get(auto_key) if auto_key else None

            # Le ticker de l'ordre autonome PRIME sur la reconstruction depuis
            # le MIC : c'est celui que le bot a lui-même choisi, coté par
            # yfinance et validé AVANT de passer l'ordre. Reconstruire ne peut
            # que faire pire (cas NVDA → NVDA.PA, 03/08/2026).
            if auto_key and auto_key != yf_t.upper():
                print(f"[sync] {bd_ticker} : ticker de l'ordre autonome "
                      f"« {auto_key} » retenu au lieu de « {yf_t} » (MIC {mic})")
                yf_t = auto_key
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
                data.setdefault("positions", {})[new_key] = portfolio.new_position(
                    yf_t, bd_qty, round(entry, 5), round(sl, 4), round(tp, 4),
                    bd_name=pos["name"],
                )
                # `opened_at` vaut « découverte », pas « achat » : le sync voit
                # la position au premier passage qui suit l'exécution, dans
                # l'heure. Pour une position déjà là avant le bot, ce serait
                # faux de plusieurs semaines — mais ce chemin ne concerne que
                # les nouvelles apparitions.
                data["positions"][new_key]["opened_at_source"] = "sync"
                if pru_native and pru_cur != pos_cur:
                    # Référence brute BD : sert à ne reconvertir que si BD
                    # change son PRU (cf. mise à jour ci-dessus).
                    data["positions"][new_key]["bd_pru_raw"] = round(bd_pru, 5)
                _bd_snapshot(data["positions"][new_key], pos)
                if auto_rec:
                    data["positions"][new_key]["autonomous"] = True
                    # Ids des jambes SL/TP, récupérés à la création de l'ordre.
                    # Sans eux la protection d'un Expert d'ACHAT est invisible
                    # (le carnet legacy l'ignore) et donc non remontable.
                    if auto_rec.get("protection_ids"):
                        data["positions"][new_key]["protection_ids"] = \
                            auto_rec["protection_ids"]
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
                                 cfg["entry_price"], exit_price,
                                 opened_at=cfg.get("opened_at"))
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
    # `orders_read=False` ⇒ l'onglet « Mes ordres » n'a pas pu être lu : la
    # liste vide ne prouve RIEN (fausse alerte du 11/08/2026). Tout ce qui
    # déduit quelque chose d'une ABSENCE d'ordre est suspendu dans ce cas.
    orders_read = bd.get("orders_read", True)
    # Seuls les ordres actifs ("En cours") sont affichés et synchronisés.
    # Les ordres annulés/exécutés sont déjà filtrés par bourse_direct_reader.
    active_orders = [o for o in orders if o.get("statut") == "En cours"]
    if not orders_read:
        lines.append("\nORDRES EN COURS SUR BD : onglet illisible ce cycle — "
                     "liste NON représentative (aucune conclusion tirée).")
    elif active_orders:
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

    # ── Contrôle de PROTECTION : quelles positions ont encore un stop ? ───
    # Le sync mettait à jour les SL/TP depuis les ordres actifs, mais ne
    # regardait JAMAIS l'inverse : une position gérée SANS aucun ordre de
    # protection ne déclenchait rien. Le /status continuait d'afficher ses
    # SL/TP mémorisés comme s'ils étaient actifs, alors qu'ils ne protégeaient
    # plus rien (cas BAC, 05/08/2026 : l'Expert d'achat portant les
    # protections a expiré le 31/07 à 22h et les a emportées avec lui).
    #
    # Discriminant : un ordre ACTIF portant un seuil (`seuil`) pour ce titre.
    # C'est exactement le contrôle qu'on fait à l'œil sur le carnet BD.
    #
    # ⚠️ Ce contrôle repose sur une ABSENCE — il n'est donc valide que si la
    # lecture des ordres a réellement abouti (`orders_read`), et il se confirme
    # par une RELECTURE avant toute alerte (voir plus bas).
    #
    # ── Protection REMONTABLE ou soudée à l'ordre d'achat ? ──────────────
    # Discriminant établi sur les ids réels du 05/08/2026 :
    #   AIR  4b07d823… « Vente(CPT) … Seuil209.70 En cours »        → annulable
    #   BAC  00e7bd95… « Vente(CPT) … Seuil58.93  En cours »        → annulable
    #   NVDA d57ffcb4… « Achat(CPT) Ordre exécuté … Seuil187.40 »   → PARENT
    # Une protection issue d'un Expert d'ACHAT n'a pas d'id propre : elle est
    # rendue dans le nœud de l'ordre d'achat exécuté, que BD refuse d'annuler
    # (403 légitime). Elle protège, mais le bot ne peut pas la remonter.
    def _cancellable_leg(o) -> str | None:
        for e in (o.get("order_entries") or []):
            txt = (e.get("text") or "")
            if "Vente" in txt and "En cours" in txt and "Ordre exécuté" not in txt:
                return e.get("id")
        return None

    def _protection_sets(order_list):
        """(protégés_par_ticker, protégés_par_nom, remontables_ticker, remontables_nom)."""
        act = [o for o in order_list if o.get("statut") == "En cours"]
        p_bases = {(o.get("bd_ticker") or "").upper().split(".")[0]
                   for o in act if o.get("seuil")}
        p_names = {(o.get("name") or "").upper() for o in act if o.get("seuil")}
        t_bases, t_names = set(), set()
        for o in act:
            if o.get("seuil") and _cancellable_leg(o):
                t_bases.add((o.get("bd_ticker") or "").upper().split(".")[0])
                t_names.add((o.get("name") or "").upper())
        return p_bases, p_names, t_bases, t_names

    protected_bases, protected_names, trailable_bases, trailable_names = \
        _protection_sets(orders)

    def _protection_order(cfg, order_list):
        """L'ordre ACTIF portant le seuil de cette position, ou None.

        Sert uniquement à en lire l'ÉCHÉANCE : `_protection_sets` répond
        « protégée ou non », pas « jusqu'à quand ».
        """
        base = _local_base(cfg)
        bdn  = (cfg.get("bd_name") or "").upper()
        for o in order_list:
            if o.get("statut") != "En cours" or not o.get("seuil"):
                continue
            if (o.get("bd_ticker") or "").upper().split(".")[0] == base:
                return o
            if bdn and (o.get("name") or "").upper() == bdn:
                return o
        return None

    def _is_protected(cfg):
        base = _local_base(cfg)
        bdn = (cfg.get("bd_name") or "").upper()
        return base in protected_bases or (bdn and bdn in protected_names)

    def _is_trailable(cfg):
        base = _local_base(cfg)
        bdn = (cfg.get("bd_name") or "").upper()
        return base in trailable_bases or (bdn and bdn in trailable_names)

    managed = [(n, c) for n, c in data.get("positions", {}).items()
               if not c.get("hold") and c.get("qty")]

    naked = []
    if not orders_read:
        # Onglet ordres non lu : aucune conclusion sur les protections, et
        # surtout aucun drapeau `protected` écrasé — sinon le trailing lirait
        # ensuite un « à nu » fabriqué par une lecture ratée.
        lines.append("\n⚠️ Ordres BD NON LUS ce cycle (onglet illisible) — "
                     "contrôle des protections SUSPENDU, drapeaux inchangés.")
    elif any(not _is_protected(c) for _n, c in managed):
        # Une page à moitié rendue peut livrer une partie seulement des ordres.
        # Avant de crier 🚨, la relecture faite en tête de fonction tranche.
        if not (bd2 and bd2.get("orders_read", False)):
            orders_read = False
            lines.append("\n⚠️ Position(s) vue(s) sans protection, mais la "
                         "RELECTURE des ordres a échoué ou n'a pas eu lieu — "
                         "alerte suspendue (rien de confirmé, drapeaux inchangés).")
        else:
            p_b2, p_n2, t_b2, t_n2 = _protection_sets(bd2.get("orders", []))
            # Union : une protection vue dans l'UNE des deux lectures est une
            # protection réelle. Seule une absence dans les DEUX compte.
            protected_bases |= p_b2
            protected_names |= p_n2
            trailable_bases |= t_b2
            trailable_names |= t_n2

    if orders_read:
        for name, cfg in managed:
            ok    = _is_protected(cfg)
            trail = _is_trailable(cfg)
            was = cfg.get("protected")
            if cfg.get("protected") is not ok:
                cfg["protected"] = ok
                meta_changed = True
            if cfg.get("trailable") is not trail:
                cfg["trailable"] = trail
                meta_changed = True
            # ── Échéance de la protection ────────────────────────────────
            # Mémorisée TANT QU'ELLE EST VISIBLE : une fois l'ordre expiré il
            # a disparu du carnet, et avec lui la seule trace de la date. Sans
            # cette date, `protection_renewal` refuse d'agir (il ne peut plus
            # prouver que reposer allongerait quelque chose) et on retombe sur
            # la simple alerte.
            if ok:
                po = (_protection_order(cfg, orders)
                      or (_protection_order(cfg, bd2.get("orders", [])) if bd2 else None))
                iso = (po or {}).get("validite_iso")
                if iso and cfg.get("protection_expires_at") != iso:
                    cfg["protection_expires_at"] = iso
                    meta_changed = True
            if not ok:
                naked.append((name, cfg, was is not False))   # was: 1re détection ?

    # ── Protections hors carnet : remontables ou vraiment soudées ? ──────
    # `trailable` ne dit qu'une chose : « le carnet expose-t-il une jambe de
    # vente annulable ? ». Ce n'est PAS la même question que « le bot peut-il
    # remonter ce stop ? » — depuis le 05/08/2026, une position achetée en
    # Expert conserve les ids de ses deux jambes (`protection_ids`, les
    # `children` renvoyés par /order/create), et `trailing.py` sait les annuler
    # une par une pour reposer plus haut.
    #
    # Ce message-ci n'avait pas suivi ce correctif : il annonçait « annulation
    # depuis l'interface BD requise » pour JNJ (13/08/2026) alors que ses deux
    # ids étaient bien en base et que le trailing les gérait tout seul.
    hors_carnet = [(n, c) for n, c in data.get("positions", {}).items()
                   if not c.get("hold") and c.get("qty")
                   and c.get("protected") and c.get("trailable") is False]
    welded    = [(n, c) for n, c in hors_carnet if not c.get("protection_ids")]
    par_ids   = [(n, c) for n, c in hors_carnet if c.get("protection_ids")]

    if par_ids:
        lines.append("\n🔁 PROTECTIONS HORS CARNET, REMONTABLES PAR LE BOT")
        for n, c in par_ids:
            lines.append(
                f"  {n} : SL {c.get('target_low')} actif. Absent du carnet (porté "
                f"par l'ordre d'achat Expert), mais ses {len(c['protection_ids'])} "
                f"jambes sont connues — le trailing les annulera et reposera plus "
                f"haut tout seul. Rien à faire."
            )

    if welded:
        lines.append("\n🔒 PROTECTIONS NON REMONTABLES PAR LE BOT")
        for n, c in welded:
            lines.append(
                f"  {n} : SL {c.get('target_low')} actif, mais soudé à l'ordre "
                f"d'ACHAT exécuté (pas d'id annulable). Le trailing ne peut pas "
                f"le remonter — annulation depuis l'interface BD requise."
            )

    if naked:
        lines.append("\n🚨 POSITIONS SANS PROTECTION SUR BD")
        for name, cfg, _first in naked:
            sym = "$" if (cfg.get("bd_price_currency") or "EUR") == "USD" else "€"
            lines.append(
                f"  {name} : AUCUN ordre SL/TP actif au carnet — les seuils "
                f"affichés ({sym}{cfg.get('target_low')} / {sym}{cfg.get('target_high')}) "
                f"ne protègent RIEN.\n"
                f"    → /ordre vendre {cfg['ticker']} {cfg['qty']} expert "
                f"{cfg.get('target_low')} {cfg.get('target_high')}"
            )

    # ── Réconciliation des ordres autonomes en attente ────────────────────
    # Un enregistrement sans ordre d'achat actif sur BD ni position créée =
    # ordre annulé/expiré → engagement libéré.
    # Même dépendance à une ABSENCE que le contrôle de protection : sans lecture
    # aboutie des ordres, on libérerait le budget d'ordres bien vivants.
    if orders_read:
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
        if meta_changed:
            portfolio.save(data)     # valorisations BD seules : silencieux
        lines.append("\nAucune modification détectée.")

    # ── Investissements programmés ───────────────────────────────────────
    programmed = bd.get("programmed", [])
    if programmed:
        lines.append("\nINVESTISSEMENTS PROGRAMMÉS")
        for p in programmed:
            lines.append(f"  {p[:120]}")
    else:
        lines.append("\nInvest. programmés : aucun")

    # Une position qui PERD sa protection est un événement de sécurité : le
    # sync horaire doit rompre son silence, sinon la découverte se fait par
    # hasard des jours plus tard (BAC, non protégé du 31/07 au 05/08).
    newly_naked = [n for n, _c, first in naked if first]

    # Mode silencieux (check horaire) : message uniquement sur événement
    # significatif (vente, achat détecté, ou perte de protection).
    if not silent:
        send_fn("✅ Sync BD terminée\n\n" + "\n".join(lines))
    elif sold_keys or added_keys:
        send_fn("🔄 Sync auto BD — exécution détectée\n\n" + "\n".join(lines))
    elif newly_naked:
        send_fn("🚨 POSITION SANS PROTECTION détectée par le sync auto\n\n"
                + "\n".join(l for l in lines
                             if "SANS PROTECTION" in l or l.startswith("  ") or l.startswith("    ")))
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


def schedule_post_order_sync(cid=None, delay: float = 8.0):
    """Planifie un sync BD silencieux `delay` secondes après un passage d'ordre.

    Détecte les exécutions immédiates (achat limite au cours) sans attendre le
    sync horaire. Silencieux : ne notifie que si un événement est détecté.

    Vivait dans `telegram_bot`, ce qui obligeait le moteur autonome à importer
    tous les handlers Telegram pour planifier un sync. Sa place est ici : c'est
    un sync, pas une commande.
    """
    import threading
    import playwright_session
    import tg

    def _run():
        try:
            playwright_session.run(
                lambda page: sync(page, lambda m: tg.send(m, cid), silent=True),
                timeout=90,
            )
        except Exception as e:
            print(f"[post-order sync] {e}")

    threading.Timer(delay, _run).start()


def schedule_order_verification(order_id: str, ticker: str, send_fn,
                                delay: float = 45.0) -> None:
    """Vérifie qu'un ordre passé sur BD a bien SURVÉCU, et le dit.

    `/order/create` répond 200 avec un id et ses jambes de protection — puis BD
    peut rejeter l'ordre APRÈS coup, sans prévenir. Le bot annonçait donc
    « ✅ ORDRE AUTONOME PLACÉ SUR BD », créditait l'engagement au budget, et ne
    revenait jamais vérifier. Le 18/08/2026, l'achat de RTX a été refusé par BD
    quelques minutes après : découvert sur le téléphone, jamais annoncé.

    Annoncer un succès sans en contrôler l'issue, c'est la même faute que
    l'alerte du watchdog qui ne revenait pas sur son verdict.

    Le délai laisse à BD le temps de statuer : le rejet n'est pas instantané.
    """
    import threading

    import playwright_session
    import portfolio
    import tg

    def _verifier():
        try:
            bd = playwright_session.run(
                lambda page: reader.get_portfolio(page, send_fn=None), timeout=90)
        except Exception as e:
            print(f"[verif ordre] {ticker} : lecture impossible ({e})")
            return
        # Onglet ordres illisible : on ne conclut rien — même règle que le
        # contrôle de protection (11/08). Une liste vide n'est pas une preuve.
        if not (bd and bd.get("orders_read", True)):
            print(f"[verif ordre] {ticker} : ordres non lus, aucune conclusion")
            return

        base = (ticker or "").upper().split(".")[0]
        mien = [o for o in bd.get("orders", [])
                if order_id and order_id in (o.get("order_ids") or [o.get("order_id")])]
        if not mien:
            mien = [o for o in bd.get("orders", [])
                    if (o.get("bd_ticker") or "").upper() == base]
        statut = mien[0].get("statut") if mien else None

        if statut == "Rejeté":
            # L'ordre n'existe pas : l'engagement doit être rendu au budget,
            # sinon il gèlerait une place jusqu'à l'expiration.
            portfolio.clear_auto_pending_order(ticker)
            (send_fn or tg.send)(
                f"🚫 {ticker} : ORDRE REJETÉ PAR BOURSE DIRECT\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"L'ordre a bien été créé puis refusé par BD — aucun titre "
                f"acheté, aucun euro engagé.\n"
                f"BD ne donne pas de motif ici : ouvre le détail de l'ordre "
                f"dans l'app pour le voir.\n\n"
                f"Budget libéré. Les deux jambes de protection peuvent rester "
                f"affichées « en cours » côté BD alors qu'elles n'ont aucun "
                f"titre à vendre — annule l'ordre depuis l'app pour nettoyer."
            )
        elif statut is None:
            print(f"[verif ordre] {ticker} : introuvable au carnet "
                  f"(exécuté et déjà soldé ?) — le sync tranchera")
        else:
            print(f"[verif ordre] {ticker} : statut « {statut} » — rien à signaler")

    threading.Timer(delay, _verifier).start()
