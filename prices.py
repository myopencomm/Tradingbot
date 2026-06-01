import time
import yfinance as yf

# Cache devise par ticker (USD, EUR, GBP…)
_currency_cache: dict[str, str] = {}


def _ticker_currency(ticker: str) -> str:
    if ticker in _currency_cache:
        return _currency_cache[ticker]
    try:
        fi = yf.Ticker(ticker).fast_info
        currency = (getattr(fi, "currency", None) or "EUR").upper()
    except Exception:
        currency = "EUR"
    _currency_cache[ticker] = currency
    return currency


def currency_symbol(currency: str) -> str:
    return {"USD": "$", "GBP": "£", "JPY": "¥"}.get(currency, "€")


def get_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as e:
        print(f"⚠️ Price error {ticker}: {e}")
    return None


def get_technicals(ticker: str) -> dict:
    """RSI 14j, momentum 1 mois, ratio volume vs moyenne 20 séances."""
    try:
        hist = yf.Ticker(ticker).history(period="3mo")
        if len(hist) < 22:
            return {}
        closes  = hist["Close"]
        volumes = hist["Volume"]
        delta   = closes.diff()
        gain    = delta.clip(lower=0).rolling(14).mean()
        loss    = (-delta.clip(upper=0)).rolling(14).mean()
        rs      = gain / loss
        rsi     = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)
        mom_1m  = round(float((closes.iloc[-1] / closes.iloc[-22] - 1) * 100), 1)
        vol_avg = float(volumes.iloc[-20:].mean())
        vol_r   = round(float(volumes.iloc[-1]) / vol_avg, 2) if vol_avg else None
        return {"rsi": rsi, "momentum_1m": mom_1m, "vol_ratio": vol_r}
    except Exception as e:
        print(f"⚠️ Technicals error {ticker}: {e}")
        return {}


def get_quote(ticker: str) -> dict:
    """Retourne prix dans la devise native du ticker, avec devise détectée.

    status : 'ok' | 'suspended' | 'error'
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if len(hist) >= 2:
            prev       = float(hist["Close"].iloc[-2])
            current    = float(hist["Close"].iloc[-1])
            change_pct = ((current - prev) / prev) * 100
        elif len(hist) == 1:
            current    = float(hist["Close"].iloc[-1])
            prev       = current
            change_pct = 0.0
        else:
            long_hist = yf.Ticker(ticker).history(period="1mo")
            status = "suspended" if long_hist.empty else "no_recent_data"
            return {"ticker": ticker, "price": None, "currency": "EUR",
                    "change_pct": None, "status": status}

        currency = _ticker_currency(ticker)
        return {
            "ticker":     ticker,
            "price":      round(current, 4),
            "currency":   currency,
            "prev_close": round(prev, 4),
            "change_pct": round(change_pct, 2),
            "status":     "ok",
        }

    except Exception as e:
        print(f"⚠️ Quote error {ticker}: {e}")
        return {"ticker": ticker, "price": None, "currency": "EUR",
                "change_pct": None, "status": "error"}
