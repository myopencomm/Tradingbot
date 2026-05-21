import time
import yfinance as yf

# ─── Cache devises ────────────────────────────────────────────────────────────
_currency_cache: dict[str, str]              = {}   # ticker  → "USD" / "EUR" / ...
_fx_cache:       dict[str, tuple[float,float]] = {}  # "USD"   → (rate_to_eur, timestamp)
_FX_TTL = 3600  # 1h


def _ticker_currency(ticker: str) -> str:
    """Détecte la devise d'un ticker via yfinance (avec cache)."""
    if ticker in _currency_cache:
        return _currency_cache[ticker]
    try:
        fi = yf.Ticker(ticker).fast_info
        currency = (getattr(fi, "currency", None) or "EUR").upper()
    except Exception:
        currency = "EUR"
    _currency_cache[ticker] = currency
    return currency


def _to_eur(amount: float, currency: str) -> float:
    """Convertit un montant dans sa devise native en EUR (avec cache 1h)."""
    if currency == "EUR":
        return amount
    now = time.time()
    if currency in _fx_cache:
        rate, ts = _fx_cache[currency]
        if now - ts < _FX_TTL:
            return round(amount * rate, 4)
    try:
        hist = yf.Ticker(f"{currency}EUR=X").history(period="1d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            _fx_cache[currency] = (rate, now)
            return round(amount * rate, 4)
    except Exception as e:
        print(f"⚠️ FX rate error {currency}/EUR: {e}")
    return amount  # fallback sans conversion


def get_price(ticker: str) -> float | None:
    """Prix en EUR (converti si nécessaire)."""
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            currency = _ticker_currency(ticker)
            return round(_to_eur(price, currency), 4)
    except Exception as e:
        print(f"⚠️ Price error {ticker}: {e}")
    return None


def get_quote(ticker: str) -> dict:
    """Retourne prix en EUR, prix natif, devise, variation % et status.

    Champs retournés :
      price        — prix en EUR (pour calculs P&L)
      price_native — prix dans la devise d'origine (pour affichage)
      currency     — "EUR", "USD", "GBP"...
      change_pct   — variation % jour
      status       — 'ok' | 'suspended' | 'error'
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")

        if len(hist) >= 2:
            prev    = float(hist["Close"].iloc[-2])
            current = float(hist["Close"].iloc[-1])
            change_pct = ((current - prev) / prev) * 100
        elif len(hist) == 1:
            current = float(hist["Close"].iloc[-1])
            prev    = current
            change_pct = 0.0
        else:
            long_hist = yf.Ticker(ticker).history(period="1mo")
            status = "suspended" if long_hist.empty else "no_recent_data"
            return {"ticker": ticker, "price": None, "price_native": None,
                    "currency": "EUR", "change_pct": None, "status": status}

        currency    = _ticker_currency(ticker)
        price_eur   = _to_eur(current, currency)

        return {
            "ticker":       ticker,
            "price":        round(price_eur, 4),       # toujours en EUR
            "price_native": round(current, 4),          # dans la devise d'origine
            "currency":     currency,
            "prev_close":   round(prev, 4),
            "change_pct":   round(change_pct, 2),
            "status":       "ok",
        }

    except Exception as e:
        print(f"⚠️ Quote error {ticker}: {e}")
        return {"ticker": ticker, "price": None, "price_native": None,
                "currency": "EUR", "change_pct": None, "status": "error"}
