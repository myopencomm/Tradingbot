import contextlib
import json
import csv
import io
import os
import threading
from config import POSITIONS_PATH

# ─── Un seul écrivain à la fois ─────────────────────────────────────────────
# `positions.json` porte TOUT l'état du bot (cash, positions, SL/TP, ordres
# autonomes en attente) et il est lu-modifié-écrit depuis une dizaine de
# threads : scheduler, polling Telegram, worker Playwright, serveur HTTP du
# dashboard, `threading.Timer` du sync post-ordre.
#
# Deux défauts en découlaient, tous deux silencieux :
#   · MISE À JOUR PERDUE — `sync()` charge l'état, passe ~30 s à lire Bourse
#     Direct, puis sauvegarde : un `/sl` passé entre-temps était écrasé sans
#     un mot. Le correctif du 11/08 (relecture de confirmation) allongeait
#     encore cette fenêtre.
#   · FICHIER TRONQUÉ — `write_text` écrit en place ; le process tué au
#     mauvais moment laissait un JSON incomplet, donc un portefeuille vide au
#     redémarrage (`load()` retombe sur son défaut sans rien dire).
#
# `_LOCK` est réentrant : `mutate()` appelle `load()`/`save()`, et plusieurs
# helpers de ce module s'appellent entre eux.
_LOCK = threading.RLock()


def load() -> dict:
    with _LOCK:
        try:
            return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"cash_available": 0, "positions": {}, "pending_orders": {}}


def save(data: dict):
    """Écriture ATOMIQUE : fichier temporaire + `os.replace`.

    `os.replace` est atomique sur le même système de fichiers — le lecteur voit
    l'ancien fichier OU le nouveau, jamais un fichier à moitié écrit.
    """
    with _LOCK:
        tmp = POSITIONS_PATH.with_suffix(POSITIONS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, POSITIONS_PATH)


@contextlib.contextmanager
def mutate():
    """Lecture-modification-écriture sous verrou, en une seule opération.

        with portfolio.mutate() as data:
            data["positions"][k]["target_low"] = 209.7

    À utiliser partout où l'on modifie l'état. Le `load()` + `save()` séparés
    restent disponibles (lecture seule, ou modification bâtie sur une donnée
    longue à obtenir), mais ils rouvrent la fenêtre de mise à jour perdue :
    plus la fenêtre est longue, plus il faut préférer ce gestionnaire.

    Aucune sauvegarde si le bloc lève : un état à moitié modifié ne part pas
    sur le disque.
    """
    with _LOCK:
        data = load()
        yield data
        save(data)


def get_positions() -> dict:
    return load().get("positions", {})


def get_managed_positions() -> dict:
    """Positions gérées par le bot — exclut les HOLD long terme (`hold: true`),
    qui restent visibles mais hors alertes SL/TP, hors P&L trading et hors
    propositions de vente/swap."""
    return {k: v for k, v in get_positions().items() if not v.get("hold")}


def set_hold(name: str, on: bool, note: str = "") -> bool:
    """Marque/démarque une position HOLD long terme (hors gestion bot)."""
    data = load()
    pos = data.get("positions", {}).get(name.upper())
    if not pos:
        return False
    if on:
        pos["hold"] = True
        if note:
            pos["hold_note"] = note
    else:
        pos.pop("hold", None)
        pos.pop("hold_note", None)
    save(data)
    return True


def get_cash() -> float:
    return load().get("cash_available", 0)


def market_close_expiry(ticker: str):
    """Clôture du marché DU TITRE aujourd'hui — source unique : market.py."""
    import market
    return market.close_time_today(ticker)


def get_pending_opportunities() -> list:
    """Retourne les opportunités validées non expirées (issues du briefing/scan)."""
    from datetime import datetime
    opps = load().get("pending_opportunities", [])
    now  = datetime.now().isoformat()
    return [o for o in opps if o.get("expires_at", "") > now]


def add_pending_opportunity(ticker: str, entry: float, sl: float, tp: float,
                             reason: str = "", source: str = "briefing",
                             context: dict | None = None):
    """Stocke une opportunité validée pour que le moteur autonome puisse
    l'exploiter. `context` (thèse, régime, indicateurs) est mémorisé pour la
    boucle d'apprentissage — voir set_entry_context / stats.record_close."""
    if context:
        ctx = dict(context)
        ctx.setdefault("source", source)
        ctx.setdefault("entry", round(entry, 4))
        ctx.setdefault("tp_pct", round((tp - entry) / entry * 100, 1) if entry else None)
        set_entry_context(ticker, ctx)
    import pytz
    from datetime import datetime
    PARIS = pytz.timezone("Europe/Paris")
    now     = datetime.now(PARIS)
    expires = market_close_expiry(ticker)
    data = load()
    opps = data.get("pending_opportunities", [])
    opps = [o for o in opps if o.get("ticker") != ticker]  # déduplique
    opps.append({
        "ticker":       ticker,
        "entry":        round(entry, 4),
        "sl":           round(sl, 4),
        "tp":           round(tp, 4),
        "reason":       reason[:150],
        "source":       source,
        "validated_at": now.isoformat(),
        "expires_at":   expires.isoformat(),
    })
    data["pending_opportunities"] = sorted(opps, key=lambda x: x["validated_at"], reverse=True)[:5]
    save(data)


def get_auto_pending_orders() -> dict:
    """Ordres d'achat AUTONOMES placés sur BD mais pas encore exécutés.
    Comptent dans le budget engagé (fonds réservés par BD)."""
    return load().get("auto_pending_orders", {})


def best_price(cfg: dict, quote: dict | None = None) -> dict:
    """Meilleur cours disponible pour une position DÉTENUE, avec sa provenance.

    Retourne {price, currency, source, as_of, stale, note}. `price` à None si
    aucune source. `source` : 'yf' | 'bd' | 'yf_stale' — c'est de la plomberie,
    utile au diagnostic, jamais à montrer à l'utilisateur.

    Ordre : yfinance frais → relevé Bourse Direct → yfinance périmé (faute de
    mieux). BD passe AVANT un yfinance périmé parce que le sync horaire le
    rafraîchit et que c'est le cours du courtier chez qui la position est
    réellement détenue — celui qui déclenchera le SL.

    Existe parce que yfinance a servi, le 04/08/2026, des cours vieux de deux à
    trois séances en les présentant comme courants : NVDA à 200.75 quand BD
    cotait 206.64, AIR à 208.00 quand BD cotait 211.40. Le P&L affiché, les
    alertes SL/TP et le trailing raisonnaient tous sur ces cours morts.

    `stale` et `note` répondent à la SEULE question qui intéresse le lecteur :
    « ce chiffre est-il à jour ? ». Ils ne parlent donc pas de la source qui a
    échoué. Le bot annonçait « cours Bourse Direct du 17/08 22:35 — yfinance
    périmé (14/08) » sur un cours qui était la dernière clôture, donc
    parfaitement bon : de la plomberie prise pour une alerte (18/08/2026).
    """
    import prices as _prices
    q = quote if quote is not None else _prices.get_quote(cfg.get("ticker", ""))
    price, stale = q.get("price"), q.get("stale")

    def _fini(price, currency, source, as_of):
        """Habille un cours retenu : son âge décide de ce qu'on en dit."""
        jour = (as_of or "")[:10]
        try:
            from datetime import date
            age = _prices._sessions_since(date.fromisoformat(jour)) if jour else None
        except ValueError:
            age = None
        # Un cours SANS date ne peut pas être déclaré à jour. Le dire, plutôt
        # que de le présenter comme frais : c'est exactement l'erreur que ce
        # module existe pour empêcher.
        if age is None:
            return {"price": price, "currency": currency, "source": source,
                    "as_of": as_of, "stale": True,
                    "note": "cours sans date connue — impossible de garantir "
                            "qu'il est à jour"}
        # Une séance d'écart, c'est la dernière clôture : normal avant
        # l'ouverture, normal le week-end. Au-delà, le titre n'a pas coté et
        # le lecteur doit le savoir.
        perime = age >= 2
        note = ""
        if perime:
            note = (f"cours du {jour} — pas de cotation depuis "
                    f"{age} séances, ce chiffre peut avoir bougé")
        return {"price": price, "currency": currency, "source": source,
                "as_of": as_of, "stale": perime, "note": note}

    if price is not None and not stale:
        return _fini(price, q.get("currency", "EUR"), "yf", q.get("as_of"))

    bd_price = cfg.get("bd_price")
    # Un relevé BD plus VIEUX que la barre yfinance ne vaut pas mieux (session
    # Playwright déconnectée depuis plusieurs jours) : dans ce cas on garde
    # yfinance, périmé mais moins.
    bd_at = (cfg.get("bd_price_at") or "")[:10]
    if bd_price and bd_at and q.get("as_of") and bd_at < q["as_of"]:
        bd_price = None
    if bd_price:
        return _fini(bd_price,
                     cfg.get("bd_price_currency") or q.get("currency", "EUR"),
                     "bd", cfg.get("bd_price_at"))

    if price is not None:
        return _fini(price, q.get("currency", "EUR"), "yf_stale", q.get("as_of"))

    return {"price": None, "currency": q.get("currency", "EUR"),
            "source": "", "as_of": None, "stale": False, "note": ""}


def quote_problem(cfg: dict, quote: dict) -> tuple[str, str]:
    """Pourquoi cette position n'a pas de cours — et à quel point c'est grave.

    Retourne (code, phrase courte). Codes : `ticker` | `suspended` | `unavailable`.

    Le discriminant est le relevé de Bourse Direct mémorisé par le sync : **si
    BD cote le titre, il n'est pas suspendu**. Yahoo qui ne répond pas sur un
    titre que le courtier valorise ne veut dire qu'une chose — le ticker stocké
    est faux. Annoncer « COURS SUSPENDU — non vendable » dans ce cas est une
    fausse alerte doublée d'un vrai angle mort : la position n'est plus suivie
    (ni SL, ni TP, ni trailing) alors que rien ne le laisse deviner.
    Cas fondateur : NVDA enregistré en `NVDA.PA` par le sync, 03/08/2026.
    """
    bd_price = cfg.get("bd_price")
    if bd_price:
        sym = "$" if (cfg.get("bd_price_currency") or "EUR") == "USD" else "€"
        return "ticker", (f"TICKER YAHOO INTROUVABLE « {cfg.get('ticker')} » — "
                          f"BD cote pourtant {sym}{bd_price}. Position NON SUIVIE "
                          f"(ni SL, ni TP) tant que le ticker n'est pas corrigé.")
    if cfg.get("worthless") or quote.get("status") == "suspended":
        return "suspended", "COURS SUSPENDU — non vendable"
    return "unavailable", "prix indisponible"


def add_auto_pending_order(ticker: str, qty: int, entry: float, sl: float, tp: float,
                           order_id: str = None, expires_at: str = None,
                           protection_ids: list | None = None):
    """`order_id` (BD) et `expires_at` permettent l'ANNULATION AUTO d'un ordre
    d'entrée resté non exécuté à la clôture : un ordre limite qui traîne ne se
    remplit que quand le momentum s'est retourné contre nous (cas AF.PA 07/2026,
    ordre valide 31/12 rempli à la cassure baissière → SL)."""
    import pytz
    from datetime import datetime
    data = load()
    rec = {
        "qty": qty, "entry": entry, "sl": sl, "tp": tp,
        "placed_at": datetime.now(pytz.timezone("Europe/Paris")).isoformat(),
    }
    if order_id:
        rec["order_id"] = str(order_id)
    if expires_at:
        rec["expires_at"] = expires_at
    # Ids des jambes SL/TP renvoyés par /order/create : seule source, et seule
    # façon de pouvoir les annuler plus tard pour remonter le stop.
    if protection_ids:
        rec["protection_ids"] = list(protection_ids)
    data.setdefault("auto_pending_orders", {})[ticker.upper()] = rec
    save(data)


def clear_auto_pending_order(ticker: str):
    data = load()
    if data.get("auto_pending_orders", {}).pop(ticker.upper(), None) is not None:
        save(data)


def clear_pending_opportunity(ticker: str):
    data = load()
    opps = data.get("pending_opportunities", [])
    data["pending_opportunities"] = [o for o in opps if o.get("ticker") != ticker]
    save(data)


def get_autonomous_config() -> dict:
    return load().get("autonomous_config", {})


def get_autonomous_positions() -> dict:
    return {k: v for k, v in load().get("positions", {}).items() if v.get("autonomous")}


def set_autonomous_config(cfg: dict):
    data = load()
    data["autonomous_config"] = cfg
    save(data)


def now_iso() -> str:
    """Instant présent, heure de Paris, au format ISO — l'horodatage du bot."""
    import pytz
    from datetime import datetime
    return datetime.now(pytz.timezone("Europe/Paris")).isoformat(timespec="seconds")


def new_position(ticker: str, qty: int, entry_price: float, sl: float, tp: float,
                 **extra) -> dict:
    """La forme d'une position qui vient d'être ouverte — UN seul endroit.

    Les positions naissaient à quatre endroits (ici, le sync qui les découvre
    sur BD, /add, l'import). Chacun écrivait son propre dictionnaire, donc
    ajouter un champ demandait de penser aux quatre — c'est ainsi que la DATE
    D'ENTRÉE a manqué à l'appel jusqu'au 13/08/2026, et sans elle la durée de
    détention n'est pas calculable : on connaissait la date de sortie de chaque
    trade et jamais celle d'entrée.
    """
    return {
        "ticker": ticker,
        "qty": qty,
        "entry_price": round(entry_price, 4),
        "target_high": round(tp, 4),
        "target_low": round(sl, 4),
        # Le point de départ du KPI « combien de temps pour faire ce gain ».
        "opened_at": now_iso(),
        **extra,
    }


def add_position(name: str, ticker: str, qty: int, entry_price: float, sl: float, tp: float):
    with mutate() as data:
        data.setdefault("positions", {})[name.upper()] = new_position(
            ticker, qty, entry_price, sl, tp)


def remove_position(name: str):
    data = load()
    data.get("positions", {}).pop(name.upper(), None)
    data.get("pending_orders", {}).pop(name.upper(), None)
    save(data)


# ── Contexte d'entrée (brique 1 de la boucle d'apprentissage) ────────────────
# À chaque décision d'achat on mémorise le POURQUOI (thèse, régime, indicateurs,
# source). Sans ça, le trade clôturé n'est qu'un chiffre et le bot ne peut pas
# relire ses décisions. Clé = symbole de base (ex "AC" pour AC.PA), pour survivre
# aux variations de suffixe. Consommé + effacé par stats.record_close().

def _base_sym(ticker: str) -> str:
    return (ticker or "").upper().split(".")[0]


def set_entry_context(ticker: str, ctx: dict):
    from datetime import datetime
    import pytz
    data = load()
    ctx = dict(ctx)
    ctx.setdefault("captured_at", datetime.now(pytz.timezone("Europe/Paris")).isoformat())
    data.setdefault("entry_contexts", {})[_base_sym(ticker)] = ctx
    save(data)


def get_entry_context(ticker: str) -> dict:
    return load().get("entry_contexts", {}).get(_base_sym(ticker), {})


def clear_entry_context(ticker: str):
    data = load()
    if data.get("entry_contexts", {}).pop(_base_sym(ticker), None) is not None:
        save(data)


def add_pending_order(name: str, ticker: str, qty: int, entry_price: float,
                      sl: float, tp: float) -> float:
    """Enregistre un ordre en attente et réserve le cash. Retourne le cash réservé."""
    from datetime import datetime
    data = load()
    reserved = round(entry_price * qty, 2)
    data.setdefault("pending_orders", {})[name.upper()] = {
        "ticker":        ticker,
        "qty":           qty,
        "entry_price":   round(entry_price, 4),
        "target_low":    round(sl, 4),
        "target_high":   round(tp, 4),
        "reserved_cash": reserved,
        "created_at":    datetime.now().strftime("%Y-%m-%d"),
    }
    data["cash_available"] = round(data.get("cash_available", 0) - reserved, 2)
    save(data)
    return reserved


def cancel_pending_order(name: str) -> float:
    """Annule un ordre en attente et libère le cash réservé. Retourne le cash libéré."""
    data = load()
    order = data.get("pending_orders", {}).pop(name.upper(), None)
    if order:
        released = order.get("reserved_cash", 0)
        data["cash_available"] = round(data.get("cash_available", 0) + released, 2)
        save(data)
        return released
    return 0.0


def get_pending_orders() -> dict:
    return load().get("pending_orders", {})


def update_cash(amount: float):
    data = load()
    data["cash_available"] = round(amount, 2)
    save(data)


def update_sl(name: str, price: float):
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["target_low"] = round(price, 4)
        save(data)
        return True
    return False


def mark_gmail_triggered(name: str, strategy: str):
    """Marque qu'une notif Gmail 'Déclenchement' a été envoyée — évite le doublon."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["gmail_triggered"] = strategy
        save(data)


def clear_gmail_triggered(name: str):
    data = load()
    pos = data.get("positions", {}).get(name.upper(), {})
    pos.pop("gmail_triggered", None)
    save(data)


def is_gmail_triggered(name: str) -> bool:
    return bool(load().get("positions", {}).get(name.upper(), {}).get("gmail_triggered"))


def mark_sl_breach(name: str):
    """Marque qu'une alerte SL dépassé a déjà été envoyée — empêche le spam."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["sl_breach_notified"] = True
        save(data)


def mark_tp_breach(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte TP atteint — une seule notif par franchissement."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["tp_breach_notified"] = notified
        save(data)


def mark_sl_proche(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte SL proche — évite le spam à chaque check."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["sl_proche_notified"] = notified
        save(data)


def mark_breakeven(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte trailing stop breakeven — une notif par épisode."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["breakeven_notified"] = notified
        save(data)


def update_tp(name: str, price: float):
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["target_high"] = round(price, 4)
        save(data)
        return True
    return False


def import_from_csv(csv_content: str) -> list:
    """
    Parse a Bourse Direct portfolio CSV export.
    Bourse Direct uses semicolon separator and French headers.
    Returns list of dicts: {name, qty, pru}
    """
    positions = []
    # Try semicolon first, then comma
    for delimiter in (";", ","):
        try:
            reader = csv.DictReader(io.StringIO(csv_content), delimiter=delimiter)
            rows = list(reader)
            if not rows:
                continue
            for row in rows:
                # Normalize header variants from Bourse Direct exports
                name = (
                    row.get("Libellé") or row.get("Valeur") or
                    row.get("Titre") or row.get("Instrument", "")
                ).strip()
                raw_qty = row.get("Quantité") or row.get("Qté") or row.get("Nb titres", "0")
                raw_pru = row.get("PRU") or row.get("Prix de revient unitaire", "0")
                try:
                    qty = int(str(raw_qty).replace(" ", "").replace("\xa0", "").replace(",", ""))
                    pru = float(str(raw_pru).replace(",", ".").replace(" ", "").replace("\xa0", ""))
                except ValueError:
                    continue
                if name and qty > 0 and pru > 0:
                    positions.append({"name": name, "qty": qty, "pru": pru})
            if positions:
                break
        except Exception:
            continue
    return positions
