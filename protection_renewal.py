"""
Repose des protections perdues — par échéance BD, ou disparues en cours de route.

DEUX FAÇONS DE PERDRE UN STOP, UNE SEULE REPOSE
-----------------------------------------------
  · l'ÉCHÉANCE, prévisible et datée (ci-dessous) ;
  · la DISPARITION en cours de route, imprévisible : BD annule les ordres à
    seuil sur événement du titre. JNJ a perdu la sienne autour de son
    détachement de dividende du 25/08/2026, SIX JOURS avant son échéance — et
    le bot, qui n'attendait le trou que le 31, n'a fait que le signaler.
    Le deuxième déclencheur ne cherche pas la cause : il constate la durée.

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

DEUX PREUVES, TOUJOURS
----------------------
Une lecture partielle du carnet rend une position protégée comme « sans
protection » (fausse alerte du 11/08/2026, trois positions d'un coup), et
reposer un Expert de VENTE là-dessus créerait un DOUBLON DE VENTE sur des
titres déjà engagés. Rien ne se repose donc sur une seule preuve :

  1. une preuve de PERSISTANCE, locale — soit l'échéance mémorisée est
     dépassée et une repose donnerait une date plus lointaine, soit le sync
     voit la position à nu SANS INTERRUPTION depuis plus de
     `NAKED_CONFIRM_MINUTES` (`naked_since`, posé par `sync_engine`). Une
     protection revue entre-temps efface le marqueur : un trou qui dure est un
     trou réel, un trou qui clignote est une lecture douteuse.
  2. une preuve de CARNET — deux lectures abouties faites ici même, aucune ne
     montrant d'ordre actif à seuil sur ce titre.

Sans échéance mémorisée NI trou persistant, on ne repose PAS : on retombe sur
l'alerte de `sync_engine`.
"""
from datetime import date, datetime, timedelta

import bourse_direct_orders as bd_orders
import bourse_direct_reader as reader
import playwright_session
import portfolio

# Durée pendant laquelle un trou doit TENIR avant qu'on le répare. Le sync
# tourne toutes les heures : un trou vu à deux cycles consécutifs n'est plus une
# lecture ratée, c'est un fait. Assez court pour reposer dans la séance, assez
# long pour qu'une page à moitié rendue ne déclenche jamais rien.
NAKED_CONFIRM_MINUTES = 45

# Une repose échouée par position et par jour : BD peut refuser l'ordre (marché
# fermé, pas de cotation, quantité engagée) et le cycle horaire repasse toutes
# les heures. Sans ce garde-fou, un refus persistant = 13 messages par jour.
_notified_failure: dict[str, date] = {}


def _iso_to_date(iso: str | None) -> date | None:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date() if iso else None
    except (ValueError, TypeError):
        return None


def _iso_to_dt(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (ValueError, TypeError):
        return None


def needs_renewal(pos: dict, now: datetime) -> tuple[bool, str]:
    """Cette position doit-elle voir sa protection reposée maintenant ?

    Fonction PURE (aucun navigateur) : c'est la preuve de PERSISTANCE, celle
    qui se teste sans BD. La preuve de carnet est faite par `renew_cycle`.

    Deux déclencheurs, l'un prévisible et l'autre pas :
      · l'échéance BD est passée, et reposer aujourd'hui donnerait une date
        plus lointaine (bascule du mois) ;
      · le sync voit la position à nu depuis plus de NAKED_CONFIRM_MINUTES
        sans interruption — la protection a disparu avant l'heure, peu importe
        pourquoi.

    Retourne (oui/non, raison lisible).
    """
    if pos.get("hold") or not pos.get("qty"):
        return False, "hors gestion bot"
    sl, tp = pos.get("target_low"), pos.get("target_high")
    if not sl or not tp:
        return False, "pas de seuils SL/TP à reposer"

    today = now.date()

    # ── Déclencheur 1 : échéance BD dépassée ─────────────────────────────
    echeance = _iso_to_date(pos.get("protection_expires_at"))
    if echeance and today > echeance:
        nouvelle = bd_orders.max_validity_deadline(pos["ticker"], now)
        if nouvelle > echeance:
            return True, (f"protection expirée le {echeance:%d/%m/%Y} — une "
                          f"repose tiendrait jusqu'au {nouvelle:%d/%m/%Y}")
        # Cas réel sur un titre US en cours de mois : « max » rendrait la même
        # fin de mois. Reposer n'allongerait rien — sauf si la position est
        # RÉELLEMENT à nu, ce que tranche le déclencheur suivant.

    # ── Déclencheur 2 : trou constaté qui dure ───────────────────────────
    depuis = _iso_to_dt(pos.get("naked_since"))
    if depuis:
        age_min = int((now - depuis).total_seconds() // 60)
        if age_min >= NAKED_CONFIRM_MINUTES:
            return True, (f"aucune protection au carnet depuis "
                          f"{depuis:%d/%m %H:%M} ({age_min} min, confirmé sur "
                          f"plusieurs cycles de sync)")
        return False, (f"vue sans protection il y a {age_min} min — "
                       f"confirmation attendue à {NAKED_CONFIRM_MINUTES} min "
                       f"(une lecture ratée ne doit jamais déclencher de repose)")

    if echeance:
        return False, f"protection valide jusqu'au {echeance:%d/%m/%Y}"
    return False, ("échéance inconnue et aucun trou constaté — rien qui "
                   "justifie une repose")


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


def renew_cycle(send_fn, verbose: bool = False, now: datetime | None = None) -> None:
    """Repose sur BD les protections perdues — échéance atteinte ou trou qui dure.

    Appelé par le cycle horaire (`main._hourly_bd_sync`), donc jusqu'à 13 fois
    par jour de marché : tout ce qui n'a pas lieu d'agir doit sortir en
    silence, sans message ni lecture de page.
    """
    now = now or datetime.now()
    today = now.date()
    data = portfolio.load()

    candidats, ecartes = [], []
    for name, pos in (data.get("positions") or {}).items():
        ok, pourquoi = needs_renewal(pos, now)
        (candidats if ok else ecartes).append((name, pos, pourquoi))

    if verbose:
        head = ["🔄 REPOSE DES PROTECTIONS",
                "Deux cas : échéance BD atteinte (fin de mois hors Euronext, "
                "31/12 dessus), ou protection disparue avant l'heure et "
                f"absente du carnet depuis plus de {NAKED_CONFIRM_MINUTES} min."]
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
        # On TENTE même marché fermé : BD accepte souvent un ordre hors séance,
        # et la protection est alors active dès l'ouverture — ce qui vaut mieux
        # que d'attendre 15h35 pour un titre US. Mais un refus dans ce cas n'est
        # pas une panne : il ne doit pas déclencher l'alerte rouge.
        import market
        marche_ouvert = market.is_open_now(ticker)
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
            if not marche_ouvert:
                print(f"[Renouvellement] {name} : refus BD hors séance — "
                      f"nouvelle tentative à l'ouverture")
                if verbose:
                    send_fn(f"⏳ {name} : marché fermé, BD a refusé la repose. "
                            f"Nouvelle tentative à l'ouverture.")
                continue
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
        echeance = bd_orders.max_validity_deadline(ticker, now)

        d = portfolio.load()
        if name in d.get("positions", {}):
            p = d["positions"][name]
            p["target_low"]  = sl_final
            p["target_high"] = tp_final
            p["protected"]   = True
            p["protection_ids"] = [c for c in (od.get("children") or []) if c]
            p["protection_expires_at"] = echeance.isoformat()
            p.pop("naked_since", None)
            portfolio.save(d)
        _notified_failure.pop(name, None)
        send_fn(
            f"🛡️ PROTECTION REPOSÉE — {name}\n"
            f"SL {sl_final} / TP {tp_final} actifs sur BD\n"
            f"Valide jusqu'au {echeance:%d/%m/%Y} (échéance max BD sur ce marché)"
        )
