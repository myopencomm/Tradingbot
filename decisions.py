"""
Boucle d'apprentissage — apprendre de CHAQUE décision, pas seulement des trades.

Demandé le 13/10/2026 : « que le bot apprenne et devienne meilleur à chaque
trade ». Avec ~5 trades par mois, ajuster la stratégie trade par trade, c'est
apprendre le hasard : les règles inventées après une série de pertes (verrou
de gain, sorties sur MM50…) ont toutes perdu au backtest. On élargit donc
l'échantillon et on durcit la preuve :

  1. JOURNAL — chaque candidat jugé par validate_candidate (scan, briefing,
     contrôle pré-achat) est noté : verdict, motif, conviction, avis Jev,
     indicateurs, cours. ~150 décisions par mois au lieu de ~5 trades.
  2. MESURE — 10 et 20 séances plus tard, rendement du titre ET rendement en
     excès de son indice (S&P 500 ou Euro Stoxx 50) : un ACHAT qui monte avec
     le marché n'a rien prouvé.
  3. PREUVE — `bilan()` compare les groupes (ACHAT vs EXCLUS, conviction,
     motifs de rejet, tranches d'indicateurs). Une piste n'est signalée que si
     l'écart tient sur assez de cas (MIN_CAS) avec un intervalle de confiance
     qui exclut zéro. Le bot PROPOSE ; rien ne change sans backtest et sans
     validation de l'utilisateur.
  4. CRITÈRE D'ARRÊT — fixé à froid : à EVAL_DEADLINE avec au moins
     EVAL_MIN_TRADES trades clôturés depuis EVAL_START, le bot doit battre le
     même capital placé dans le S&P 500. Sinon, passage à un ETF recommandé.

Données locales (decisions_journal.json, gitignoré) — jamais publiées.
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path

import pytz

PARIS = pytz.timezone("Europe/Paris")
PATH = Path(__file__).parent / "decisions_journal.json"
HORIZONS = (10, 20)
MIN_CAS = int(os.getenv("LEARN_MIN_CAS", "30"))
EVAL_START = os.getenv("EVAL_START", "2026-10-13")
EVAL_DEADLINE = os.getenv("EVAL_DEADLINE", "2027-04-13")
EVAL_MIN_TRADES = int(os.getenv("EVAL_MIN_TRADES", "50"))


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)


def indice_de(ticker: str) -> str:
    """Indice de référence du titre, pour le rendement en excès."""
    return "^GSPC" if "." not in ticker else "^STOXX50E"


def categorie_motif(reason: str) -> str:
    r = (reason or "").lower()
    if not r:
        return "achat"
    if "contre-avis jev" in r:
        return "veto Jev"
    if "recherche web fiable indisponible" in r:
        return "sans recherche"
    if "profil historique" in r:
        return "veto profil"
    if "objectif moyen" in r:
        return "au-dessus de l'objectif analystes"
    if "information insuffisante" in r:
        return "information insuffisante"
    if "analyse insuffisante" in r:
        return "thèse refusée par la porte"
    if any(k in r for k in ("rsi", "mm200", "volatil", "sl requis", "ratio risque")):
        return "veto quantitatif"
    if "cours indisponible" in r:
        return "sans cours"
    return "défaut cité par l'IA"


def enregistrer(res: dict, mode: str, date: str | None = None) -> None:
    """Note une décision de validate_candidate. Une ligne par (jour, titre,
    mode) : la dernière décision du jour l'emporte."""
    if not res or not res.get("price"):
        return
    date = date or datetime.now(PARIS).strftime("%Y-%m-%d")
    t = res.get("ticker")
    tech = res.get("tech") or {}
    jev = res.get("jev") or {}
    ligne = {
        "date": date, "ticker": t, "mode": mode,
        "verdict": res.get("verdict"), "motif": categorie_motif(
            "" if res.get("verdict") == "ACHAT" else res.get("reason", "")),
        "raison": (res.get("reason") or "")[:120],
        "conviction": res.get("conviction"),
        "jev_ancrage": jev.get("ancrage"), "jev_specifique": jev.get("specifique"),
        "cours": res["price"], "indice": indice_de(t),
        "rsi": tech.get("rsi"), "mom_1m": tech.get("momentum_1m"),
        "mom_12_1": tech.get("mom_12_1"), "atr_pct": tech.get("atr_pct"),
        "ma200_dist": tech.get("ma200_dist_pct"),
    }
    rows = [r for r in _load()
            if not (r["date"] == date and r["ticker"] == t and r["mode"] == mode)]
    rows.append(ligne)
    _save(rows)


# ── Mesure ───────────────────────────────────────────────────────────────────

def _serie(ticker: str, cache: dict):
    import yfinance as yf
    import prices
    if ticker not in cache:
        try:
            h = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
            div = prices.price_divisor(ticker) if not ticker.startswith("^") else 1.0
            cache[ticker] = h / div
        except Exception:
            cache[ticker] = None
    return cache[ticker]


def _fwd(serie, date: str, seances: int, base: float | None = None) -> float | None:
    if serie is None or not len(serie):
        return None
    avant = serie[serie.index.strftime("%Y-%m-%d") <= date]
    apres = serie[serie.index.strftime("%Y-%m-%d") > date]
    if len(apres) < seances:
        return None
    depart = base if base else (float(avant.iloc[-1]) if len(avant) else None)
    if not depart:
        return None
    return (float(apres.iloc[seances - 1]) / depart - 1) * 100


def mesurer() -> list[dict]:
    """Lignes du journal enrichies de r10/r20 (rendement) et x10/x20 (excès
    sur l'indice), quand l'horizon est atteint."""
    cache: dict = {}
    out = []
    for r in _load():
        r = dict(r)
        s, si = _serie(r["ticker"], cache), _serie(r["indice"], cache)
        for h in HORIZONS:
            ret = _fwd(s, r["date"], h, base=r["cours"])
            idx = _fwd(si, r["date"], h)
            r[f"r{h}"] = round(ret, 2) if ret is not None else None
            r[f"x{h}"] = round(ret - idx, 2) if ret is not None and idx is not None else None
        out.append(r)
    return out


# ── Preuve ───────────────────────────────────────────────────────────────────

def _stats(vals: list[float]) -> dict | None:
    if not vals:
        return None
    n = len(vals)
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "moy": m, "ic": 1.96 * sd / math.sqrt(n) if n > 1 else float("inf"),
            "pos": sum(v > 0 for v in vals) / n * 100}


def _ecart(a: list[float], b: list[float]) -> dict | None:
    """Différence de moyennes a − b et son IC95 % (Welch, approximation)."""
    sa, sb = _stats(a), _stats(b)
    if not sa or not sb or sa["n"] < 2 or sb["n"] < 2:
        return None
    se = math.sqrt((sa["ic"] / 1.96) ** 2 + (sb["ic"] / 1.96) ** 2)
    d = sa["moy"] - sb["moy"]
    return {"diff": d, "ic": 1.96 * se, "na": sa["n"], "nb": sb["n"],
            "prouve": sa["n"] >= MIN_CAS and sb["n"] >= MIN_CAS and abs(d) > 1.96 * se}


def _tranches(rows: list[dict], champ: str, cle: str) -> tuple | None:
    vals = sorted(r[champ] for r in rows if r.get(champ) is not None and r.get(cle) is not None)
    if len(vals) < 9:
        return None
    b1, b2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    bas = [r[cle] for r in rows if r.get(champ) is not None and r.get(cle) is not None and r[champ] < b1]
    haut = [r[cle] for r in rows if r.get(champ) is not None and r.get(cle) is not None and r[champ] >= b2]
    return b1, b2, bas, haut


def bilan(rows: list[dict] | None = None, cle: str = "x20") -> dict:
    """Comparaisons utiles, sur le rendement en excès à 20 séances."""
    rows = rows if rows is not None else mesurer()
    # Une ligne par (date, titre) pour les comparaisons de verdict : le
    # contrôle pré-achat redoublerait le scan du même jour.
    uniques = {}
    # Les re-validations de positions détenues (mode « review », reanalyse.py)
    # ne sont pas des décisions d'achat : elles fausseraient la comparaison.
    rows = [r for r in rows if r.get("mode") != "review"]
    for r in rows:
        k = (r["date"], r["ticker"])
        if k not in uniques or r["mode"] == "standard":
            uniques[k] = r
    u = [r for r in uniques.values() if r.get(cle) is not None]
    achats = [r[cle] for r in u if r["verdict"] == "ACHAT"]
    exclus = [r[cle] for r in u if r["verdict"] != "ACHAT"]
    out = {"n_mesures": len(u), "n_journal": len(rows),
           "achat": _stats(achats), "exclus": _stats(exclus),
           "achat_vs_exclus": _ecart(achats, exclus), "motifs": {}, "tranches": {},
           "conviction": None}
    for m in sorted({r["motif"] for r in u if r["verdict"] != "ACHAT"}):
        out["motifs"][m] = _stats([r[cle] for r in u if r["motif"] == m])
    c5 = [r[cle] for r in u if r["verdict"] == "ACHAT" and (r.get("conviction") or 0) >= 5]
    c4 = [r[cle] for r in u if r["verdict"] == "ACHAT" and r.get("conviction") == 4]
    out["conviction"] = _ecart(c5, c4)
    for champ, nom in (("rsi", "RSI"), ("mom_12_1", "momentum 12 mois"),
                       ("mom_1m", "momentum 1 mois"), ("atr_pct", "volatilité (ATR)")):
        t = _tranches(u, champ, cle)
        if t:
            b1, b2, bas, haut = t
            out["tranches"][nom] = {"seuils": (b1, b2), "ecart": _ecart(haut, bas)}
    return out


def _fmt(s: dict | None) -> str:
    if not s:
        return "pas encore de cas mesuré"
    return f"{s['moy']:+.1f}% ± {s['ic']:.1f} ({s['n']} cas, {s['pos']:.0f}% positifs)"


def critere_arret() -> str:
    """Le point 4 : où en est le bot face au S&P 500 depuis EVAL_START."""
    import benchmark
    import history
    import portfolio
    import position_view
    try:
        closed = [t for t in history.closed_trades()
                  if (t.get("opened_at") or "")[:10] >= EVAL_START]
    except Exception:
        closed = []
    vues = [v for v in position_view.views(portfolio.get_managed_positions())
            if (v.get("opened_at") or "")[:10] >= EVAL_START]
    cmp = benchmark.compare(closed, vues) if (closed or vues) else None
    aujourd = datetime.now(PARIS).strftime("%Y-%m-%d")
    lignes = [f"🎯 CRITÈRE FIXÉ LE {EVAL_START[8:10]}/{EVAL_START[5:7]}/{EVAL_START[:4]} : au "
              f"{EVAL_DEADLINE[8:10]}/{EVAL_DEADLINE[5:7]}/{EVAL_DEADLINE[:4]}, avec au moins "
              f"{EVAL_MIN_TRADES} trades clôturés, le bot doit battre le même argent placé "
              f"dans le S&P 500. Sinon : passage à un ETF recommandé.",
              f"Trades clôturés depuis : {len(closed)}/{EVAL_MIN_TRADES}"]
    if cmp:
        lignes.append(f"Bot {cmp['bot_eur']:+.0f} € vs S&P 500 {cmp['spx_eur']:+.0f} € "
                      f"→ écart {cmp['ecart_eur']:+.0f} € ({cmp['battus']}/{cmp['n']} trades "
                      f"devant l'indice)")
    if aujourd >= EVAL_DEADLINE:
        if len(closed) < EVAL_MIN_TRADES:
            lignes.append("⏳ Échéance atteinte mais trop peu de trades pour conclure — "
                          "le bilan se fera dès le seuil atteint.")
        elif cmp and cmp["ecart_eur"] > 0:
            lignes.append("✅ VERDICT : le bot bat le S&P 500 — l'expérience peut continuer.")
        else:
            lignes.append("🛑 VERDICT : le bot ne bat pas le S&P 500 — passage à un ETF "
                          "recommandé (critère fixé à froid le 13/10/2026).")
    return "\n".join(lignes)


def rapport() -> str:
    b = bilan()
    lignes = ["🧠 APPRENTISSAGE — ce que mesurent les décisions du bot",
              f"{b['n_journal']} décisions notées, {b['n_mesures']} arrivées à 20 séances",
              "(rendement en EXCÈS de l'indice : S&P 500 pour les US, Euro Stoxx 50 sinon)",
              "",
              f"ACHAT  : {_fmt(b['achat'])}",
              f"EXCLUS : {_fmt(b['exclus'])}"]
    pistes = []
    e = b["achat_vs_exclus"]
    if e:
        lignes.append(f"→ écart ACHAT − EXCLUS : {e['diff']:+.1f} pts ± {e['ic']:.1f}")
        if e["prouve"]:
            pistes.append("le filtre IA " + ("AJOUTE de la valeur" if e["diff"] > 0
                                             else "FAIT PIRE que ses rejets — à revoir"))
    if b["motifs"]:
        lignes.append("\nPar motif de rejet :")
        for m, s in b["motifs"].items():
            lignes.append(f"  {m} : {_fmt(s)}")
            if s and s["n"] >= MIN_CAS and s["moy"] - s["ic"] > 0:
                pistes.append(f"les rejets « {m} » battent l'indice : ce motif rejette "
                              f"peut-être de bonnes actions")
    c = b["conviction"]
    if c:
        lignes.append(f"\nConviction 5 vs 4 : {c['diff']:+.1f} pts ± {c['ic']:.1f} "
                      f"({c['na']} vs {c['nb']} cas)")
        if c["prouve"]:
            pistes.append("la conviction 5 " + ("prédit mieux" if c["diff"] > 0
                                                else "ne prédit PAS mieux") + " que la 4")
    if b["tranches"]:
        lignes.append("\nTiers haut − tiers bas, par indicateur :")
        for nom, t in b["tranches"].items():
            ec = t["ecart"]
            if ec:
                lignes.append(f"  {nom} : {ec['diff']:+.1f} pts ± {ec['ic']:.1f}")
                if ec["prouve"]:
                    pistes.append(f"{nom} élevé " + ("aide" if ec["diff"] > 0 else "nuit")
                                  + " — à tester en backtest avant tout changement")
    lignes.append("")
    if pistes:
        lignes.append("💡 PISTES PROUVÉES (≥ " f"{MIN_CAS} cas, IC95 % hors de zéro) — à "
                      "valider en backtest, puis par toi :")
        lignes += [f"  • {p}" for p in pistes]
    else:
        lignes.append(f"Aucune piste prouvée pour l'instant (il faut ≥ {MIN_CAS} cas par "
                      f"groupe et un écart hors du bruit). Rien n'est changé.")
    lignes += ["", critere_arret()]
    return "\n".join(lignes)
