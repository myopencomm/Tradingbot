"""
Le bot bat-il le S&P 500 ? — même capital, mêmes dates, en euros.

Pour chaque trade, on se demande : « si ces euros étaient restés dans le
S&P 500 du jour d'achat au jour de vente, combien auraient-ils rapporté ? ».
La différence avec le P&L réel est ce que le bot apporte (ou coûte) par
rapport à un simple ETF.

Demandé le 24/09/2026 : 17 trades à +456 € réalisés, mais aucun moyen de
savoir si c'était mieux que l'indice. Sans ce chiffre, garder ou arrêter le
bot se décide au ressenti.

Choix assumés :
  - S&P 500 CONVERTI EN EUROS (^GSPC / EURUSD) : le capital du compte est en
    euros, un ETF S&P non couvert suit exactement cela.
  - indice de PRIX (hors dividendes, ~1,3 %/an) et sans frais d'ETF : les
    deux se compensent à peu près sur des durées de quelques semaines.
  - clôture du jour d'achat et du jour de vente (la plus proche AVANT, si le
    jour n'a pas coté).
"""
import time
from datetime import datetime, timedelta

import pandas as pd

_cache: dict = {}          # {"series": pd.Series, "ts": float, "start": str}
_TTL = 3600


def _spx_eur(start: str) -> pd.Series | None:
    """Clôtures du S&P 500 en euros, indexées par date « AAAA-MM-JJ »."""
    c = _cache
    if c.get("series") is not None and time.time() - c["ts"] < _TTL \
            and c["start"] <= start:
        return c["series"]
    try:
        import yfinance as yf
        raw = yf.download(["^GSPC", "EURUSD=X"], start=start, progress=False,
                          auto_adjust=True, group_by="ticker")
        spx = raw["^GSPC"]["Close"].dropna()
        eur = raw["EURUSD=X"]["Close"].dropna()
        spx.index = pd.DatetimeIndex(spx.index).strftime("%Y-%m-%d")
        eur.index = pd.DatetimeIndex(eur.index).strftime("%Y-%m-%d")
        days = pd.date_range(start, datetime.now().strftime("%Y-%m-%d")).strftime("%Y-%m-%d")
        serie = (spx.reindex(days).ffill() / eur.reindex(days).ffill()).dropna()
    except Exception as e:
        print(f"[benchmark] S&P 500 indisponible : {e}")
        return None
    if serie.empty:
        return None
    _cache.update(series=serie, ts=time.time(), start=start)
    return serie


def _at(serie: pd.Series, day: str) -> float | None:
    """Valeur au jour `day`, ou à la dernière séance avant."""
    s = serie[serie.index <= day]
    return float(s.iloc[-1]) if len(s) else None


def _invested_eur(t: dict) -> float | None:
    """Montant investi en euros. Trade en devise : le rapport pnl_eur/pnl
    enregistré à la clôture donne le taux, sans nouvelle requête."""
    qty, entry = abs(t.get("qty") or 0), t.get("entry_price") or 0
    if not qty or not entry:
        return None
    fx = 1.0
    if (t.get("currency") or "EUR") != "EUR":
        if t.get("pnl") and t.get("pnl_eur") is not None:
            fx = t["pnl_eur"] / t["pnl"]
        else:
            import prices
            fx = prices.fx_to_eur(t["currency"])
    return qty * entry * fx


def compare(closed: list[dict], open_views: list[dict] | None = None) -> dict | None:
    """Bot vs S&P 500 sur le même capital et les mêmes dates.

    `closed` : trades clôturés (history.closed_trades()).
    `open_views` : positions ouvertes gérées (position_view.views), comptées
    jusqu'à aujourd'hui avec leur P&L latent.

    Retourne {n, bot_eur, spx_eur, ecart_eur, battus, skipped} ou None si
    l'indice est indisponible.
    """
    rows = []
    for t in closed:
        opened, closed_at = (t.get("opened_at") or "")[:10], (t.get("closed_at") or "")[:10]
        inv = _invested_eur(t)
        if opened and closed_at and inv:
            rows.append((opened, closed_at, inv, t.get("pnl_eur", t.get("pnl", 0))))
    today = datetime.now().strftime("%Y-%m-%d")
    for v in open_views or []:
        cfg_open = (v.get("opened_at") or "")[:10]
        if cfg_open and v.get("pnl_eur") is not None and v.get("entry_eur") and v.get("qty"):
            rows.append((cfg_open, today, v["entry_eur"] * v["qty"], v["pnl_eur"]))
    skipped = len(closed) + len(open_views or []) - len(rows)
    if not rows:
        return None

    start = (datetime.fromisoformat(min(r[0] for r in rows)) - timedelta(days=7)).strftime("%Y-%m-%d")
    serie = _spx_eur(start)
    if serie is None:
        return None

    bot = spx = 0.0
    battus = n = 0
    for opened, closed_at, inv, pnl in rows:
        a, b = _at(serie, opened), _at(serie, closed_at)
        if not a or not b:
            skipped += 1
            continue
        bench = inv * (b / a - 1)
        bot += pnl
        spx += bench
        n += 1
        battus += pnl > bench
    return {"n": n, "bot_eur": round(bot, 2), "spx_eur": round(spx, 2),
            "ecart_eur": round(bot - spx, 2), "battus": battus, "skipped": skipped}
