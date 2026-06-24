"""
Passage d'ordres Bourse Direct via l'API hub/trading.

Découvert par exploration Playwright sur :
  - /fr/marche/euronext-paris/exosens-.../seance  (formulaire Vue.js)
  - ordertrade.bundle.js  (fonctions buildCreateOrderPayload / buildSendOrderPayload)

API base  : https://www.boursedirect.fr/hub/trading
Auth      : cookies de session Playwright (withCredentials=true)
CSRF      : token statique extrait du bundle

Flow en 2 étapes :
  1. create_order()  → POST /order/create  → retourne order_id + frais
  2. send_order()    → POST /order/send    → envoi RÉEL au marché ⚠️
  (ou execute_strategy() → /order/execute/strategy pour ordre Expert)
"""

import os
import json

BD_API_BASE = "https://www.boursedirect.fr/hub/trading"
BD_LOGIN    = os.getenv("BD_LOGIN", "")
# Numéro de compte CTO — donnée personnelle, à mettre dans .env (BD_ACCOUNT)
BD_ACCOUNT  = os.getenv("BD_ACCOUNT", "")

# Token CSRF — extrait dynamiquement de la session Playwright si possible,
# sinon fallback sur la valeur statique du bundle ordertrade.bundle.js.
_CSRF_STATIC = "OWY4NmQwODE4ODRjN2Q2NTlhMmZlYWEwYzU1YWQwMTVhM2JmNGYxYjJiMGI4MjJjZDE1ZDMGYwMGEwOA=="


def _get_csrf(page) -> str:
    """
    Tente d'extraire le token CSRF depuis la page BD (meta tag, cookie XSRF, global JS).
    Retourne le token statique du bundle en fallback.
    """
    try:
        token = page.evaluate("""() => {
            // 1. Meta tag
            const meta = document.querySelector('meta[name="csrf-token"], meta[name="_token"]');
            if (meta && meta.content) return meta.content;
            // 2. Cookie XSRF-TOKEN (standard Angular/Laravel)
            const xsrf = document.cookie.split(';').map(c => c.trim())
                .find(c => c.startsWith('XSRF-TOKEN=') || c.startsWith('csrf='));
            if (xsrf) return xsrf.split('=').slice(1).join('=');
            // 3. Globals JS injectés par BD
            if (window._csrf) return window._csrf;
            if (window.csrf_token) return window.csrf_token;
            return null;
        }""")
        if token:
            return token
    except Exception:
        pass
    return _CSRF_STATIC

# ── Mapping yfinance ticker → données BD ─────────────────────────────────────
# mic       : Market Identifier Code (XPAR=Euronext Paris, XNAS=NASDAQ, XNYS=NYSE...)
# bd_ticker : mnémonique de la place — PAS de préfixe "E:" (confirmé via /order/create live)
#             ex: Air Liquide XPAR → "AI", Xetra → "AIL", ILMN → "ILMN"
# Pour les tickers yfinance type ISIN (.PA sur un ISIN), préciser le vrai mnémo ici.
TICKER_MAP: dict[str, dict] = {
    "EXENS.PA":               {"bd_ticker": "EXENS",  "mic": "XPAR", "currency": "EUR"},
    "GNFT.PA":                {"bd_ticker": "GNFT",   "mic": "XPAR", "currency": "EUR"},
    "LBIRD.PA":               {"bd_ticker": "LBIRD",  "mic": "XPAR", "currency": "EUR"},
    "MCPHY.PA":               {"bd_ticker": "MCPHY",  "mic": "XPAR", "currency": "EUR"},
    "FR0011799907.PA":        {"bd_ticker": "ALGV",   "mic": "XPAR", "currency": "EUR"},  # Genomic Vision
    "ILMN":                   {"bd_ticker": "ILMN",   "mic": "XNAS", "currency": "USD"},
    # Ajouter au fur et à mesure lors des premiers passages d'ordres
}


def get_ticker_info(yf_ticker: str) -> dict | None:
    """
    Retourne les données BD pour un ticker yfinance.
    Cherche d'abord dans TICKER_MAP (override manuel),
    puis applique les règles de conversion automatique.

    Règles de conversion yfinance → BD (mnémonique SANS préfixe) :
      .PA  → <base>  MIC=XPAR  EUR   (Euronext Paris)
      .BR  → <base>  MIC=XBRU  EUR   (Euronext Bruxelles)
      .AS  → <base>  MIC=XAMS  EUR   (Euronext Amsterdam)
      .L   → <base>  MIC=XLON  GBP   (London Stock Exchange)
      .DE  → <base>  MIC=XETR  EUR   (Xetra Frankfurt)
      (rien) → <base> MIC=XNAS USD   (NASDAQ — défaut US)

    NOTE : le mnémonique BD peut différer du ticker yfinance (ex: Genomic Vision
    = GVN sur yfinance ISIN mais ALGV sur Euronext). En cas de doute, ajouter
    un override explicite dans TICKER_MAP.
    """
    t = yf_ticker.upper()
    if t in TICKER_MAP:
        return TICKER_MAP[t]

    for suffix, mic, currency in [
        (".PA", "XPAR", "EUR"),
        (".BR", "XBRU", "EUR"),
        (".AS", "XAMS", "EUR"),
        (".L",  "XLON", "GBP"),
        (".DE", "XETR", "EUR"),
    ]:
        if t.endswith(suffix):
            base = t[: -len(suffix)]
            return {"bd_ticker": base, "mic": mic, "currency": currency}

    # US sans suffixe — NASDAQ par défaut (NYSE → ajouter override "mic": "XNYS")
    return {"bd_ticker": t, "mic": "XNAS", "currency": "USD"}


def register_ticker(yf_ticker: str, bd_ticker: str, mic: str, currency: str):
    """Enregistre dynamiquement un nouveau ticker dans le mapping."""
    TICKER_MAP[yf_ticker.upper()] = {
        "bd_ticker": bd_ticker,
        "mic":       mic,
        "currency":  currency,
    }


# ── Appel API interne (via fetch Playwright avec cookies de session) ──────────

# Dernière réponse brute — permet au caller de logguer/afficher l'erreur
_last_raw: dict = {}


def _api_post(page, endpoint: str, payload: dict) -> dict | None:
    """
    POST sur hub/trading via fetch() dans le contexte Playwright.
    Les cookies de session BD sont transmis automatiquement.
    Passe le payload via argument JS (évite les problèmes d'échappement).
    """
    global _last_raw
    url = BD_API_BASE + endpoint
    result = page.evaluate(
        """async ([url, payload]) => {
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                    credentials: 'include'
                });
                const text = await resp.text();
                let data;
                try { data = JSON.parse(text); } catch(e) { data = {raw: text}; }
                return {ok: resp.ok, status: resp.status, data};
            } catch(e) {
                return {ok: false, status: 0, error: e.toString()};
            }
        }""",
        [url, payload],
    )
    _last_raw = result or {}
    if result and not result.get("ok"):
        print(f"[BD Orders] {endpoint} HTTP {result.get('status')} : {json.dumps(result.get('data', {}))[:400]}")
    return result


# ── Étape 1 : créer l'ordre (validation + calcul des frais) ──────────────────

def create_order(page, ticker: str, side: str, qty: int,
                 order_type: str = "market",
                 limit_price: float = None,
                 stop_price:  float = None,
                 validity:    str   = "end_of_year",
                 smart:       dict  = None) -> dict | None:
    """
    Crée un ordre sur BD — étape de validation.
    L'ordre n'est PAS encore envoyé au marché.

    Retourne le dict de réponse BD (contient order_id, fees, messages)
    ou None si erreur.

    Paramètres :
      ticker     : ticker yfinance (ex: "EXENS.PA", "ILMN")
      side       : "buy" | "sell"
      order_type : "market" | "limit" | "stop" | "stop_limit" | "best_limit" | "meta"
      limit_price: prix limite (TP pour ordre Expert)
      stop_price : seuil de déclenchement (SL pour ordre Expert)
      validity   : "day" | "revocation" | "end_of_year"
      smart      : dict pour ordre Expert (voir create_expert_order)
    """
    info = get_ticker_info(ticker)
    if not info:
        print(f"[BD Orders] Ticker inconnu : {ticker} — ajouter dans TICKER_MAP")
        return None

    # end_of_year n'existe que sur Euronext Paris (cf. PLAYWRIGHT_STRATEGY.md :
    # « Fin d'année, XPAR only ») — sur les autres places BD renvoie un 400
    # « validityDate : format incorrect ». Fallback validité jour.
    if validity == "end_of_year" and info["mic"] != "XPAR":
        print(f"[BD Orders] validity end_of_year indisponible sur {info['mic']} → day")
        validity = "day"

    # validityDate ISO 8601 : jour courant si validity=day (confirmé live)
    from datetime import datetime, timedelta
    validity_date = None
    if validity in ("end_of_year",):
        validity_date = f"{datetime.now().year}-12-31T00:00:00.000Z"
    elif validity == "day":
        validity_date = datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")

    payload = {
        "login":          BD_LOGIN.upper(),          # MAJUSCULES (confirmé live)
        "mic":            info["mic"],
        "ticker":         info["bd_ticker"],          # mnémonique SANS préfixe E:
        "currency":       info["currency"],
        "quantity":       str(qty),                   # string (confirmé live)
        "portfolio":      BD_ACCOUNT,
        "type":           order_type,
        "side":           side,
        "validity":       validity,
        "validityDate":   validity_date,
        "settlement":     "cash",
        "limit":          limit_price,
        "stop":           stop_price,
        "position_effect": "open" if side == "buy" else "close",
        "globex":         False,
        "comment":        None,
        "brokerage":      None,
    }

    if smart:
        payload["nature"] = "smart"
        if side == "sell" and smart.get("strategy") == "take_profit":
            payload["type"] = "meta"
        payload["smart"] = smart

    result = _api_post(page, "/order/create", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] create_order HTTP {result.get('status')} : {result.get('data')}")
        return None

    data = result.get("data")
    # Log complet pour découvrir la structure réelle de la réponse BD
    print(f"[BD Orders] create_order OK : {json.dumps(data)[:600]}")
    return data


# ── Étape 2 : confirmer l'envoi (irréversible) ───────────────────────────────

def send_order(page, order_id: str) -> dict | None:
    """
    Envoie l'ordre au marché — ACTION IRRÉVERSIBLE.
    Appeler UNIQUEMENT après double confirmation de l'utilisateur.

    order_id : identifiant retourné par create_order()
    """
    payload = {
        "order_id": order_id,
        "login":    BD_LOGIN.upper(),
        "csrf":     _get_csrf(page),
    }
    result = _api_post(page, "/order/send", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] send_order HTTP {result.get('status')} : {result.get('data')}")
        return None
    return result.get("data")


def cancel_order(page, order_id: str) -> dict | None:
    """
    Annule un ordre en cours sur BD (Take Profit, Stop Loss, ordre limite...).
    Endpoint /order/cancel confirmé dans portfolio.js.
    """
    payload = {
        "order_id": order_id,
        "login":    BD_LOGIN.upper(),
        "csrf":     _get_csrf(page),
    }
    result = _api_post(page, "/order/cancel", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] cancel_order HTTP {result.get('status')} : {result.get('data')}")
        return None
    return result.get("data")


def execute_strategy(page, order_id: str) -> dict | None:
    """
    Exécute un ordre Expert (SL+TP combiné) — ACTION IRRÉVERSIBLE.
    Utiliser pour les ordres de type 'smart'/'meta' après create_order().
    """
    payload = {
        "order_id": order_id,
        "login":    BD_LOGIN.upper(),
        "csrf":     _get_csrf(page),
    }
    result = _api_post(page, "/order/execute/strategy", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] execute_strategy HTTP {result.get('status')} : {result.get('data')}")
        return None
    return result.get("data")


# ── Helpers ordre Expert (Take Profit + Stop Loss combinés) ──────────────────

def create_expert_order(page, ticker: str, qty: int,
                        stop_loss: float, take_profit: float) -> dict | None:
    """
    Crée un ordre Expert Take Profit (SL + TP en un seul ordre).
    Correspond aux ordres 'Vente transmise au marché (Cpt EQT)' dans BD.

    Retourne les données de l'ordre créé (non encore envoyé).
    Appeler execute_strategy(page, order_id) pour confirmer.
    """
    smart = {
        "strategy":    "take_profit",
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "variation":   None,
    }
    return create_order(
        page, ticker, side="sell", qty=qty,
        order_type="meta", smart=smart,
        validity="end_of_year",
    )


def format_order_summary(order_data: dict, ticker: str, side: str,
                         qty: int, order_type: str,
                         limit_price: float = None, stop_price: float = None) -> str:
    """Formate un résumé lisible de l'ordre créé pour confirmation Telegram."""
    order_id = order_data.get("id") or order_data.get("order_id", "?")

    # Montant prévisionnel (seule donnée financière retournée par /order/create)
    # Les frais BD réels (courtage) ne sont communiqués qu'après envoi.
    prev = (order_data.get("order") or {}).get("previsionalAmount") or {}
    montant = prev.get("value")
    currency = prev.get("currency", "EUR")
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, "€")

    side_fr = "ACHAT" if side == "buy" else "VENTE"
    type_map = {
        "market":     "Au marché",
        "limit":      "Cours limité",
        "stop":       "Seuil déclenchement",
        "stop_limit": "Plage déclenchement",
        "meta":       "Expert (SL+TP)",
    }
    type_fr = type_map.get(order_type, order_type)

    lines = [
        f"{side_fr} {qty}x {ticker}",
        f"Type : {type_fr}",
    ]
    if limit_price:
        lines.append(f"Limite : {limit_price}{sym}")
    if stop_price:
        lines.append(f"Seuil  : {stop_price}{sym}")
    if montant is not None:
        lines.append(f"Montant prévu : {montant}{sym}  (frais BD ajoutés à l'exécution)")
    else:
        lines.append(f"Montant prévu : —")
    lines.append(f"Ref BD : {order_id}")

    return "\n".join(lines)
