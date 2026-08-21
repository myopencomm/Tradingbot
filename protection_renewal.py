"""
Renouvellement des protections expirées — repose le SL/TP au mois suivant.

LE PROBLÈME
-----------
Bourse Direct n'accepte jamais un `validityDate` nul : toute protection porte
une échéance, et la plus lointaine qu'il propose est bornée par le marché
(`bd_orders.parse_validity`) — 31/12 sur Euronext, FIN DU MOIS COURANT partout
ailleurs, US compris. Passé cette date l'ordre disparaît du carnet et la
position se retrouve nue, sans que rien ne l'annonce.

C'est arrivé : BAC, ouvert le 30/07/2026, protégé par un Expert d'achat dont la
validité expirait le 31/07 à 22h — soit le LENDEMAIN de l'entrée. La position
est restée sans stop du 31/07 au 05/08, découverte par hasard. Le contrôle de
protection du sync (`sync_engine`) a été écrit après coup : il voit le trou,
mais il ne fait que le signaler.

CE QUI NE MARCHE PAS
--------------------
« Reposer la protection quelques jours AVANT l'échéance » : sur un titre US,
« max » se recalcule depuis le jour de la pose et rend la MÊME fin de mois.
Reposer BAC le 21/08 le ferait expirer... le 31/08. On ne gagne rien, et on
paie une fenêtre d'annulation/repose pour rien.

La seule repose qui allonge quelque chose est celle faite APRÈS la bascule du
mois. C'est exactement la condition testée ici : `max_validity_deadline`
d'aujourd'hui doit être POSTÉRIEURE à l'échéance en cours.

POURQUOI CE N'EST PAS UN TROU DE PROTECTION
-------------------------------------------
L'échéance tombe à la CLÔTURE du marché concerné (22:00 Paris pour le NYSE,
17:35 pour Euronext). Entre l'expiration et la repose au premier cycle de la
séance suivante, le marché est fermé : aucun ordre n'aurait pu s'exécuter de
toute façon. Le trou est calendaire, pas boursier.

POURQUOI ON NE REPOSE PAS SUR LA SEULE DÉTECTION « À NU »
---------------------------------------------------------
Parce qu'une lecture partielle du carnet rend une position protégée comme
« sans protection » (fausse alerte du 11/08/2026, trois positions d'un coup) et
que reposer un Expert de VENTE sur cette base créerait un DOUBLON DE VENTE sur
des titres déjà engagés. Il faut donc deux preuves indépendantes :

  1. une preuve de DATE, locale et déterministe — l'échéance mémorisée est
     dépassée, et une repose aujourd'hui donnerait une date plus lointaine ;
  2. une preuve de CARNET — deux lectures abouties, aucune ne montrant d'ordre
     actif à seuil sur ce titre.

Sans échéance mémorisée (position jamais vue protégée par le sync), on ne
renouvelle PAS : on retombe sur l'alerte de `sync_engine`.
"""
from datetime import date, datetime

import bourse_direct_orders as bd_orders
import bourse_direct_reader as reader
import playwright_session
import portfolio

# Une repose échouée par position et par jour : BD peut refuser l'ordre (marché
# fermé, pas de cotation, quantité engagée) et le cycle horaire repasse toutes
# les heures. Sans ce garde-fou, un refus persistant = 13 messages par jour.
_notified_failure: dict[str, date] = {}


def _iso_to_date(iso: str | None) -> date | None:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date() if iso else None
    except (ValueError, TypeError):
        return None


def needs_renewal(pos: dict, today: date) -> tuple[bool, str]:
    """Cette position doit-elle voir sa protection reposée aujourd'hui ?

    Fonction PURE (aucun navigateur) : c'est la preuve de DATE, celle qui se
    teste sans BD. La preuve de carnet est faite par `renew_cycle`.

    Retourne (oui/non, raison lisible).
    """
    if pos.get("hold") or not pos.get("qty"):
        return False, "hors gestion bot"
    sl, tp = pos.get("target_low"), pos.get("target_high")
    if not sl or not tp:
        return False, "pas de seuils SL/TP à reposer"

    echeance = _iso_to_date(pos.get("protection_expires_at"))
    if not echeance:
        return False, ("échéance de protection inconnue — jamais vue au carnet, "
                       "repose impossible à justifier")
    if today <= echeance:
        return False, f"protection valide jusqu'au {echeance:%d/%m/%Y}"

    nouvelle = bd_orders.max_validity_deadline(pos["ticker"],
                                               datetime.combine(today, datetime.min.time()))
    if nouvelle <= echeance:
        # Cas réel sur un titre US en cours de mois : « max » rendrait la même
        # fin de mois. Reposer n'allongerait rien et ouvrirait une fenêtre.
        return False, (f"une repose aujourd'hui expirerait le "
                       f"{nouvelle:%d/%m/%Y} — pas plus loin que l'échéance "
                       f"actuelle ({echeance:%d/%m/%Y})")
    return True, (f"protection expirée le {echeance:%d/%m/%Y} — une repose "
                  f"tiendrait jusqu'au {nouvelle:%d/%m/%Y}")


def _has_live_protection(orders: list[dict], pos: dict) -> bool:
    """Un ordre ACTIF portant un seuil existe-t-il pour ce titre ?

    Discriminant identique à celui du contrôle de protection du sync : c'est ce
    qu'on lit à l'œil sur le carnet BD.
    """
    base = pos["ticker"].upper().split(".")[0]
    bdn  = (pos.get("bd_name") or "").upper()
    for o in orders:
        if o.get("statut") != "En cours" or not o.get("seuil"):
            continue
        if (o.get("bd_ticker") or "").upper().split(".")[0] == base:
            return True
        if bdn and (o.get("name") or "").upper() == bdn:
            return True
    return False


def _lecture_carnet() -> dict | None:
    try:
        return playwright_session.run(
            lambda page: reader.get_portfolio(page, send_fn=None), timeout=90)
    except Exception as e:
        print(f"[Renouvellement] lecture portefeuille : {e}")
        return None


def renew_cycle(send_fn, verbose: bool = False, today: date | None = None) -> None:
    """Repose sur BD les protections dont l'échéance est passée.

    Appelé par le cycle horaire (`main._hourly_bd_sync`), donc jusqu'à 13 fois
    par jour de marché : tout ce qui n'a pas lieu d'agir doit sortir en
    silence, sans message ni lecture de page.
    """
    today = today or date.today()
    data = portfolio.load()

    candidats, ecartes = [], []
    for name, pos in (data.get("positions") or {}).items():
        ok, pourquoi = needs_renewal(pos, today)
        (candidats if ok else ecartes).append((name, pos, pourquoi))

    if verbose:
        head = ["🔄 RENOUVELLEMENT DES PROTECTIONS",
                "BD borne toute validité : fin de mois hors Euronext, 31/12 "
                "dessus. Une protection expirée se repose au mois suivant."]
        for n, _p, why in ecartes:
            head.append(f"  ⏳ {n} : {why}")
        if not candidats:
            head.append("\n✅ Rien à reposer.")
        send_fn("\n".join(head))
    if not candidats:
        return

    # ── Preuve de CARNET : deux lectures indépendantes ────────────────────
    # Une seule ne suffit pas — une page à moitié rendue rend « aucun ordre »,
    # et reposer là-dessus créerait un doublon de vente sur des titres déjà
    # engagés (fausse alerte du 11/08/2026).
    bd1 = _lecture_carnet()
    bd2 = _lecture_carnet()
    if not (bd1 and bd1.get("orders_read") and bd2 and bd2.get("orders_read")):
        print("[Renouvellement] carnet non lu (2 tentatives) — aucune repose")
        if verbose:
            send_fn("⚠️ Carnet d'ordres illisible — aucune repose (on ne repose "
                    "jamais sur une absence non prouvée).")
        return
    vus = (bd1.get("orders") or []) + (bd2.get("orders") or [])

    for name, pos, pourquoi in candidats:
        if _has_live_protection(vus, pos):
            # L'ordre est toujours là : soit l'échéance mémorisée est périmée,
            # soit BD l'a prolongé. Dans les deux cas, ne rien reposer.
            print(f"[Renouvellement] {name} : protection encore active au carnet "
                  f"— repose annulée, échéance mémorisée corrigée au prochain sync")
            continue

        qty = abs(int(pos.get("qty") or 0))
        sl, tp = pos["target_low"], pos["target_high"]
        ticker = pos["ticker"]
        send_fn(f"🔄 {name} : {pourquoi}\nRepose de la protection sur BD "
                f"(SL {sl} / TP {tp}, {qty} titres)…")

        od = None
        try:
            od = playwright_session.run(
                lambda page, t=ticker, q=qty, s=sl, p=tp:
                    bd_orders.create_expert_order(page, t, q, s, p, "max"),
                timeout=30)
        except Exception as e:
            print(f"[Renouvellement] {name} create : {e}")
        oid = od and (od.get("id") or od.get("order_id"))
        conf = None
        if oid:
            try:
                conf = playwright_session.run(
                    lambda page, o=oid: bd_orders.confirm_order_auto(page, o, False),
                    timeout=30)
            except Exception as e:
                print(f"[Renouvellement] {name} confirm : {e}")

        if not conf:
            # Rien n'a été annulé pour en arriver là : l'échec laisse la
            # position dans l'état où elle était (à nu), pas dans un état pire.
            if _notified_failure.get(name) == today:
                print(f"[Renouvellement] {name} : nouvel échec (déjà signalé aujourd'hui)")
                continue
            _notified_failure[name] = today
            send_fn(
                f"🚨 {name} : REPOSE DE LA PROTECTION ÉCHOUÉE — la position "
                f"reste SANS STOP sur BD.\n"
                f"Nouvelle tentative au prochain cycle horaire.\n\n"
                f"À faire à la main si ça dure :\n"
                f"/ordre vendre {ticker} {qty} expert {sl} {tp}"
            )
            continue

        adj      = (od.get("_adjusted") or {})
        sl_final = adj.get("stop_loss") or sl
        tp_final = adj.get("take_profit") or tp
        echeance = bd_orders.max_validity_deadline(
            ticker, datetime.combine(today, datetime.min.time()))

        d = portfolio.load()
        if name in d.get("positions", {}):
            p = d["positions"][name]
            p["target_low"]  = sl_final
            p["target_high"] = tp_final
            p["protected"]   = True
            p["protection_ids"] = [c for c in (od.get("children") or []) if c]
            p["protection_expires_at"] = echeance.isoformat()
            portfolio.save(d)
        _notified_failure.pop(name, None)
        send_fn(
            f"🛡️ PROTECTION REPOSÉE — {name}\n"
            f"SL {sl_final} / TP {tp_final} actifs sur BD\n"
            f"Valide jusqu'au {echeance:%d/%m/%Y} (échéance max BD sur ce marché)"
        )
