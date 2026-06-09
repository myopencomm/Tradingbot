"""
Synchronisation Bourse Direct → positions.json
Appelé par /sync. Met à jour cash + détecte les écarts de positions.
"""
import portfolio
import bourse_direct_reader as reader


def sync(send_fn) -> bool:
    """
    Lit le portefeuille depuis BD et met à jour positions.json.
    Retourne True si sync réussie.
    """
    send_fn("Synchronisation avec Bourse Direct...")

    bd = reader.get_portfolio(send_fn=send_fn)
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
    bd_names = {p["name"].upper(): p for p in bd["positions"]}

    for pos in bd["positions"]:
        bd_name = pos["name"].upper()
        bd_qty  = pos["qty"]
        bd_pru  = pos["pru"]

        # Cherche la position locale par nom ou ticker (base sans suffixe)
        local_key = next(
            (k for k in local
             if k.upper() == bd_name
             or k.upper() == bd_name.replace(" ", "")
             or local[k]["ticker"].upper().split(".")[0] == bd_name.split("(")[0].strip()),
            None
        )

        if local_key:
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
                cours_str = f" (cours {pos['cours']}€)" if pos["cours"] else ""
                lines.append(f"{local_key} OK — {bd_qty}t{cours_str}")
        else:
            # Position sur BD mais pas dans le bot
            lines.append(
                f"⚠️ {pos['name']} sur BD ({bd_qty}t @ {bd_pru}€) — pas dans le bot\n"
                f"   → /add {pos['name']} <TICKER> {bd_qty} {bd_pru} <SL> <TP>"
            )

    # ── Positions locales absentes de BD ────────────────────────────────
    for local_key in local:
        bd_match = any(
            local_key.upper() == n
            or local_key.upper() == n.replace(" ", "")
            or local[local_key]["ticker"].upper().split(".")[0] == n.split("(")[0].strip()
            for n in bd_names
        )
        if not bd_match:
            lines.append(
                f"⚠️ {local_key} dans le bot mais absent de BD\n"
                f"   → Vendu ou mauvais ticker ?"
            )

    if changed:
        portfolio.save(data)
        lines.append("\npositions.json mis à jour.")
    else:
        lines.append("\nAucune modification détectée.")

    send_fn("✅ Sync BD terminée\n\n" + "\n".join(lines))
    return True
