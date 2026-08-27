"""
Sortie sur STAGNATION — le capital doit tourner, jamais à perte.

LE KPI
------
Ce n'est pas le gain, c'est le gain PAR JOUR. Mesuré sur les 15 trades clos au
27/08/2026 :

    2CRSI  +141.10 €  en  2.0 j  →  70.55 €/jour
    NVDA   +120.56 €  en  9.1 j  →  13.28 €/jour
    VU     +138.11 €  en 11.0 j  →  12.56 €/jour
    ...
    GLE     +83.87 €  en 18.0 j  →   4.66 €/jour
    UNA     +95.66 €  en 28.0 j  →   3.42 €/jour
    AIR     +70.75 €  en 40.1 j  →   1.76 €/jour

Le gain final se ressemble ; la vitesse, pas du tout. Passé ~17 jours, plus
aucun trade n'a dépassé 5 €/jour. Or la durée était MESURÉE partout (/stats,
dashboard, €/jour, « meilleur gain par jour ») et n'entrait dans AUCUNE
décision : une position sans catalyseur dormait jusqu'au SL ou au TP, donc
indéfiniment. BAC est resté 28 jours pour -2%.

LA RÈGLE
--------
Des jalons sur le chemin PRU→TP, en JOURS DE BOURSE. À J+STALE_DAYS_1 la
position doit en avoir parcouru STALE_PROGRESS_1 %. Sinon elle stagne, et le
capital repart ailleurs.

Ce n'est pas un stop de plus : le SL protège du RISQUE, ce jalon-ci protège du
TEMPS. Les deux se lisent sur des axes différents.

⚠️ CETTE RÈGLE NE VEND PLUS RIEN — OBSERVATION SEULE
----------------------------------------------------
Backtestée deux fois (27/08/2026), elle FAIT BAISSER le P&L. Plus les jalons
serrent, plus le taux de réussite monte et plus le résultat baisse :

    univers 137 titres     sans jalon  -812 €   →  J+10/25%+J+15/50%  -949 €
    univers 608 titres    sans jalon -1602 €   →  J+10/25%+J+15/50% -1856 €

Elle coupe les positions lentes qui finissaient par payer — une seule fenêtre
(mars→oct. 2024) perd 534 € parce qu'un titre vendu à 33% de son chemin a
continué sans nous. L'argument « le capital libéré rachètera mieux » a été
testé et ne tient pas : le capital EST réinvesti (jusqu'à +20 trades), dans
des trades qui ne valent pas celui qu'on vient de couper. Élargir l'univers
au périmètre réel du scan ne change rien non plus.

ROLLBACK décidé le 27/08/2026 : `STALE_EXIT` vaut **off** par défaut. Le
module reste — il calcule et affiche toujours le verdict de vitesse, que
`/stagnation` expose, parce que savoir quelles lignes traînent est utile. Ce
qui a été retiré, c'est la VENTE automatique. Passer STALE_EXIT=on la
réactive, en connaissance de cause.

LA CONTRAINTE : NE JAMAIS VENDRE À PERTE
----------------------------------------
Décision explicite de l'utilisateur (27/08/2026) : une sortie sur stagnation ne
doit jamais matérialiser une perte. Le seuil n'est donc pas le PRU, c'est le
PRU + LES FRAIS DE SORTIE (`config.breakeven_price`) — vendre au PRU pile
perdrait le courtage de vente et, sur un titre en devise, la commission de
change.

Conséquence à assumer, et elle est lourde : une position stagnante ET dans le
rouge n'est PAS vendue. Elle est signalée, une fois par jour, et continue
d'immobiliser son capital jusqu'à ce qu'elle repasse au-dessus du point mort ou
touche son SL. Au 27/08/2026, BAC (-2.0%, 28 jours) et CA (-2.6%) tombent
exactement dans ce cas : la règle ne les libère pas. Elle ne récupère que les
positions lentes et VERTES.
"""
from datetime import datetime, timezone

import bot_mode
import config
import market
import playwright_session
import portfolio
import prices

# Une alerte de blocage par position et par jour : le cycle repasse toutes les
# heures et le message ne change pas tant que le cours n'a pas bougé.
_notified_stuck: dict[str, str] = {}
# Positions dont la vente a échoué : on ne réessaie pas en boucle.
_sell_failed: set[str] = set()


def _age_days(pos: dict, now: datetime) -> float | None:
    """Âge de la position en JOURS DE BOURSE.

    Pas en jours calendaires : un week-end ne fait pas stagner une position, il
    n'y a simplement pas eu de séance. C'est aussi l'unité dans laquelle les
    jalons ont été calibrés (backtest du 27/08/2026, `np.busday_count`) —
    mélanger les deux décalerait les seuils de ~40%.
    """
    op = pos.get("opened_at")
    if not op:
        return None
    try:
        d = datetime.fromisoformat(op)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    if now < d:
        return 0.0
    import numpy as np
    return float(np.busday_count(d.date(), now.date()))


def tp_path_pct(pos: dict, price: float) -> float | None:
    """Part du chemin PRU → TP déjà parcourue, en %. Négatif = sous le PRU."""
    entry = pos.get("entry_price") or 0
    tp    = pos.get("target_high") or 0
    if not entry or not tp or tp <= entry or not price:
        return None
    return (price - entry) / (tp - entry) * 100


def required_progress(age_days: float) -> tuple[float, float] | None:
    """Jalon applicable à cet âge : (jours du jalon, % de chemin exigé).

    Le PLUS EXIGEANT des jalons franchis — sinon une position de 20 jours
    serait jugée sur le jalon de 10.
    """
    jalons = [(config.STALE_DAYS_1, config.STALE_PROGRESS_1),
              (config.STALE_DAYS_2, config.STALE_PROGRESS_2)]
    # Un jalon à 0 jour est DÉSACTIVÉ, pas « applicable dès l'ouverture » — le
    # second l'est par défaut (voir config).
    passes = [j for j in jalons if j[0] > 0 and age_days >= j[0]]
    return max(passes, key=lambda j: j[1]) if passes else None


def verdict(pos: dict, price: float, now: datetime,
            fx: float = 1.0) -> tuple[str, str]:
    """Que faire de cette position ? Fonction PURE, testable sans BD.

    Retourne (action, raison) avec action dans :
      "vendre"  — stagnante ET au-dessus du point mort : on encaisse
      "bloquee" — stagnante mais dans le rouge : on signale, on ne vend pas
      "garder"  — dans les temps, ou hors périmètre
    """
    # Le verdict de VITESSE se calcule toujours, même règle désactivée : c'est
    # ce que `/stagnation` affiche. Seule l'exécution est conditionnée à
    # STALE_EXIT, et c'est `stale_exit_cycle` qui la garde.
    if pos.get("hold") or not pos.get("qty"):
        return "garder", "hors gestion bot"
    entry = pos.get("entry_price")
    if not entry or not pos.get("target_high"):
        return "garder", "PRU ou TP manquant — rien à mesurer"

    age = _age_days(pos, now)
    if age is None:
        # Sans date d'ouverture, aucune vitesse ne se calcule. Ne jamais
        # supposer : les positions d'avant le suivi de durée seraient toutes
        # jugées stagnantes le jour de la mise en service.
        return "garder", "date d'ouverture inconnue — vitesse non mesurable"

    jalon = required_progress(age)
    if not jalon:
        return "garder", (f"{age:.0f} j de bourse — premier jalon à "
                          f"{config.STALE_DAYS_1:.0f} j")
    jours_jalon, exige = jalon

    parcouru = tp_path_pct(pos, price)
    if parcouru is None:
        return "garder", "chemin PRU→TP non calculable"
    if parcouru >= exige:
        return "garder", (f"{age:.0f} j de bourse, {parcouru:.0f}% du chemin vers le TP "
                          f"(jalon J+{jours_jalon:.0f} : {exige:.0f}%) — dans les temps")

    pt_mort = config.breakeven_price(pos["ticker"], entry, abs(int(pos["qty"])), fx)
    manque  = (pt_mort / price - 1) * 100
    if price < pt_mort:
        return "bloquee", (
            f"stagnante ({age:.0f} j de bourse, {parcouru:.0f}% du chemin, "
            f"jalon J+{jours_jalon:.0f} : {exige:.0f}%) MAIS sous le point mort "
            f"{pt_mort} — vendre perdrait {manque:.1f}%. Conservée.")
    return "vendre", (
        f"stagnante : {age:.0f} j de bourse pour {parcouru:.0f}% du chemin "
        f"(jalon J+{jours_jalon:.0f} : {exige:.0f}%). Au-dessus du point mort "
        f"{pt_mort} — on encaisse et le capital repart.")


def _annuler_protections(pos: dict, name: str) -> bool:
    """Annule les jambes SL/TP avant de vendre.

    Obligatoire : les titres sont ENGAGÉS par l'ordre Expert de protection.
    Envoyer une vente au marché par-dessus, c'est vendre deux fois la même
    ligne — le doublon que tout le reste du bot s'emploie à éviter.
    """
    import bourse_direct_orders as bd_orders
    import bourse_direct_reader as reader

    oids = [o for o in (pos.get("protection_ids") or []) if o]
    if oids:
        for oid in oids:
            try:
                playwright_session.run(
                    lambda page, o=oid: bd_orders.cancel_order(page, o), timeout=30)
            except Exception as e:
                print(f"[Stagnation] {name} cancel {oid} : {e}")

    # Jambes visibles au carnet legacy (protections posées en ordre de VENTE) :
    # elles ont une référence annulable que `protection_ids` n'a pas.
    try:
        rows = playwright_session.run(
            lambda page: reader.read_order_book(page), timeout=90) or []
        base = pos["ticker"].upper().split(".")[0]
        for o in rows:
            if (o.get("ticker") or "").upper().split(".")[0] == base:
                playwright_session.run(
                    lambda page, r=o["ref"], rb=o["refbo"]:
                        bd_orders.cancel_legacy_order(page, r, rb), timeout=30)
    except Exception as e:
        print(f"[Stagnation] {name} carnet legacy : {e}")

    # BD annule en ASYNCHRONE : on vérifie que plus rien ne porte de seuil sur
    # ce titre avant d'envoyer la vente.
    import time
    time.sleep(5)
    etat = playwright_session.run(
        lambda page: reader.get_portfolio(page, send_fn=None), timeout=90) or {}
    if not etat.get("orders_read"):
        print(f"[Stagnation] {name} : carnet illisible après annulation — vente abandonnée")
        return False
    base = pos["ticker"].upper().split(".")[0]
    reste = [o for o in etat.get("orders", [])
             if o.get("statut") == "En cours" and o.get("seuil")
             and (o.get("bd_ticker") or "").upper().split(".")[0] == base]
    return not reste


def _vendre_au_marche(pos: dict, name: str, send_fn) -> bool:
    import bourse_direct_orders as bd_orders
    qty = abs(int(pos["qty"]))
    try:
        od = playwright_session.run(
            lambda page, t=pos["ticker"], q=qty:
                bd_orders.create_order(page, t, side="sell", qty=q,
                                       order_type="market", validity="seance"),
            timeout=30)
    except Exception as e:
        print(f"[Stagnation] {name} create sell : {e}")
        return False
    oid = od and (od.get("id") or od.get("order_id"))
    if not oid:
        return False
    try:
        return bool(playwright_session.run(
            lambda page, o=oid: bd_orders.send_order(page, o), timeout=30))
    except Exception as e:
        print(f"[Stagnation] {name} send : {e}")
        return False


def stale_exit_cycle(send_fn, verbose: bool = False,
                     now: datetime | None = None) -> None:
    """Vend les positions stagnantes ET vertes, signale les stagnantes rouges.

    Appelé par le cycle horaire. Silencieux quand il n'y a rien à faire.
    """
    now = now or datetime.now(timezone.utc)
    aujourd_hui = now.date().isoformat()
    # Règle désactivée : aucun cours n'est interrogé et rien n'est envoyé. Sauf
    # demande explicite (`/stagnation`), qui veut le constat sans la vente.
    if not config.STALE_EXIT and not verbose:
        return
    data = portfolio.load()

    lignes = []
    for name, pos in (data.get("positions") or {}).items():
        if pos.get("hold") or not pos.get("qty"):
            continue
        quote = prices.get_quote(pos["ticker"])
        price = portfolio.best_price(pos, quote).get("price")
        if not price:
            continue
        fx = prices.fx_to_eur(prices._ticker_currency(pos["ticker"]))
        action, raison = verdict(pos, price, now, fx)
        lignes.append((name, pos, price, action, raison))

    if verbose:
        etat = ("VENTE AUTOMATIQUE ACTIVE" if config.STALE_EXIT else
                "observation seule — aucune vente (STALE_EXIT=off)")
        head = [f"⏱️ VITESSE DES POSITIONS — {etat}",
                f"Jalon : {config.STALE_PROGRESS_1:.0f}% du chemin PRU→TP à "
                f"J+{config.STALE_DAYS_1:.0f} jours de bourse."]
        if not config.STALE_EXIT:
            head.append("Rollback du 27/08/2026 : la vente sur stagnation coûtait "
                        "du rendement au backtest. Le constat reste, l'action non.")
        for n, _p, _pr, a, r in lignes:
            icone = {"vendre": "💰", "bloquee": "🔒"}.get(a, "✅")
            head.append(f"  {icone} {n} : {r}")
        if not any(a != "garder" for _n, _p, _pr, a, _r in lignes):
            head.append("\n✅ Rien à libérer.")
        send_fn("\n".join(head))

    if not config.STALE_EXIT:
        return          # /stagnation a rendu son constat, on ne vend rien
    for name, pos, price, action, raison in lignes:
        if action == "bloquee":
            if _notified_stuck.get(name) != aujourd_hui:
                _notified_stuck[name] = aujourd_hui
                send_fn(f"🔒 CAPITAL BLOQUÉ — {name}\n{raison}\n"
                        f"Aucune vente : ta règle est de ne jamais sortir à perte.")
            continue
        if action != "vendre" or name in _sell_failed:
            continue
        if not (bot_mode.is_playwright() and playwright_session.is_connected()):
            send_fn(f"⏱️ {name} : {raison}\n"
                    f"Session BD déconnectée — /connect puis la vente partira seule.")
            continue
        if not market.is_open_now(pos["ticker"]):
            print(f"[Stagnation] {name} : marché fermé, vente reportée")
            continue

        send_fn(f"⏱️ SORTIE SUR STAGNATION — {name}\n{raison}\nAnnulation de la "
                f"protection puis vente au marché…")
        if not _annuler_protections(pos, name):
            _sell_failed.add(name)
            send_fn(f"⚠️ {name} : protection NON annulée de façon certaine — "
                    f"vente abandonnée (vendre par-dessus une protection active "
                    f"créerait un doublon).\n✅ La position reste protégée.")
            continue
        if _vendre_au_marche(pos, name, send_fn):
            _notified_stuck.pop(name, None)
            send_fn(f"💰 VENDU — {name}\nOrdre au marché envoyé à BD. Le prochain "
                    f"sync enregistrera le trade et libérera le budget.")
        else:
            _sell_failed.add(name)
            send_fn(f"🚨 {name} : protection annulée mais VENTE REFUSÉE par BD — "
                    f"la position est à nu.\n"
                    f"À faire : /ordre vendre {pos['ticker']} {abs(int(pos['qty']))} marche\n"
                    f"ou remets la protection : /ordre vendre {pos['ticker']} "
                    f"{abs(int(pos['qty']))} expert {pos.get('target_low')} "
                    f"{pos.get('target_high')}")


def rearm() -> None:
    """Oublie les échecs et alertes déjà envoyés (appelé par /stagnation)."""
    _notified_stuck.clear()
    _sell_failed.clear()
