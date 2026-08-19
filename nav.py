"""
Valeur liquidative par part — la performance du bot, sans ses mouvements d'argent.

Le P&L en euros ne dit pas si le bot est bon : verser 1 000 € fait grimper le
total sans qu'aucune décision n'ait été prise. Les fonds règlent ça depuis
toujours avec des PARTS.

  · au lancement, la part vaut 100 € et le capital achète des parts ;
  · un versement ACHÈTE des parts au cours du jour, un retrait en REND —
    le nombre de parts bouge, la valeur de la part non ;
  · seule la performance fait bouger la valeur de la part.

« Ma part valait 100, elle vaut 117,52 » répond donc exactement à « combien mon
investissement a-t-il grossi », que l'on ait versé de l'argent en route ou pas.

PÉRIMÈTRE : le fonds, c'est ce que le bot PILOTE — le cash plus les positions
gérées. Les HOLD long terme en sont exclus (choix du 19/08/2026) : la plus
grosse d'entre elles pèse à elle seule plus que tout le reste du périmètre et
porte une lourde moins-value latente issue d'une décision ANTÉRIEURE au bot.
L'inclure noierait la performance du bot dans un pari qu'il n'a jamais pris.

DEUX RÉGIMES, jamais mélangés en silence :
  · « reconstitué » — du 1er trade à la mise en service de ce module. Bâti sur
    les seuls trades clôturés : juste aux dates de sortie, interpolé entre, et
    aveugle aux plus-values latentes de l'époque.
  · « mesuré » — relevé quotidien de la valeur réelle. Exact.
Chaque point porte sa provenance, et le graphique les distingue.
"""
import json
import os
import threading
from datetime import datetime

import pytz

from config import BASE_DIR

PARIS = pytz.timezone("Europe/Paris")
NAV_PATH = BASE_DIR / "nav_history.json"
BASE = 100.0          # valeur d'une part au lancement

# Au-delà de cet écart en un jour, sans trade clôturé pour l'expliquer, un
# mouvement d'espèces non déclaré est plus probable qu'une performance réelle.
# On le SIGNALE plutôt que de laisser la courbe mentir.
SAUT_SUSPECT_PCT = 15.0

_LOCK = threading.RLock()


def _load() -> dict:
    with _LOCK:
        try:
            return json.loads(NAV_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"base": BASE, "points": [], "flux": []}


def _save(data: dict):
    with _LOCK:
        tmp = NAV_PATH.with_suffix(NAV_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, NAV_PATH)


def perimetre() -> dict:
    """Ce que le bot pilote, ici et maintenant : cash + positions gérées."""
    import portfolio
    import position_view
    import prices

    d = portfolio.load()
    valeur = latent = 0.0
    lignes = []
    for v in position_view.views(d.get("positions", {})):
        if v["hold"]:
            continue
        montant = v.get("bd_value_eur")
        if not montant:
            montant = (v["price"] or 0) * v["qty"] * prices.fx_to_eur(v["currency"])
        valeur += montant
        latent += v["pnl_eur"] or 0
        lignes.append(v["name"])
    cash = d.get("cash_available", 0) or 0
    return {"cash": round(cash, 2), "positions": round(valeur, 2),
            "total": round(cash + valeur, 2), "latent": round(latent, 2),
            "lignes": lignes}


def capital_initial() -> float:
    """Capital de départ, déduit à rebours : valeur d'aujourd'hui moins tout ce
    que le bot a produit (réalisé + latent). Sans versement ni retrait — et il
    n'y en a pas eu — c'est exactement la mise de départ."""
    import history
    p = perimetre()
    realise = sum(t.get("pnl_eur", t.get("pnl", 0)) for t in history.closed_trades())
    return round(p["total"] - realise - p["latent"], 2)


def reconstituer() -> list[dict]:
    """Courbe du 1er trade à aujourd'hui, bâtie sur les trades clôturés.

    APPROXIMATION ASSUMÉE : la valeur ne bouge qu'aux dates de sortie. Les
    plus-values latentes de l'époque sont invisibles — une position qui montait
    sans être vendue n'apparaît pas. C'est pour ça que chaque point est marqué
    « reconstitué » et que le graphique le montre autrement.
    """
    import history
    trades = sorted(history.closed_trades(), key=lambda t: t.get("date", ""))
    if not trades:
        return []
    v0 = capital_initial()
    if v0 <= 0:
        return []
    parts = v0 / BASE
    points, cumul = [], 0.0
    debut = trades[0].get("opened_at", "")[:10] or trades[0].get("date", "")
    points.append({"date": debut, "valeur": round(v0, 2), "parts": round(parts, 6),
                   "part": BASE, "source": "reconstitué"})
    for t in trades:
        cumul += t.get("pnl_eur", t.get("pnl", 0))
        valeur = v0 + cumul
        points.append({"date": t.get("date", ""), "valeur": round(valeur, 2),
                       "parts": round(parts, 6), "part": round(valeur / parts, 2),
                       "source": "reconstitué"})
    return points


def declarer_flux(montant: float, note: str = "") -> dict:
    """Déclare un versement (+) ou un retrait (−) sur le périmètre du bot.

    C'est le SEUL geste qui change le nombre de parts. Sans lui, un versement
    apparaîtrait comme une performance — exactement ce que la valeur de part
    existe pour empêcher.
    """
    data = _load()
    data.setdefault("flux", []).append({
        "date": datetime.now(PARIS).strftime("%Y-%m-%d"),
        "montant": round(float(montant), 2),
        "note": note,
        "applique": False,
    })
    _save(data)
    return data["flux"][-1]


def relever(send_fn=None) -> dict:
    """Relève la valeur réelle du jour et met la série à jour. Exact.

    Retourne le point enregistré. Un versement déclaré depuis le dernier relevé
    est converti en parts AVANT de calculer la nouvelle valeur de part.
    """
    data = _load()
    p = perimetre()
    jour = datetime.now(PARIS).strftime("%Y-%m-%d")

    mesures = [x for x in data["points"] if x.get("source") == "mesuré"]
    if mesures:
        parts = mesures[-1]["parts"]
        part_avant = mesures[-1]["part"]
    else:
        # Premier relevé : on raccorde à la courbe reconstituée pour que la
        # bascule estimé → mesuré ne crée pas une marche artificielle.
        recon = reconstituer()
        parts = recon[-1]["parts"] if recon else (capital_initial() or p["total"]) / BASE
        part_avant = recon[-1]["part"] if recon else BASE

    # Versements/retraits déclarés et pas encore convertis en parts
    for f in data.get("flux", []):
        if not f.get("applique"):
            parts += f["montant"] / (part_avant or BASE)
            f["applique"] = True

    part = round(p["total"] / parts, 2) if parts else BASE
    point = {"date": jour, "valeur": p["total"], "parts": round(parts, 6),
             "part": part, "source": "mesuré"}

    # Un saut brutal sans trade pour l'expliquer = argent entré ou sorti sans
    # avoir été déclaré. On le dit : une courbe fausse ne se voit pas.
    if mesures and part_avant:
        ecart = (part - part_avant) / part_avant * 100
        if abs(ecart) >= SAUT_SUSPECT_PCT and send_fn:
            send_fn(
                f"⚠️ Valeur de part : {ecart:+.1f} % en un relevé.\n"
                f"Si tu as versé ou retiré de l'argent, déclare-le "
                f"(sinon la courbe comptera ce mouvement comme une performance) :\n"
                f"/nav depot 1000   ou   /nav retrait 500"
            )

    data["points"] = [x for x in data["points"]
                      if not (x["date"] == jour and x["source"] == "mesuré")]
    data["points"].append(point)
    data["points"].sort(key=lambda x: (x["date"], x["source"] == "mesuré"))
    _save(data)
    return point


def serie() -> list[dict]:
    """La courbe complète : reconstituée jusqu'au premier relevé, mesurée après."""
    data = _load()
    mesures = [x for x in data["points"] if x.get("source") == "mesuré"]
    debut_mesure = mesures[0]["date"] if mesures else None
    recon = [x for x in reconstituer()
             if not debut_mesure or x["date"] < debut_mesure]
    return recon + mesures


def resume() -> dict:
    """De quoi remplir une tuile : valeur de part, performance, provenance."""
    s = serie()
    if not s:
        return {"part": BASE, "perf": 0.0, "points": 0, "depuis": None,
                "mesure_depuis": None}
    mesures = [x for x in s if x["source"] == "mesuré"]
    return {
        "part": s[-1]["part"],
        "perf": round((s[-1]["part"] / BASE - 1) * 100, 2),
        "valeur": s[-1]["valeur"],
        "points": len(s),
        "depuis": s[0]["date"],
        "mesure_depuis": mesures[0]["date"] if mesures else None,
    }
