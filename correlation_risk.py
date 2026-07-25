"""
Corrélation avec le portefeuille détenu (07/2026) : avant d'ouvrir une nouvelle
position autonome, vérifie qu'elle n'ajoute pas juste une deuxième couche du
même pari qu'une position déjà gérée par le bot (ex: AIR + SAF, même thème
aéro) — deux tickers peuvent avoir des scores quant indépendants et pourtant
bouger quasiment ensemble, ce que la corrélation des rendements quotidiens
révèle directement.
"""
import pandas as pd
import yfinance as yf
from config import CORR_LOOKBACK_DAYS, CORR_DAMPEN_THRESHOLD, CORR_VETO_THRESHOLD


def _daily_returns(ticker: str):
    try:
        hist = yf.Ticker(ticker).history(period=f"{CORR_LOOKBACK_DAYS + 20}d")
        closes = hist["Close"].dropna()
        if len(closes) < 20:
            return None
        return closes.pct_change().dropna().tail(CORR_LOOKBACK_DAYS)
    except Exception:
        return None


def max_correlation(ticker: str, held_tickers: list[str]) -> tuple[float, str | None]:
    """Corrélation de Pearson (rendements quotidiens) la plus forte entre
    `ticker` et chaque position de `held_tickers`. Retourne (corr, ticker
    détenu correspondant) — (0.0, None) si pas assez de données communes pour
    juger (marchés fermés différents, IPO récente, etc.)."""
    cand = _daily_returns(ticker)
    if cand is None:
        return 0.0, None
    best_corr, best_t = 0.0, None
    for held in held_tickers:
        if held.upper() == ticker.upper():
            continue
        held_ret = _daily_returns(held)
        if held_ret is None:
            continue
        both = pd.concat([cand, held_ret], axis=1, join="inner").dropna()
        if len(both) < 20:
            continue
        corr = both.iloc[:, 0].corr(both.iloc[:, 1])
        if corr == corr and abs(corr) > abs(best_corr):  # corr == corr écarte NaN
            best_corr, best_t = corr, held
    return round(best_corr, 2), best_t


def size_factor(ticker: str, held_tickers: list[str]) -> tuple[float, str | None, str | None]:
    """
    Retourne (factor, note, veto_reason) :
    - veto_reason non-None → l'appelant DOIT bloquer l'entrée (même pari
      qu'une position déjà détenue, aucune diversification réelle).
    - factor < 1.0 (avec note) → corrélation modérée, risque réduit de moitié.
    - sinon (1.0, None, None) → rien à signaler.
    """
    if not held_tickers:
        return 1.0, None, None
    corr, held = max_correlation(ticker, held_tickers)
    if held is None:
        return 1.0, None, None
    if abs(corr) >= CORR_VETO_THRESHOLD:
        return 0.0, None, (f"corrélation {corr:+.2f} avec {held} déjà en portefeuille "
                           f"— même pari, aucune diversification")
    if abs(corr) >= CORR_DAMPEN_THRESHOLD:
        return 0.5, f"corrélé à {held} ({corr:+.2f}) → risque réduit de moitié", None
    return 1.0, None, None
