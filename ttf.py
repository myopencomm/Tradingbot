"""
TTF — taxe française sur les transactions financières.

Module FEUILLE : il ne dépend ni de `config` ni de `prices`, seulement de
yfinance et de son cache disque. C'est ce qui casse le cycle `config ⇄ prices`
— `config` avait besoin de cette seule réponse pour chiffrer les frais et
importait `prices` tout entier, alors que `prices` importait `config` pour le
seuil de capitalisation ; les deux se contournaient par des imports différés.

Critère officiel : siège social en France ET capitalisation > 1 Md€ (appréciée
au 1er décembre précédent), 0.4 % à l'ACHAT uniquement depuis le 01/04/2025.
Ni la place ni le suffixe ne le disent — Airbus (AIR.PA) cote à Paris mais son
siège est aux Pays-Bas, Genfit (GNFT.PA) est française mais sous le milliard :
les deux sont exonérées, et nos ordres réels le confirment au centime.
"""
import json
import os
import time
from pathlib import Path

import yfinance as yf

RATE           = float(os.getenv("TTF_RATE", "0.004"))
MIN_MARKET_CAP = 1_000_000_000.0

# Cache disque : `country` et `marketCap` ne bougent pas d'un jour à l'autre, et
# `yf.Ticker().info` est lent (~1 s) — inacceptable dans un calcul de frais.
_CACHE_PATH = Path(__file__).resolve().parent / "ttf_cache.json"
_TTL = 30 * 24 * 3600
_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text())
        except Exception:
            _cache = {}
    return _cache


def is_liable(ticker: str) -> bool:
    """Ce titre supporte-t-il la TTF à l'achat ?

    En cas de donnée indisponible : True pour un `.PA`. Surestimer les frais
    fait renoncer à un trade marginal ; les sous-estimer fait entrer dans un
    trade qui ne couvre pas ses coûts.
    """
    t = (ticker or "").strip().upper()
    if not t.endswith(".PA"):
        return False

    cache = _load_cache()
    entry = cache.get(t)
    if entry and time.time() - entry.get("ts", 0) < _TTL:
        return bool(entry["liable"])

    try:
        info    = yf.Ticker(t).info or {}
        country = (info.get("country") or "").strip()
        cap     = info.get("marketCap")
        if not country or cap is None:
            raise ValueError("country/marketCap indisponibles")
        liable = country == "France" and float(cap) > MIN_MARKET_CAP
    except Exception as e:
        # Échec mémorisé 24 h : sans ça, un titre que yfinance ne sait pas
        # classer relance une requête réseau à CHAQUE calcul de frais.
        print(f"[TTF] {t} : classement impossible ({e}) — considéré assujetti")
        cache[t] = {"liable": True, "country": "?", "cap": 0.0,
                    "ts": time.time() - _TTL + 24 * 3600}
        return True

    cache[t] = {"liable": liable, "country": country,
                "cap": float(cap), "ts": time.time()}
    try:
        _CACHE_PATH.write_text(json.dumps(cache, indent=1))
    except Exception as e:
        print(f"[TTF] cache non écrit : {e}")
    return liable
