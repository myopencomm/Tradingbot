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


# Taux de change → EUR, cache 1h (suffisant pour du sizing de position)
_fx_cache: dict = {}   # currency → (rate_to_eur, timestamp)
_FX_TTL = 3600

def fx_to_eur(currency: str) -> float:
    """
    Taux de conversion 1 unité de `currency` → EUR (ex: USD 0.877 si EURUSD=1.14).
    Retourne 1.0 pour EUR ou si le taux est indisponible (fallback conservateur :
    surestime le coût des devises faibles plutôt que de sous-estimer).
    """
    import time as _time
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return 1.0
    cached = _fx_cache.get(cur)
    if cached and _time.time() - cached[1] < _FX_TTL:
        return cached[0]
    try:
        pair = yf.Ticker(f"EUR{cur}=X").history(period="1d")
        if not pair.empty:
            eur_to_cur = float(pair["Close"].iloc[-1])
            if eur_to_cur > 0:
                rate = round(1.0 / eur_to_cur, 6)
                _fx_cache[cur] = (rate, _time.time())
                return rate
    except Exception as e:
        print(f"⚠️ FX error {cur}: {e}")
    return 1.0


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
    """Indicateurs techniques pour la sélection momentum validée par la recherche.

    - rsi / momentum_1m / vol_ratio : historiques (consommés partout).
    - mom_12_1 : momentum 12 mois HORS dernier mois (Jegadeesh & Titman 1993) —
      le signal de formation ; le momentum 1 mois seul s'inverse (Jegadeesh 1990).
    - above_ma200 / ma200_dist_pct : filtre de tendance long terme.
    - atr_pct : ATR 14j en % du cours — sert au SL adapté à la volatilité.
    - vol_ratio_20_250 : volatilité réalisée 20j / 1 an — >1.5 = titre en
      régime de volatilité élevée → taille réduite (Barroso & Santa-Clara 2015).
    """
    try:
        hist = yf.Ticker(ticker).history(period="1y")
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
        # Utilise le dernier jour complet (iloc[-2]) pour éviter le volume
        # partiel intraday qui fausse le ratio (ex: 0.07x à 10h du matin)
        vol_last = float(volumes.iloc[-2])
        vol_avg  = float(volumes.iloc[-21:-1].mean())
        vol_r    = round(vol_last / vol_avg, 2) if vol_avg else None
        out = {"rsi": rsi, "momentum_1m": mom_1m, "vol_ratio": vol_r}

        # Momentum 12-1 : perf du début de l'historique (≈12 mois) jusqu'à il y
        # a 1 mois. Avec < 10 mois d'historique (IPO récente) : non calculable.
        if len(closes) >= 210:
            out["mom_12_1"] = round(float((closes.iloc[-22] / closes.iloc[0] - 1) * 100), 1)

        # MM200 (filtre de tendance)
        if len(closes) >= 200:
            ma200 = float(closes.rolling(200).mean().iloc[-1])
            cur   = float(closes.iloc[-1])
            out["above_ma200"]    = cur > ma200
            out["ma200_dist_pct"] = round((cur / ma200 - 1) * 100, 1)

        # ATR 14j en % du cours (True Range classique)
        if {"High", "Low"}.issubset(hist.columns) and len(hist) >= 15:
            prev_close = closes.shift(1)
            tr = (hist["High"] - hist["Low"]).combine(
                (hist["High"] - prev_close).abs(), max).combine(
                (hist["Low"] - prev_close).abs(), max)
            atr = float(tr.rolling(14).mean().iloc[-1])
            cur = float(closes.iloc[-1])
            if cur:
                out["atr_pct"] = round(atr / cur * 100, 2)

        # Régime de volatilité du titre : réalisée 20j vs 1 an
        rets = closes.pct_change().dropna()
        if len(rets) >= 60:
            vol_20  = float(rets.iloc[-20:].std())
            vol_all = float(rets.std())
            if vol_all:
                out["vol_ratio_20_250"] = round(vol_20 / vol_all, 2)

        return out
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


def get_market_regime() -> dict:
    """
    Détecte le régime de marché global : BULL / NEUTRAL / CORRECTION / CRISIS.
    Critères : position CAC40 + S&P500 vs MM20, momentum 1 mois, VIX.
    Retourne {"label", "vix", "cac_vs_mm20", "spy_vs_mm20", "index_mom_avg",
              "summary"} — summary = ligne lisible pour les prompts IA.
    """
    try:
        vix_data = get_vix()
        vix = vix_data.get("level", 20.0)

        idx_data = {}
        for ticker, key in [("^FCHI", "cac"), ("^GSPC", "spy")]:
            hist = yf.Ticker(ticker).history(period="1y").dropna(subset=["Close"])
            if len(hist) < 20:
                continue
            cur   = float(hist["Close"].iloc[-1])
            mm20  = float(hist["Close"].rolling(20).mean().iloc[-1])
            mom1m = float((cur / hist["Close"].iloc[-22] - 1) * 100) if len(hist) >= 22 else 0.0
            idx_data[key] = {
                "vs_mm20": round((cur / mm20 - 1) * 100, 1),
                "mom1m":   round(mom1m, 1),
            }
            # Filtre de tendance long terme (MM200) : le VIX seul rate les
            # marchés qui plafonnent sans paniquer (juin-juillet 2026 : VIX < 20
            # mais CAC sous son pic — toutes les pertes entrées dans ce régime).
            if len(hist) >= 200:
                mm200 = float(hist["Close"].rolling(200).mean().iloc[-1])
                idx_data[key]["above_ma200"] = cur > mm200
                idx_data[key]["vs_mm200"]    = round((cur / mm200 - 1) * 100, 1)

        cac_vs   = idx_data.get("cac", {}).get("vs_mm20", 0.0)
        spy_vs   = idx_data.get("spy", {}).get("vs_mm20", 0.0)
        above_mm = sum(1 for v in [cac_vs, spy_vs] if v > -1.0)
        avg_mom  = sum(idx_data.get(k, {}).get("mom1m", 0) for k in ("cac", "spy")) / 2
        ma200_flags = [d.get("above_ma200") for d in idx_data.values()
                       if d.get("above_ma200") is not None]
        nb_above_ma200 = sum(1 for f in ma200_flags if f)

        if vix > 40:
            label = "CRISIS"
        elif vix > 28 or above_mm == 0 or (ma200_flags and nb_above_ma200 == 0):
            # Indices sous leur MM200 = tendance long terme cassée → régime
            # défensif (force relative uniquement), même si le VIX reste calme.
            label = "CORRECTION"
        elif vix > 20 or above_mm < 2 or avg_mom < -2 or (ma200_flags and nb_above_ma200 < len(ma200_flags)):
            label = "NEUTRAL"
        else:
            label = "BULL"

        cac_str = f"CAC {cac_vs:+.1f}% vs MM20" if "cac" in idx_data else ""
        spy_str = f"SPY {spy_vs:+.1f}% vs MM20" if "spy" in idx_data else ""
        ma_str  = (f"MM200 : {nb_above_ma200}/{len(ma200_flags)} indices au-dessus"
                   if ma200_flags else "")
        summary = (f"RÉGIME {label} | VIX {vix}"
                   + (f" | {cac_str}" if cac_str else "")
                   + (f" | {spy_str}" if spy_str else "")
                   + (f" | {ma_str}" if ma_str else ""))

        return {
            "label":         label,
            "vix":           round(vix, 1),
            "cac_vs_mm20":   idx_data.get("cac", {}).get("vs_mm20"),
            "spy_vs_mm20":   idx_data.get("spy", {}).get("vs_mm20"),
            "cac_vs_mm200":  idx_data.get("cac", {}).get("vs_mm200"),
            "spy_vs_mm200":  idx_data.get("spy", {}).get("vs_mm200"),
            "index_mom_avg": round(avg_mom, 1),
            "summary":       summary,
        }
    except Exception as e:
        print(f"⚠️ Market regime error: {e}")
        return {"label": "NEUTRAL", "vix": None, "cac_vs_mm20": None,
                "spy_vs_mm20": None, "index_mom_avg": 0.0,
                "summary": "RÉGIME NEUTRAL (données indisponibles)"}


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


def get_chart_image(ticker: str, period: str = "3mo") -> bytes | None:
    """
    Génère un graphique chandeliers (MM20, MM50, volume) et retourne les bytes JPEG.
    Utilisé pour l'analyse technique vision IA.
    """
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")  # backend non-interactif (pas de fenêtre)
        import matplotlib.pyplot as plt
        import mplfinance as mpf
        from PIL import Image

        hist = yf.Ticker(ticker).history(period=period)
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 10:
            return None

        # Style sombre lisible par la vision IA
        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            gridstyle=":",
            gridcolor="#333333",
        )

        png_buf = io.BytesIO()
        mpf.plot(
            hist,
            type="candle",
            style=style,
            volume=True,
            mav=(20, 50),
            mavcolors=["#00bfff", "#ff9900"],
            title=f"\n{ticker} — {period}",
            figsize=(10, 6),
            savefig=dict(fname=png_buf, dpi=100, bbox_inches="tight"),
        )
        plt.close("all")

        # Conversion PNG → JPEG (compatible avec tous les providers vision)
        png_buf.seek(0)
        img = Image.open(png_buf).convert("RGB")
        jpg_buf = io.BytesIO()
        img.save(jpg_buf, format="JPEG", quality=85)
        jpg_buf.seek(0)
        return jpg_buf.read()

    except Exception as e:
        print(f"⚠️ Chart error {ticker}: {e}")
        return None
