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


def _yf_ticker(bd_ticker: str, mic: str) -> str:
    """Reconstruit le ticker yfinance depuis le mnémo BD + la place (MIC)."""
    bd_ticker = (bd_ticker or "").upper()
    suffix = MIC_SUFFIX.get((mic or "").upper(), ".PA")  # défaut Paris
    return f"{bd_ticker}{suffix}" if bd_ticker else ""


def sync(page, send_fn, silent: bool = False) -> bool:
    """
    Lit le portefeuille depuis BD et met à jour positions.json.
    `page` fourni par playwright_session.run() (thread worker).
    silent=True (check horaire auto) : aucun message sauf si une vente ou un
    achat exécuté est détecté.
    Retourne True si sync réussie.
    """
    if not silent:
        send_fn("Synchronisation avec Bourse Direct...")

    bd = reader.get_portfolio(page, send_fn=None if silent else send_fn)
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
        prot = {"seuil": o.get("seuil"), "profit": o.get("profit"), "mic": o.get("mic", "")}
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

        local_key = _match_local(bd_ticker, bd_name)

        if local_key:
            matched_local_keys.add(local_key)
            loc = local[local_key]
            sub = []
            if loc["qty"] != bd_qty:
                sub.append(f"qté {loc['qty']} → {bd_qty}")
                data["positions"][local_key]["qty"] = bd_qty
                changed = True
            if bd_pru and abs((bd_pru - loc["entry_price"]) / max(loc["entry_price"], 0.001)) > 0.001:
                sub.append(f"PRU {loc['entry_price']} → {bd_pru}")
                data["positions"][local_key]["entry_price"] = round(bd_pru, 5)
                changed = True
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

            sl = prot.get("seuil")
            tp = prot.get("profit")
            # Sans SL/TP connus (aucun ordre Expert actif) : valeurs par défaut -7%/+10%
            if not sl and bd_pru:
                sl = round(bd_pru * 0.93, 4)
            if not tp and bd_pru:
                tp = round(bd_pru * 1.10, 4)

            if not yf_t or bd_qty is None or not bd_pru:
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
                    "entry_price": round(bd_pru, 5),
                    "target_high": round(tp, 4),
                    "target_low":  round(sl, 4),
                    "bd_name":     pos["name"],
                }
                local[new_key] = data["positions"][new_key]
                matched_local_keys.add(new_key)
                added_keys.append(new_key)
                changed = True
                src = "SL/TP lus sur l'ordre BD" if prot.get("seuil") or prot.get("profit") else "SL/TP par défaut -7%/+10%"
                lines.append(
                    f"➕ {new_key} ({yf_t}) AJOUTÉ auto : {bd_qty}t @ {bd_pru}€ | "
                    f"SL {sl}€ TP {tp}€ ({src})"
                )

    # ── Positions locales absentes de BD → vendues : clôture AUTOMATIQUE ──
    # L'ordre de vente exécuté sur BD donne le prix réel de sortie (volet TP
    # ou SL). Le cash BD, déjà synchronisé plus haut, inclut le produit de la
    # vente — on ne l'ajoute donc PAS une deuxième fois.
    executed_sells = [o for o in bd.get("orders", [])
                      if o.get("statut") == "Exécuté" and o.get("sens") == "Vente"]

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
        exit_price, price_src = None, None
        if exec_order:
            exit_price = (exec_order.get("exec_price")
                          or exec_order.get("profit") or exec_order.get("seuil"))
            price_src = "ordre exécuté BD"
        if not exit_price:
            exit_price = cfg.get("target_high")
            price_src = "TP posé (estimation — ordre exécuté non lu)"
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

            detail = []
            if seuil:
                detail.append(f"SL {seuil}€")
            if profit:
                detail.append(f"TP {profit}€")
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
