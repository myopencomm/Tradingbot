import yfinance as yf


def get_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as e:
        print(f"⚠️ Price error {ticker}: {e}")
    return None


def get_quote(ticker: str) -> dict:
    """Returns price, previous close, % change, and status.

    status values:
      'ok'        — price available
      'suspended' — no trade data over 1 month, likely delisted / in liquidation
      'error'     — unexpected fetch failure
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            current = float(hist["Close"].iloc[-1])
            change_pct = ((current - prev) / prev) * 100
            return {
                "ticker": ticker,
                "price": round(current, 4),
                "prev_close": round(prev, 4),
                "change_pct": round(change_pct, 2),
                "status": "ok",
            }
        elif len(hist) == 1:
            current = float(hist["Close"].iloc[-1])
            return {
                "ticker": ticker,
                "price": round(current, 4),
                "prev_close": round(current, 4),
                "change_pct": 0.0,
                "status": "ok",
            }
        else:
            # No recent data — check if there has been ANY trading in the past month
            long_hist = yf.Ticker(ticker).history(period="1mo")
            status = "suspended" if long_hist.empty else "no_recent_data"
            return {"ticker": ticker, "price": None, "change_pct": None, "status": status}
    except Exception as e:
        print(f"⚠️ Quote error {ticker}: {e}")
        return {"ticker": ticker, "price": None, "change_pct": None, "status": "error"}
