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
    # Matching prioritaire sur le ticker BD (ex: "EXENS" ↔ "EXENS.PA")
    def _local_base(cfg):
        return cfg["ticker"].upper().split(".")[0]

    matched_local_keys = set()

    for pos in bd["positions"]:
        bd_ticker = (pos.get("bd_ticker") or "").upper()
        bd_name   = pos["name"].upper()
        bd_qty    = pos["qty"]
        bd_pru    = pos["pru"]

        # 1) ticker base exact  2) nom de position  3) nom collé
        local_key = next(
            (k for k, cfg in local.items()
             if _local_base(cfg) == bd_ticker
             or k.upper() == bd_name
             or k.upper() == bd_name.replace(" ", "")),
            None
        )

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

    # ── Positions locales absentes de BD ────────────────────────────────
    for local_key in local:
        if local_key not in matched_local_keys:
            lines.append(
                f"⚠️ {local_key} dans le bot mais absent de BD — vendu ?"
            )

    if changed:
        portfolio.save(data)
        lines.append("\npositions.json mis à jour.")
    else:
        lines.append("\nAucune modification détectée.")

    # ── Ordres en cours sur BD ───────────────────────────────────────────
    orders = bd.get("orders", [])
    if orders:
        lines.append("\nORDRES EN COURS SUR BD")
        for o in orders:
            typ   = o.get("type", "?")
            sens  = o.get("sens", "")
            seuil = o.get("seuil")
            profit = o.get("profit")
            statut = o.get("statut", "")
            detail = []
            if seuil:
                detail.append(f"SL {seuil}€")
            if profit:
                detail.append(f"TP {profit}€")
            detail_str = " | ".join(detail)
            lines.append(f"  {sens} {typ} : {detail_str} ({statut})")

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
