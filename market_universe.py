"""
Découverte automatique de l'univers investissable — fin de la liste écrite à la main.

POURQUOI : `SCAN_UNIVERSE` est une liste manuelle (149 valeurs, dont seulement
36 US). Le marché US compte ~6000 actions ordinaires : en scanner 36 revient à
regarder 0.6% du gisement, et le choix de ces 36 n'obéit à aucun critère —
c'était un garde-fou anti-hallucination (commit 5a7dc2f), pas une stratégie de
couverture.

SOURCE DE VÉRITÉ — pas de devinette, pas de mémoire de modèle :
Nasdaq Trader publie quotidiennement la liste OFFICIELLE de toutes les valeurs
cotées aux États-Unis, en accès libre et sans clé API :
  - nasdaqlisted.txt : valeurs cotées au NASDAQ
  - otherlisted.txt  : NYSE, NYSE American, Cboe, etc.
Soit ~13 000 lignes, ETF et warrants compris.

PIPELINE EN DEUX ÉTAGES (le second est le seul coûteux) :
  1. `fetch_us_symbols()` — liste officielle, filtrée des ETF, valeurs de test,
     preferred/warrants/units. Quelques secondes.
  2. `build_liquid_universe()` — téléchargement GROUPÉ (yfinance accepte des
     centaines de tickers par appel : ~55 ms/ticker mesuré, contre ~1 s en
     appel unitaire) puis filtre de LIQUIDITÉ. Sans ce filtre, l'univers se
     remplit de micro-caps dont le spread coûte plus cher que les frais de
     courtage. Attention : cet univers est US, donc au tarif étranger — 17€
     l'aller-retour (et non 4€ comme sur Euronext), seuil de rentabilité 5×.

Le résultat est mis en cache sur disque : le scan lit le cache, il ne relance
jamais ce pipeline lui-même.
"""
import io
import re
import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).parent / "universe_cache.json"
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Liquidité minimale : volume médian quotidien en devise de cotation, sur 3
# mois. En dessous, le spread rend la position non rentable une fois les frais
# BD pris en compte (constaté le 29/07/2026 sur l'extension Euronext : 23
# candidats écartés entre 0.01 et 1.97 M€/jour).
MIN_DOLLAR_VOLUME = 5_000_000
# Prix plancher : sous ce seuil, le pas de cotation et le spread relatif
# deviennent dominants (penny stocks).
MIN_PRICE = 5.0


def _parse_pipe_file(text: str) -> list[dict]:
    rows = [l for l in text.splitlines() if l and not l.startswith("File Creation")]
    return list(csv.DictReader(io.StringIO("\n".join(rows)), delimiter="|"))


# Mots entiers, JAMAIS de sous-chaîne : filtrer sur "etf" excluait Netflix
# ("N-etf-lix"), et "right" exclurait Wright. Bug réel du 29/07/2026, corrigé
# ici — c'est exactement le genre d'erreur silencieuse qui ampute l'univers
# sans rien signaler.
_NOT_STOCK = re.compile(
    r"\b(warrants?|units?|preferred|depositary|notes?|rights?|index|"
    r"acquisition\s+corp)\b", re.I
)


def _is_common_stock(symbol: str, name: str) -> bool:
    """Écarte ce qui n'est pas une action ordinaire (le momentum n'a pas de
    sens sur un warrant, une unit de SPAC ou une preferred).

    Les ETF sont déjà exclus par la colonne `ETF` du fichier officiel : on ne
    tente donc AUCUNE détection d'ETF par le nom.
    """
    if not symbol or not symbol.isalpha():
        # $ . - et autres suffixes = warrants, units, preferred, classes
        return False
    if len(symbol) > 5:
        return False
    return not _NOT_STOCK.search(name or "")


def fetch_us_symbols() -> list[str]:
    """Liste officielle des actions ordinaires cotées aux US (source Nasdaq
    Trader). Aucune invention : ce sont les fichiers publiés par la place."""
    out: set[str] = set()

    try:
        r = requests.get(NASDAQ_LISTED, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for row in _parse_pipe_file(r.text):
            if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
                continue
            if row.get("Financial Status") not in (None, "", "N"):
                continue  # déficient / en sursis de radiation
            sym, name = (row.get("Symbol") or "").strip(), row.get("Security Name")
            if _is_common_stock(sym, name):
                out.add(sym)
    except Exception as e:
        print(f"[universe] nasdaqlisted : {e}")

    try:
        r = requests.get(OTHER_LISTED, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for row in _parse_pipe_file(r.text):
            if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
                continue
            sym = (row.get("NASDAQ Symbol") or row.get("ACT Symbol") or "").strip()
            if _is_common_stock(sym, row.get("Security Name")):
                out.add(sym)
    except Exception as e:
        print(f"[universe] otherlisted : {e}")

    return sorted(out)


def build_liquid_universe(symbols: list[str], batch: int = 250,
                          min_dollar_volume: float = MIN_DOLLAR_VOLUME,
                          min_price: float = MIN_PRICE,
                          progress=None) -> list[dict]:
    """
    Filtre de liquidité par téléchargement GROUPÉ. Retourne une liste de
    {ticker, price, dollar_volume} triée par liquidité décroissante.

    Le groupage est ce qui rend l'opération possible : ~55 ms/ticker mesuré,
    soit ~3 min pour 3000 valeurs, contre plusieurs heures en appels unitaires.
    """
    import yfinance as yf
    import pandas as pd

    kept: list[dict] = []
    total = len(symbols)
    for i in range(0, total, batch):
        chunk = symbols[i:i + batch]
        try:
            df = yf.download(chunk, period="3mo", interval="1d",
                             auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[universe] batch {i}: {e}")
            continue
        if df is None or df.empty:
            continue
        try:
            closes = df["Close"] if "Close" in df else None
            vols   = df["Volume"] if "Volume" in df else None
            if closes is None or vols is None:
                continue
            if isinstance(closes, pd.Series):  # un seul ticker
                closes, vols = closes.to_frame(chunk[0]), vols.to_frame(chunk[0])
            for t in closes.columns:
                c, v = closes[t].dropna(), vols[t].dropna()
                if len(c) < 40:
                    continue          # historique trop court (IPO récente)
                px = float(c.iloc[-1])
                if px < min_price:
                    continue
                dv = float((c * v).median())
                if dv >= min_dollar_volume:
                    kept.append({"ticker": t, "price": round(px, 2),
                                 "dollar_volume": round(dv)})
        except Exception as e:
            print(f"[universe] parse batch {i}: {e}")
        if progress:
            progress(min(i + batch, total), total, len(kept))
    return sorted(kept, key=lambda x: -x["dollar_volume"])


def compute_indicators_bulk(tickers: list[str], batch: int = 200,
                            progress=None) -> dict[str, dict]:
    """
    Calcule les MÊMES indicateurs que `prices.get_technicals`, mais par
    téléchargement GROUPÉ — seule façon de couvrir des milliers de valeurs.

    En unitaire, `get_technicals` fait un appel d'un an d'historique PAR
    ticker : à 2500 valeurs c'est intenable et yfinance rate-limite (constaté
    le 29/07/2026). Ici un seul appel couvre 200 tickers.

    Clés retournées identiques à get_technicals pour que `_quant_screen` ne
    voie aucune différence : rsi, momentum_1m, mom_12_1, above_ma200,
    ma200_dist_pct, atr_pct, vol_ratio, vol_ratio_20_250.
    """
    import yfinance as yf
    import pandas as pd
    import numpy as np

    out: dict[str, dict] = {}
    total = len(tickers)
    for i in range(0, total, batch):
        chunk = tickers[i:i + batch]
        try:
            df = yf.download(chunk, period="2y", interval="1d",
                             auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[universe] indicateurs batch {i}: {e}")
            continue
        if df is None or df.empty:
            continue
        try:
            def field(name):
                d = df[name] if name in df else None
                if d is None:
                    return None
                return d.to_frame(chunk[0]) if isinstance(d, pd.Series) else d

            closes, highs, lows, vols = (field("Close"), field("High"),
                                         field("Low"), field("Volume"))
            if closes is None:
                continue
            for t in closes.columns:
                try:
                    c = closes[t].dropna()
                    if len(c) < 210:      # besoin de la MM200 + marge
                        continue
                    # RSI 14 (Wilder simplifié, comme prices.rsi)
                    delta = c.diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = (-delta.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss.replace(0, np.nan)
                    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

                    ma200 = float(c.rolling(200).mean().iloc[-1])
                    px    = float(c.iloc[-1])
                    mom1m = float((px / c.iloc[-22] - 1) * 100) if len(c) > 22 else None
                    # Momentum 12 mois HORS dernier mois — ancrage sur une
                    # fenêtre CALENDAIRE de 365 jours, comme
                    # prices.get_technicals qui fait (iloc[-22] / iloc[0]) sur
                    # period="1y". Un décalage fixe de 252 barres serait FAUX :
                    # Euronext cote ~256 séances/an, donc l'ancre glisserait de
                    # plusieurs jours (mesuré le 29/07/2026 : GLE 44.8 au lieu
                    # de 53.6, AIR 15.0 au lieu de 10.4). mom_12_1 étant LE
                    # signal de classement, l'écart fausserait tout le top 8.
                    m121 = None
                    win = c[c.index >= (c.index[-1] - pd.Timedelta(days=365))]
                    if len(win) > 22:
                        m121 = float((win.iloc[-22] / win.iloc[0] - 1) * 100)

                    atr_pct = None
                    if highs is not None and lows is not None and t in highs.columns:
                        h, l = highs[t].dropna(), lows[t].dropna()
                        idx = c.index.intersection(h.index).intersection(l.index)
                        if len(idx) > 15:
                            cc, hh, ll = c.loc[idx], h.loc[idx], l.loc[idx]
                            prev = cc.shift()
                            tr = pd.concat([hh - ll, (hh - prev).abs(),
                                            (ll - prev).abs()], axis=1).max(axis=1)
                            atr_pct = float(tr.rolling(14).mean().iloc[-1] / px * 100)

                    vol_ratio = None
                    if vols is not None and t in vols.columns:
                        v = vols[t].dropna()
                        # DERNIER JOUR COMPLET, comme prices.get_technicals :
                        # la barre du jour est partielle (volume à 0.08× la
                        # moyenne à midi). Lue telle quelle, elle faisait
                        # écarter par le veto volume 3 634 valeurs sur 3 737
                        # (01/10/2026) — le scan mondial n'en gardait que 18.
                        if len(v) > 21:
                            avg20 = float(v.iloc[-21:-1].mean())
                            if avg20:
                                vol_ratio = round(float(v.iloc[-2]) / avg20, 2)

                    ret = c.pct_change().dropna()
                    vr20_250 = None
                    if len(ret) > 250:
                        s20  = float(ret.tail(20).std())
                        s250 = float(ret.tail(250).std())
                        if s250:
                            vr20_250 = round(s20 / s250, 2)

                    out[t] = {
                        "rsi": round(rsi, 1),
                        "momentum_1m": round(mom1m, 1) if mom1m is not None else None,
                        "mom_12_1": round(m121, 1) if m121 is not None else None,
                        "above_ma200": bool(px > ma200),
                        "ma200_dist_pct": round((px / ma200 - 1) * 100, 1),
                        "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
                        "vol_ratio": vol_ratio,
                        "vol_ratio_20_250": vr20_250,
                    }
                except Exception:
                    continue
        except Exception as e:
            print(f"[universe] parse indicateurs {i}: {e}")
        if progress:
            progress(min(i + batch, total), total, len(out))
    return out


def save_cache(entries: list[dict], source: str = "us") -> None:
    data = {}
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
        except Exception:
            data = {}
    data[source] = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "count": len(entries),
        "entries": entries,
    }
    CACHE_PATH.write_text(json.dumps(data, indent=1))


def load_cache(source: str = "us", max_age_days: int = 7) -> list[dict]:
    """Univers en cache. Liste vide si absent ou périmé — l'appelant retombe
    alors sur SCAN_UNIVERSE : jamais de scan à l'aveugle."""
    try:
        data = json.loads(CACHE_PATH.read_text()).get(source) or {}
        upd = datetime.fromisoformat(data["updated"])
        if datetime.now() - upd > timedelta(days=max_age_days):
            print(f"[universe] cache '{source}' périmé ({upd.date()})")
            return []
        return data.get("entries", [])
    except Exception:
        return []


def refresh_us(top_n: int = 0, send_fn=None) -> dict:
    """
    Rafraîchit l'univers US complet : liste officielle → liquidité →
    indicateurs, le tout mis en cache. À PLANIFIER (hebdomadaire), jamais à la
    demande : yfinance rate-limite après un passage complet.

    `top_n` > 0 limite aux N valeurs les plus liquides (le reste du gisement
    apporte peu pour des positions de ~500-1000€ et multiplie les refus de
    traitabilité côté BD).
    """
    def log(m):
        print(f"[universe] {m}")
        if send_fn:
            send_fn(m)

    t0 = time.time()
    syms = fetch_us_symbols()
    log(f"{len(syms)} actions ordinaires US listées")
    liq = build_liquid_universe(syms)
    log(f"{len(liq)} passent le filtre de liquidité "
        f"(≥ {MIN_DOLLAR_VOLUME/1e6:.0f} M$/j, prix ≥ {MIN_PRICE}$)")
    if top_n and len(liq) > top_n:
        liq = liq[:top_n]
        log(f"limité aux {top_n} plus liquides")
    save_cache(liq, "us")

    tickers = [e["ticker"] for e in liq]
    ind = compute_indicators_bulk(tickers)
    save_indicators(ind, "us")
    log(f"indicateurs calculés pour {len(ind)} valeurs en {(time.time()-t0)/60:.1f} min")
    return {"symbols": len(syms), "liquid": len(liq), "indicators": len(ind)}


def save_indicators(ind: dict[str, dict], source: str = "us") -> None:
    data = {}
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
        except Exception:
            data = {}
    data[f"{source}_indicators"] = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "count": len(ind),
        "data": ind,
    }
    CACHE_PATH.write_text(json.dumps(data, indent=1))


def load_indicators(source: str = "us", max_age_days: int = 3) -> dict[str, dict]:
    """
    Indicateurs en cache. Vide si périmé — l'appelant retombe alors sur la
    liste manuelle plutôt que de scanner avec des données mortes.

    3 jours : le screen travaille sur des bougies quotidiennes (RSI, momentum,
    MM200), une donnée de la veille est acceptable ; au-delà d'un week-end
    prolongé elle ne l'est plus.
    """
    try:
        d = json.loads(CACHE_PATH.read_text()).get(f"{source}_indicators") or {}
        upd = datetime.fromisoformat(d["updated"])
        if datetime.now() - upd > timedelta(days=max_age_days):
            print(f"[universe] indicateurs '{source}' périmés ({upd.date()})")
            return {}
        return d.get("data", {})
    except Exception:
        return {}




# ═══ Univers MONDIAL (01/10/2026) ═══════════════════════════════════════════
# L'univers réellement scanné se réduisait à 149 valeurs choisies à la main :
# le gisement US expirait (indicateurs valables 3 jours, rafraîchis le
# dimanche seulement) et rien d'autre n'existait. Une valeur comme Höegh
# Autoliners (+89 % sur un an) ne pouvait donc JAMAIS remonter.
#
# Source : le screener Yahoo Finance, interrogé bourse par bourse — uniquement
# les places que Bourse Direct traite EN LIGNE (Oslo, Stockholm, Milan… sont
# « sur demande », à des frais dissuasifs). Le filtre de bourse écarte les
# doubles cotations (ASML à Zurich, titres US à Milan, Francfort .F) et l'OTC.

WORLD_EXCHANGES = {   # code Yahoo → suffixe Yahoo des tickers
    "PAR": ".PA", "AMS": ".AS", "BRU": ".BR", "LIS": ".LS",
    "GER": ".DE", "LSE": ".L", "MCE": ".MC", "EBS": ".SW",
    "NYQ": "", "NMS": "", "NGM": "", "NCM": "", "ASE": "",
}
import os as _os
WORLD_MIN_MCAP       = float(_os.getenv("WORLD_MIN_MCAP", "300000000"))
WORLD_MIN_TRADED_EUR = float(_os.getenv("WORLD_MIN_TRADED_EUR", "2000000"))
# Au-delà, la « performance » est presque toujours une erreur de donnée
# (cotation en pence prise pour des livres, regroupement d'actions…).
WORLD_MAX_52W_PCT    = 1500.0


def _fx_eur(currency: str) -> float:
    cur = (currency or "EUR")
    if cur in ("GBp", "GBX"):
        return _fx_eur("GBP") / 100.0
    try:
        import prices
        return prices.fx_to_eur(cur.upper())
    except Exception:
        return 1.0


def fetch_world_symbols(log=print) -> list[dict]:
    """Actions liquides de toutes les bourses accessibles en ligne chez BD."""
    import yfinance as yf
    from yfinance import EquityQuery as Q
    fx_cache: dict = {}
    out, seen = [], set()
    for ex, sfx in WORLD_EXCHANGES.items():
        q = Q("and", [Q("is-in", ["exchange", ex]),
                      Q("gt", ["intradaymarketcap", WORLD_MIN_MCAP]),
                      Q("gt", ["avgdailyvol3m", 10000])])
        offset, n_ex = 0, 0
        while True:
            r = None
            for essai in range(3):
                try:
                    r = yf.screen(q, offset=offset, size=250,
                                  sortField="intradaymarketcap", sortAsc=False)
                    break
                except Exception as e:
                    print(f"[universe] screener {ex} offset {offset} : {e}")
                    time.sleep(3 * (essai + 1))
            if not r:
                break
            quotes = r.get("quotes") or []
            for x in quotes:
                sym = x.get("symbol") or ""
                if not sym or sym in seen:
                    continue
                # Bonne place de cotation : suffixe attendu (US : aucun point)
                if sfx and not sym.endswith(sfx):
                    continue
                if not sfx and "." in sym:
                    continue
                cur   = x.get("currency") or ("USD" if not sfx else "EUR")
                price = x.get("regularMarketPrice") or 0
                vol   = x.get("averageDailyVolume3Month") or 0
                chg   = x.get("fiftyTwoWeekChangePercent") or 0
                if cur not in fx_cache:
                    fx_cache[cur] = _fx_eur(cur)
                traded = price * vol * fx_cache[cur]
                if traded < WORLD_MIN_TRADED_EUR or abs(chg) > WORLD_MAX_52W_PCT:
                    continue
                seen.add(sym)
                n_ex += 1
                out.append({"ticker": sym, "exchange": ex, "currency": cur,
                            "name": x.get("longName") or x.get("shortName") or sym,
                            "traded_eur": round(traded),
                            "mcap": x.get("marketCap"),
                            "chg_52w": round(chg, 1)})
            offset += len(quotes)
            if not quotes or offset >= (r.get("total") or 0):
                break
        log(f"{ex} : {n_ex} valeurs")
    out.sort(key=lambda e: e["traded_eur"], reverse=True)
    return _dedupe(out, log)


_FORMES = re.compile(r"\b(inc|incorporated|corp|corporation|plc|se|sa|s\.a|ag|nv|n\.v|"
                     r"ltd|limited|co|company|holdings?|group|the|class [a-c]|"
                     r"asa|spa|ab|oyj|cl [a-c])\b")


def _norm_name(name: str) -> str:
    n = _FORMES.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def _dedupe(entries: list[dict], log=print) -> list[dict]:
    """Une ligne par SOCIÉTÉ : la plus liquide. Xetra, Londres ou Zurich
    listent aussi des valeurs étrangères (Alphabet à Francfort : ABEA.DE) —
    sans ce filtre, la même société occupait deux places du classement, et
    la moins chère à traiter n'était pas forcément celle retenue. Entrées déjà
    triées par liquidité décroissante : la première vue est gardée."""
    vus, out, doublons = set(), [], 0
    for e in entries:
        k = _norm_name(e.get("name", "")) or e["ticker"]
        if k in vus:
            doublons += 1
            continue
        vus.add(k)
        out.append(e)
    if doublons:
        log(f"{doublons} doubles cotations écartées")
    return out


def refresh_world(send_fn=None) -> dict:
    """Univers mondial → indicateurs, mis en cache pour le scan. Quotidien,
    avant l'ouverture (marchés fermés : le passage groupé ne gêne aucun cours
    suivi). Les valeurs manquées par un lot rate-limité sont retentées une fois
    en petits lots — c'est ce qui laissait le gisement US à 210/1482 valeurs."""
    def log(m):
        print(f"[universe] {m}")
        if send_fn:
            send_fn(m)
    t0 = time.time()
    entries = fetch_world_symbols(log=lambda m: print(f"[universe] {m}"))
    log(f"{len(entries)} actions liquides sur {len(WORLD_EXCHANGES)} bourses accessibles")
    if not entries:
        raise RuntimeError("screener Yahoo vide — cache précédent conservé")
    save_cache(entries, "world")
    tickers = [e["ticker"] for e in entries]
    ind = compute_indicators_bulk(tickers)
    manquants = [t for t in tickers if t not in ind]
    if manquants:
        time.sleep(10)
        ind.update(compute_indicators_bulk(manquants, batch=50))
    save_indicators(ind, "world")
    log(f"indicateurs : {len(ind)}/{len(tickers)} valeurs en {(time.time()-t0)/60:.1f} min")
    return {"symbols": len(entries), "indicators": len(ind)}
