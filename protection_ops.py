"""
Opérations de protection d'une position sur Bourse Direct — partagées par
/securiser, /sortie_mini (telegram_bot) et la réanalyse autonome (reanalyse).

Séparé de telegram_bot pour que la réanalyse puisse s'en servir sans importer
le bot Telegram (cycle d'imports, 14/10/2026). Les messages passent par un
`send_fn` injecté.
"""
import playwright_session
import portfolio


def stop_gain_minimum(pos: dict, gain_eur: float = 1.0) -> float | None:
    """Cours de stop qui garantit AU MOINS `gain_eur` net, frais de vente
    compris, dans la devise du titre. Le PRU de BD inclut déjà les frais
    d'achat ; config.breakeven_price ajoute ceux de la sortie. Marge de 0,05 %
    pour l'exécution : un stop déclenche une vente au marché, qui peut passer
    un cran sous le seuil. Arrondi au pas de cotation, vers le HAUT."""
    import prices
    import ticks
    from config import breakeven_price
    qty = abs(int(pos.get("qty") or 0))
    if not qty or not pos.get("entry_price"):
        return None
    from config import order_fees
    cur = prices.get_quote(pos["ticker"]).get("currency") or "EUR"
    fx = prices.fx_to_eur(cur)
    if cur != "EUR" and pos.get("bd_pru_raw") and fx and fx != 1.0:
        # Titre en devise : le gain se mesure EN EUROS, change compris, contre
        # le PRU brut BD (frais d'achat inclus). Marge de 0,5 % : le dollar
        # peut bouger entre la pose du stop et son déclenchement.
        cout = pos["bd_pru_raw"] * qty
        stop = (cout + gain_eur) / (qty * fx)
        for _ in range(3):
            stop = (cout + gain_eur + order_fees(pos["ticker"], stop * qty * fx, "sell")) / (qty * fx)
        stop *= 1.005
    else:
        be = breakeven_price(pos["ticker"], pos["entry_price"], qty, fx)
        stop = (be + gain_eur / fx / qty) * 1.0005
    return ticks.round_to_tick(stop, ticks.tick_for(stop, cur), "up")


def remplacer_protection(nom: str, pos: dict, stop: float, tp: float, send_fn,
                         label: str, on_ok) -> None:
    """Remplace la protection SL/TP d'une position sur BD — mécanique commune
    à /securiser et /sortie_mini. Annulation VÉRIFIÉE au carnet d'abord (rien
    n'est reposé sinon : pas de doublon de vente), puis nouvel ordre Expert.
    Si le nouvel ordre échoue, la position est signalée SANS STOP avec la
    commande pour la reprotéger."""
    import bourse_direct_orders as bd_orders
    import stale_exit
    qty = abs(int(pos["qty"]))
    send_fn(f"🔁 {nom} : annulation de la protection actuelle…")
    if not stale_exit._annuler_protections(pos, nom):
        send_fn(stale_exit.message_echec_annulation(pos, nom, "nouvelle protection"))
        return
    od = conf = None
    try:
        od = playwright_session.run(
            lambda page: bd_orders.create_expert_order(
                page, pos["ticker"], qty, stop, tp, "max"), timeout=30)
        oid = od and (od.get("id") or od.get("order_id"))
        if oid:
            conf = playwright_session.run(
                lambda page: bd_orders.confirm_order_auto(page, oid, False),
                timeout=30)
    except Exception as e:
        print(f"[{label}] {nom} : {e}")
    if not conf:
        send_fn(f"🚨 {nom} : ancienne protection annulée mais NOUVELLE NON "
             f"CONFIRMÉE — position SANS STOP.\nÀ faire tout de suite :\n"
             f"/ordre vendre {pos['ticker']} {qty} expert {stop} {tp}")
        return
    adj = (od.get("_adjusted") or {})
    d = portfolio.load()
    if nom in d.get("positions", {}):
        d["positions"][nom]["target_low"] = adj.get("stop_loss") or stop
        d["positions"][nom]["target_high"] = adj.get("take_profit") or tp
        d["positions"][nom]["protection_ids"] = [c for c in (od.get("children") or []) if c]
        d["positions"][nom]["protected"] = True
        d["positions"][nom].pop("pending_sl", None)
        portfolio.save(d)
    on_ok(od, adj)
