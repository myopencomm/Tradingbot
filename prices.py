import math
import time
from datetime import datetime, timedelta
import pytz
import requests
import yfinance as yf

_PARIS = pytz.timezone("Europe/Paris")

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


def get_fundamentals(ticker: str) -> dict:
    """Fondamentaux yfinance : objectif analyste, P/E, beta, 52w range, consensus."""
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        result = {}

        name = info.get("longName") or info.get("shortName") or ""
        if name:
            result["name"] = name
        sector = info.get("sector") or info.get("industry") or ""
        if sector:
            result["sector"] = sector

        target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        if target:
            result["analyst_target"] = round(float(target), 2)

        for key in ("trailingPE", "forwardPE"):
            if info.get(key):
                result["pe"] = round(float(info[key]), 1)
                break

        if info.get("beta"):
            result["beta"] = round(float(info["beta"]), 2)

        low52  = info.get("fiftyTwoWeekLow")
        high52 = info.get("fiftyTwoWeekHigh")
        if low52 and high52:
            result["week52_low"]  = round(float(low52), 2)
            result["week52_high"] = round(float(high52), 2)

        cap = info.get("marketCap")
        if cap:
            result["market_cap_m"] = round(cap / 1_000_000, 0)

        try:
            rec = t.recommendations_summary
            if rec is not None and not rec.empty:
                row = rec.iloc[0]
                result["analyst_buy"]   = int(row.get("strongBuy", 0) + row.get("buy", 0))
                result["analyst_hold"]  = int(row.get("hold", 0))
                result["analyst_sell"]  = int(row.get("sell", 0) + row.get("strongSell", 0))
        except Exception:
            pass

        try:
            dates = t.earnings_dates
            if dates is not None and not dates.empty:
                future = dates[dates.index > datetime.now(pytz.UTC)]
                if not future.empty:
                    result["next_earnings"] = future.index[0].strftime("%Y-%m-%d")
        except Exception:
            pass

        return result
    except Exception as e:
        print(f"⚠️ Fundamentals error {ticker}: {e}")
        return {}


def get_yf_news(ticker: str, max_items: int = 6) -> list[dict]:
    """Actualités récentes via yfinance (titre + source)."""
    try:
        news = yf.Ticker(ticker).news or []
        out  = []
        for item in news[:max_items]:
            title = item.get("title", "")
            pub   = item.get("publisher", "")
            if title:
                out.append({"title": title, "publisher": pub})
        return out
    except Exception as e:
        print(f"⚠️ YF news error {ticker}: {e}")
        return []


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


def get_intraday_range(ticker: str, hours: int = 4) -> dict:
    """High et Low des N dernières heures en bougies horaires.

    Retourne {"high": float, "low": float, "current": float}
    ou {} si données indisponibles.
    Permet de détecter un franchissement de seuil entre deux checks.
    """
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1h")
        if hist.empty:
            return {}
        cutoff = datetime.now(pytz.UTC) - timedelta(hours=hours)
        recent = hist[hist.index >= cutoff]
        if recent.empty:
            return {}
        return {
            "high":    round(float(recent["High"].max()), 4),
            "low":     round(float(recent["Low"].min()), 4),
            "current": round(float(recent["Close"].iloc[-1]), 4),
        }
    except Exception as e:
        print(f"⚠️ Intraday range error {ticker}: {e}")
        return {}


def search_ticker(query: str, max_results: int = 5) -> list[dict]:
    """Recherche Yahoo Finance par nom de société ou ticker approximatif.

    Permet le fail-safe /add LVMH → suggestion MC.PA.
    Retourne [{"symbol", "name", "exchange"}] trié par pertinence Yahoo.
    """
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": max_results * 2, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        out = []
        for q in r.json().get("quotes", []):
            if q.get("quoteType") != "EQUITY":
                continue
            out.append({
                "symbol":   q.get("symbol", ""),
                "name":     q.get("shortname") or q.get("longname", ""),
                "exchange": q.get("exchDisp") or q.get("exchange", ""),
            })
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        print(f"⚠️ Ticker search error {query}: {e}")
        return []


def get_price_context(ticker: str) -> dict:
    """Position dans le range 52 semaines + performances.

    Sert à détecter les « couteaux qui tombent » : un titre proche de son
    plus bas annuel après une forte baisse, où les objectifs analystes
    sont souvent en retard sur la réalité.
    """
    try:
        hist = yf.Ticker(ticker).history(period="1y").dropna(subset=["Close"])
        if len(hist) < 30:
            return {}
        closes = hist["Close"]
        cur = float(closes.iloc[-1])
        lo  = float(closes.min())
        hi  = float(closes.max())
        out = {
            "perf_1y":       round((cur / float(closes.iloc[0]) - 1) * 100, 1),
            "from_52w_low":  round((cur / lo - 1) * 100, 1),
            "from_52w_high": round((cur / hi - 1) * 100, 1),
        }
        if len(closes) > 63:
            out["perf_3m"] = round((cur / float(closes.iloc[-63]) - 1) * 100, 1)
        return out
    except Exception as e:
        print(f"⚠️ Price context error {ticker}: {e}")
        return {}


def get_vix() -> dict:
    """Niveau du VIX + variation 1 jour, avec lecture qualitative.

    Retourne {"level": float, "change_pct": float, "label": str} ou {}.
    """
    try:
        hist = yf.Ticker("^VIX").history(period="5d").dropna(subset=["Close"])
        if len(hist) < 2:
            return {}
        current = float(hist["Close"].iloc[-1])
        prev    = float(hist["Close"].iloc[-2])
        chg     = ((current - prev) / prev) * 100
        if current < 15:
            label = "marché calme (risk-on)"
        elif current < 20:
            label = "volatilité normale"
        elif current < 30:
            label = "marché nerveux (prudence)"
        else:
            label = "panique / forte aversion au risque"
        return {"level": round(current, 1), "change_pct": round(chg, 1), "label": label}
    except Exception as e:
        print(f"⚠️ VIX error: {e}")
        return {}


def get_quote(ticker: str) -> dict:
    """Retourne prix dans la devise native du ticker, avec devise détectée.

    status : 'ok' | 'suspended' | 'error'
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        hist = hist.dropna(subset=["Close"])
        if len(hist) >= 2:
            prev       = float(hist["Close"].iloc[-2])
            current    = float(hist["Close"].iloc[-1])
            change_pct = ((current - prev) / prev) * 100
        elif len(hist) == 1:
            current    = float(hist["Close"].iloc[-1])
            prev       = current
            change_pct = 0.0
        else:
            long_hist = yf.Ticker(ticker).history(period="1mo").dropna(subset=["Close"])
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
