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
     courtage (~4€ A/R, seuil de rentabilité 5×).

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


def cache_info() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text())
        return {k: {"updated": v.get("updated"), "count": v.get("count")}
                for k, v in data.items()}
    except Exception:
        return {}
