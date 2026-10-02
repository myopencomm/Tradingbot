"""
Verdicts du briefing — enregistrés, puis MESURÉS.

Le briefing juge chaque position chaque matin (✅ maintenir, 👀 surveiller,
⚠️ alléger, 🔴 vendre). Le 02/10/2026, l'utilisateur demandait si ces avis
devaient changer la stratégie (resserrer un stop, alléger). On ne peut pas les
backtester : impossible de rejouer ce que l'IA aurait dit en 2024. Et les
sorties anticipées sur signal de PRIX ont toutes perdu au backtest du 23/09.

On mesure donc en avant : chaque verdict est noté avec le cours du jour, puis
comparé au cours 10 et 20 séances plus tard. Si les ⚠️/🔴 font nettement pire
que les ✅, la preuve est là pour automatiser une réaction ; sinon, on aura
évité de payer pour du bruit. `/verdicts` affiche le bilan.

Données locales (verdicts_history.json, gitignoré) : tickers et cours du
portefeuille, jamais publiés.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytz

PARIS = pytz.timezone("Europe/Paris")
PATH = Path(__file__).parent / "verdicts_history.json"
ICONES = ("✅", "👀", "⚠️", "🔴")
HORIZONS = (10, 20)          # séances


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)


def icone_de(ligne: str) -> str | None:
    for ic in ICONES:
        if ligne.startswith(ic):
            return ic
    return None


def enregistrer(lignes: list[str], positions: dict, date: str | None = None) -> int:
    """Note les verdicts du jour (un par position, le dernier du jour l'emporte).
    `lignes` = sortie de analysis.briefing_lines, « <icône> NOM — texte »."""
    import prices
    date = date or datetime.now(PARIS).strftime("%Y-%m-%d")
    rows = [r for r in _load() if r.get("date") != date]
    n = 0
    for l in lignes:
        ic = icone_de(l)
        if not ic:
            continue
        nom = l[len(ic):].strip().split(" — ")[0].strip()
        cfg = positions.get(nom)
        if not cfg:
            continue
        q = prices.get_quote(cfg["ticker"])
        if not q.get("price"):
            continue
        rows.append({"date": date, "nom": nom, "ticker": cfg["ticker"],
                     "verdict": ic, "cours": q["price"],
                     "texte": l.split(" — ", 1)[1] if " — " in l else ""})
        n += 1
    _save(rows)
    return n


def _rendement(ticker: str, date: str, cours: float, seances: int,
               cache: dict) -> float | None:
    """Rendement (%) entre le cours noté et la clôture `seances` séances plus
    tard ; None si l'horizon n'est pas encore atteint."""
    import yfinance as yf
    import prices
    if ticker not in cache:
        try:
            h = yf.Ticker(ticker).history(period="6mo")["Close"].dropna()
            cache[ticker] = h / prices.price_divisor(ticker)
        except Exception:
            cache[ticker] = None
    h = cache[ticker]
    if h is None or not len(h):
        return None
    apres = h[h.index.strftime("%Y-%m-%d") > date]
    if len(apres) < seances:
        return None
    return round((float(apres.iloc[seances - 1]) / cours - 1) * 100, 2)


def bilan() -> dict:
    """{icône: {horizon: [rendements]}} sur les verdicts arrivés à échéance."""
    out = {ic: {h: [] for h in HORIZONS} for ic in ICONES}
    cache: dict = {}
    for r in _load():
        for h in HORIZONS:
            v = _rendement(r["ticker"], r["date"], r["cours"], h, cache)
            if v is not None and r["verdict"] in out:
                out[r["verdict"]][h].append(v)
    return out


def rapport() -> str:
    rows = _load()
    if not rows:
        return ("Aucun verdict enregistré pour l'instant — le premier sera noté "
                "au prochain briefing (9h05).")
    b = bilan()
    debut = min(r["date"] for r in rows)
    lignes = [f"📏 VERDICTS DU BRIEFING — mesurés depuis le {debut[8:10]}/{debut[5:7]}",
              f"{len(rows)} verdicts notés", ""]
    for ic in ICONES:
        parts = []
        for h in HORIZONS:
            vals = b[ic][h]
            if vals:
                moy = sum(vals) / len(vals)
                neg = sum(v < 0 for v in vals) / len(vals) * 100
                parts.append(f"{h} séances : {moy:+.1f}% en moyenne "
                             f"({len(vals)} cas, {neg:.0f}% négatifs)")
        n_total = sum(1 for r in rows if r["verdict"] == ic)
        if n_total:
            lignes.append(f"{ic} {n_total} verdict(s)")
            lignes += [f"   {p}" for p in parts] or ["   horizon pas encore atteint"]
    lignes += ["",
               "Lecture : si ⚠️/🔴 font nettement PIRE que ✅ sur 20 séances, avec "
               "assez de cas (≥ 15), les verdicts prédisent quelque chose et "
               "méritent une réaction automatique. Sinon, ce sont des opinions."]
    return "\n".join(lignes)


def plan_action(ligne: str, cfg: dict, prix: float | None) -> str:
    """Ce qu'on peut FAIRE d'un verdict, sous la ligne du briefing : distance
    au stop et à l'objectif, et la commande de sortie propre pour ⚠️/🔴.
    Chaîne vide pour ✅ ou sans cours."""
    ic = icone_de(ligne)
    if ic not in ("👀", "⚠️", "🔴") or not prix:
        return ""
    sl, tp = cfg.get("target_low"), cfg.get("target_high")
    morceaux = []
    if sl:
        morceaux.append(f"SL à {(sl / prix - 1) * 100:+.1f}%")
    if tp:
        morceaux.append(f"TP à {(tp / prix - 1) * 100:+.1f}%")
    txt = "   " + " · ".join(morceaux) if morceaux else ""
    if ic in ("⚠️", "🔴"):
        nom = ligne[len(ic):].strip().split(" — ")[0].strip()
        txt += f"\n   → sortir proprement : /sortir {nom}"
    return txt
