"""
Entrées « tôt dans la courbe » — règles, et leur suivi en mode FANTÔME.

Demandé le 29/10/2026 : le classement par momentum 12-1 sort par construction
des titres au plus haut (HAE, TEN…), que l'IA refuse ensuite (« résistance
proche », « surchauffe »). Trois façons d'entrer plus tôt sont testées :
repli sur MM20 dans la tendance, cassure d'une base calme, gap post-résultats.

Les MÊMES fonctions servent au backtest (`backtest.py --entries`) et au mode
fantôme : chaque matin, après le rafraîchissement de l'univers mondial, les
meilleurs signaux de chaque règle sont NOTÉS (jamais achetés), avec ceux du
momentum de production comme repère, puis suivis jusqu'au SL, au TP ou au
time-stop. `/fantome` en donne le bilan. Une règle ne nourrit de vrais achats
qu'une fois prouvée sur données réelles (≥ 30 cas, voir `bilan`).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RULES = ("pullback", "breakout", "gap", "recul")
REFERENCE = "momentum"
LABELS = {
    "momentum": "momentum 12-1 (production)",
    "pullback": "repli sur MM20 dans la tendance",
    "breakout": "cassure de base calme sur volume",
    "gap":      "gap post-résultats (proxy PEAD)",
    "recul":    "momentum à 7-15 % sous le plus haut 52 sem.",
}
JOURNAL_PATH = Path(__file__).with_name("early_entries_journal.json")
PAR_REGLE = 4          # signaux notés par règle et par jour
MAX_HOLD_DAYS = 60     # time-stop, en séances (comme le backtest)
TAILLE_REF = 900.0     # taille de ligne de référence pour chiffrer les frais
MIN_CAS = 30           # seuil de la boucle d'apprentissage


# ── Indicateurs ──────────────────────────────────────────────────────────────

def rsi14(closes: pd.Series) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def atr14_pct(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean() / df["Close"] * 100


def add_columns(ind: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Colonnes propres aux règles d'entrée précoce."""
    c = df["Close"]
    ind["ma20"] = c.rolling(20).mean()
    ind["hh252"] = df["High"].rolling(252).max()
    v = df["Volume"] if "Volume" in df.columns else pd.Series(np.nan, index=df.index)
    # Volume du jour rapporté à la moyenne des 50 séances PRÉCÉDENTES.
    ind["vol_ratio"] = v / v.shift(1).rolling(50).mean()
    # Range des 30 séances précédentes (aujourd'hui exclu) : la « base ».
    ind["hh30"] = df["High"].shift(1).rolling(30).max()
    ind["ll30"] = df["Low"].shift(1).rolling(30).min()
    ind["gap_pct"] = (df["Open"] / c.shift(1) - 1) * 100
    return ind


def frame(df: pd.DataFrame) -> pd.DataFrame:
    """Tout ce que `entry_signal` lit, à partir d'un OHLCV quotidien."""
    c = df["Close"]
    ind = pd.DataFrame(index=df.index)
    ind["close"], ind["open"] = c, df["Open"]
    ind["high"], ind["low"] = df["High"], df["Low"]
    ind["rsi"] = rsi14(c)
    ind["mom_1m"] = (c / c.shift(21) - 1) * 100
    ind["mom_12_1"] = (c.shift(21) / c.shift(252) - 1) * 100
    ind["ma200"] = c.rolling(200).mean()
    ind["ma50"] = c.rolling(50).mean()
    ind["atr_pct"] = atr14_pct(df)
    return add_columns(ind, df)


def signal_series(kind: str, ind: pd.DataFrame) -> pd.Series:
    """Score d'entrée d'une règle « tôt dans la courbe » pour CHAQUE séance
    (NaN = le titre ne remplit pas la règle à cette clôture ; entrée à l'open
    suivant). Vectorisé : le backtest l'évalue sur des milliers de titres.

    pullback — tendance établie (12-1 > 0, au-dessus des MM50 et MM200) mais
               cours revenu au contact de la MM20 (entre -1 et +0,5 ATR),
               RSI 35-60. On achète le creux, pas le sommet. Score : 12-1.
    breakout — base calme (range des 30 séances précédentes ≤ 6 ATR), cassure
               du plus haut de la base en clôture sur volume ≥ 1,5× la
               moyenne, sans dépasser +1 ATR au-dessus. Score : volume.
    gap      — proxy de la dérive post-résultats (PEAD) : dans les 5
               dernières séances, ouverture en gap ≥ max(3 %, 1,5 ATR) sur
               volume ≥ 2,5×, séance clôturée au-dessus de son ouverture, et
               cours toujours au-dessus du plus bas de ce jour-là. Score : gap.
    recul    — filtres du momentum de production (12-1 > 0, au-dessus de la
               MM200, RSI 35-65, momentum 1 mois ≥ -12 %), mais cours entre 7
               et 15 % sous son plus haut de 52 semaines. Repérée APRÈS coup
               dans le backtest du 29/10/2026 (+1,29 %/trade brut contre
               +0,24 % à moins de 3 % du plus haut) : hypothèse à prouver en
               fantôme, pas un résultat. Score : 12-1.
    """
    c, atr = ind["close"], ind["atr_pct"]
    base = (atr > 0) & ind["ma200"].notna()
    if kind == "pullback":
        dist = (c / ind["ma20"] - 1) * 100 / atr
        ok = (base & (ind["mom_12_1"] > 0) & (c > ind["ma200"]) & (c > ind["ma50"])
              & ind["rsi"].between(35, 60) & (dist >= -1.0) & (dist <= 0.5))
        return ind["mom_12_1"].clip(upper=80).where(ok)
    if kind == "breakout":
        hh, ll, vr = ind["hh30"], ind["ll30"], ind["vol_ratio"]
        largeur = (hh / ll - 1) * 100
        ok = (base & (c > ind["ma200"]) & (largeur <= 6 * atr) & (vr >= 1.5)
              & (c > hh) & (c <= hh * (1 + atr / 100)))
        return vr.where(ok)
    if kind == "gap":
        seuil = np.maximum(3.0, 1.5 * atr)
        evt = ((ind["gap_pct"] >= seuil) & (ind["vol_ratio"] >= 2.5)
               & (ind["close"] > ind["open"]))
        score = pd.Series(np.nan, index=ind.index)
        # Du plus ancien au plus récent : le gap le plus RÉCENT l'emporte.
        for k in range(4, -1, -1):
            e = evt.shift(k, fill_value=False)
            ok = base & e & (c >= ind["low"].shift(k))
            score = ind["gap_pct"].shift(k).where(ok, score)
        return score
    if kind == "recul":
        dist = (c / ind["hh252"] - 1) * 100
        ok = (base & (ind["mom_12_1"] > 0) & (c > ind["ma200"])
              & ind["rsi"].between(35, 65) & (ind["mom_1m"] >= -12)
              & (dist >= -15) & (dist <= -7))
        return ind["mom_12_1"].clip(upper=80).where(ok)
    raise ValueError(kind)


def latest_signals(ohlcv: pd.DataFrame) -> dict | None:
    """Signaux de la DERNIÈRE séance complète : {"close", "atr_pct", règle: score}.
    Appelé par le rafraîchissement de l'univers (07:30, marchés fermés : la
    dernière barre est une séance terminée)."""
    if len(ohlcv) < 260:
        return None
    ind = frame(ohlcv).iloc[-300:]
    r = ind.iloc[-1]
    if np.isnan(r["close"]) or np.isnan(r["atr_pct"]):
        return None
    out = {"close": round(float(r["close"]), 4), "atr_pct": round(float(r["atr_pct"]), 2)}
    for k in RULES:
        s = signal_series(k, ind).iloc[-1]
        if not np.isnan(s):
            out[k] = round(float(s), 2)
    return out


# ── Mode fantôme ─────────────────────────────────────────────────────────────

def niveaux(close: float, atr_pct: float) -> tuple[float, float]:
    """SL 2 ATR borné 3-10 %, TP ≥ 1,5R et ≥ +10 % — la gestion de production,
    identique pour toutes les règles : seule l'ENTRÉE est comparée."""
    sl_pct = min(max(2 * atr_pct, 3.0), 10.0)
    return (round(close * (1 - sl_pct / 100), 4),
            round(close * (1 + max(10.0, 1.5 * sl_pct) / 100), 4))


def _load() -> list[dict]:
    try:
        return json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    JOURNAL_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=0),
                            encoding="utf-8")


def noter_signaux(indicators: dict, reference: list[str], held: set[str],
                  jour: str | None = None) -> int:
    """Note les meilleurs signaux du jour, règle par règle, plus le top du
    momentum de production (`reference`) comme repère. Idempotent par jour."""
    jour = jour or date.today().isoformat()
    rows = _load()
    deja = {(r["jour"], r["regle"], r["ticker"]) for r in rows}
    held = {h.upper() for h in held}
    choix: list[tuple[str, str]] = []
    for k in RULES:
        cands = [(t, v["early"][k]) for t, v in indicators.items()
                 if (v.get("early") or {}).get(k) is not None and t.upper() not in held]
        cands.sort(key=lambda x: -x[1])
        choix += [(k, t) for t, _ in cands[:PAR_REGLE]]
    choix += [(REFERENCE, t) for t in reference[:PAR_REGLE]
              if (indicators.get(t) or {}).get("early")]
    n = 0
    for k, t in choix:
        if (jour, k, t) in deja:
            continue
        e = indicators[t]["early"]
        sl, tp = niveaux(e["close"], e["atr_pct"])
        rows.append({"jour": jour, "regle": k, "ticker": t, "ref": e["close"],
                     "sl": sl, "tp": tp, "statut": "ouvert"})
        n += 1
    if n:
        _save(rows)
    return n


def suivre(download=None) -> int:
    """Clôt les signaux fantômes dont le SL, le TP ou le time-stop est atteint.
    Entrée simulée à l'open de la séance SUIVANT le signal, +0,3 % (comme le
    backtest) ; SL d'abord si les deux sont touchés dans la même bougie."""
    rows = _load()
    ouverts = [r for r in rows if r["statut"] == "ouvert"]
    if not ouverts:
        return 0
    if download is None:
        import yfinance as yf
        download = yf.download
    debut = min(r["jour"] for r in ouverts)
    tickers = sorted({r["ticker"] for r in ouverts})
    raw = download(tickers, start=debut, group_by="ticker", auto_adjust=True,
                   threads=True, progress=False)
    from config import roundtrip_fee
    clos = 0
    for r in ouverts:
        try:
            df = raw[r["ticker"]] if len(tickers) > 1 else raw
            df = df.dropna(subset=["Close"])
        except Exception:
            continue
        df = df[df.index.strftime("%Y-%m-%d") > r["jour"]]
        if df.empty:
            continue
        entree = float(df["Open"].iloc[0]) * 1.003
        sortie, pourquoi = None, None
        for i, (d, b) in enumerate(df.iterrows()):
            if b["Open"] <= r["sl"]:
                sortie, pourquoi = float(b["Open"]), "SL(gap)"
            elif b["Low"] <= r["sl"]:
                sortie, pourquoi = r["sl"], "SL"
            elif b["High"] >= r["tp"]:
                sortie, pourquoi = r["tp"], "TP"
            elif i + 1 >= MAX_HOLD_DAYS:
                sortie, pourquoi = float(b["Close"]), "TIME"
            if sortie is not None:
                r["sortie_jour"] = d.strftime("%Y-%m-%d")
                break
        r["entree"] = round(entree, 4)
        if sortie is None:
            r["latent_pct"] = round((float(df["Close"].iloc[-1]) / entree - 1) * 100, 2)
            continue
        brut = (sortie / entree - 1) * 100
        frais = roundtrip_fee(r["ticker"], TAILLE_REF) / TAILLE_REF * 100
        r.update(statut="clos", sortie=round(sortie, 4), pourquoi=pourquoi,
                 net_pct=round(brut - frais, 2))
        r.pop("latent_pct", None)
        clos += 1
    _save(rows)
    return clos


def bilan() -> dict:
    """Par règle : cas clos, taux de réussite, gain net moyen et IC 95 %."""
    rows = _load()
    out = {}
    for k in (REFERENCE,) + RULES:
        mine = [r for r in rows if r["regle"] == k]
        nets = [r["net_pct"] for r in mine if r["statut"] == "clos"]
        n = len(nets)
        moy = sum(nets) / n if n else None
        ic = None
        if n >= 2:
            sd = float(np.std(nets, ddof=1))
            demi = 1.96 * sd / math.sqrt(n)
            ic = (round(moy - demi, 2), round(moy + demi, 2))
        out[k] = {"ouverts": len(mine) - n, "clos": n,
                  "reussite": round(sum(x > 0 for x in nets) / n * 100) if n else None,
                  "moyenne": round(moy, 2) if moy is not None else None, "ic95": ic}
    ref = out[REFERENCE]["moyenne"]
    for k in RULES:
        b = out[k]
        b["prouvee"] = bool(b["clos"] >= MIN_CAS and b["ic95"] and b["ic95"][0] > 0
                            and (ref is None or b["moyenne"] > ref))
    return out


def texte_bilan() -> str:
    b = bilan()
    lignes = ["👻 ENTRÉES FANTÔMES — notées, jamais achetées",
              "Même gestion pour toutes (SL 2 ATR, TP ≥ 1,5R) : seule l'entrée change.", ""]
    for k in (REFERENCE,) + RULES:
        x = b[k]
        tete = f"{'📏' if k == REFERENCE else '🧪'} {LABELS[k]}"
        if not x["clos"]:
            lignes.append(f"{tete}\n   {x['ouverts']} en cours, aucun clos")
            continue
        ic = f" (IC95 {x['ic95'][0]:+.1f} / {x['ic95'][1]:+.1f})" if x["ic95"] else ""
        etat = ""
        if k != REFERENCE:
            etat = (" ✅ prouvée" if x["prouvee"] else
                    f" — {x['clos']}/{MIN_CAS} cas" if x["clos"] < MIN_CAS else " — pas mieux")
        lignes.append(f"{tete}{etat}\n   {x['clos']} clos · {x['reussite']} % gagnants · "
                      f"{x['moyenne']:+.2f} % net/trade{ic} · {x['ouverts']} en cours")
    lignes += ["", f"Une règle n'achète pour de vrai qu'une fois prouvée : ≥ {MIN_CAS} cas "
               "clos, IC95 au-dessus de zéro ET meilleure que le momentum."]
    return "\n".join(lignes)
