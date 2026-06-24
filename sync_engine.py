"""
Synchronisation Bourse Direct → positions.json
Appelé par /sync. Met à jour cash + détecte les écarts de positions.
"""
import portfolio
import bourse_direct_reader as reader


def sync(page, send_fn) -> bool:
    """
    Lit le portefeuille depuis BD et met à jour positions.json.
    `page` fourni par playwright_session.run() (thread worker).
    Retourne True si sync réussie.
    """
    send_fn("Synchronisation avec Bourse Direct...")

    bd = reader.get_portfolio(page, send_fn=send_fn)
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

    matched_local_keys = set()

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
            lines.append(
                f"⚠️ {pos['name']} ({bd_ticker}) sur BD : {bd_qty}t @ {bd_pru}€\n"
                f"   → pas dans le bot. /add NOM {bd_ticker}.PA {bd_qty} {bd_pru} <SL> <TP>"
            )

    # ── Positions locales absentes de BD → probablement vendues ─────────
    # Détection de clôture : si une position du bot n'est plus sur BD,
    # elle a été vendue. On propose la commande de clôture (avec le prix TP
    # comme estimation car l'ordre TP est le scénario de sortie le plus courant).
    for local_key in local:
        if local_key not in matched_local_keys:
            cfg = local[local_key]
            tp = cfg.get("target_high")
            suggestion = f"/vendu {local_key} {tp}" if tp else f"/vendu {local_key} PRIX"
            lines.append(
                f"⚠️ {local_key} absent de BD — VENDU ?\n"
                f"   → Confirme la cloture : {suggestion}"
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

    send_fn("✅ Sync BD terminée\n\n" + "\n".join(lines))
    return True
