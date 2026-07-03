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

def parse_validity(validity_str: str, mic: str) -> tuple[str, str | None]:
    """
    Convertit la saisie utilisateur en (validity, validityDate) pour l'API BD.

    Valeurs acceptées :
      "seance"       → day (séance du jour)
      "max"          → end_of_year sur Euronext (XPAR/XAMS/XBRU/XLIS), revocation ailleurs
      "revocation"   → GTC (bonne pour annulation)
      "DD/MM/YYYY"   → date précise (type "date")
      autres         → transmis tels quels (end_of_year, day…)

    Retourne (validity_api, validityDate_iso | None).
    """
    from datetime import datetime
    import re

    # end_of_year confirmé sur XAMS via capture réseau réelle — valable sur tout
    # Euronext (Paris/Amsterdam/Bruxelles/Lisbonne).
    EURONEXT_MICS = {"XPAR", "XAMS", "XBRU", "XLIS"}

    s = (validity_str or "max").strip().lower()

    if s == "seance":
        return "day", datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
    if s == "max":
        if mic in EURONEXT_MICS:
            return "end_of_year", f"{datetime.now().year}-12-31T00:00:00.000Z"
        return "revocation", None
    if s == "revocation":
        return "revocation", None
    if re.match(r"\d{2}/\d{2}/\d{4}$", s):
        d = datetime.strptime(s, "%d/%m/%Y")
        return "date", d.strftime("%Y-%m-%dT00:00:00.000Z")
    # Valeur brute (end_of_year, day…)
    if s == "end_of_year":
        if mic not in EURONEXT_MICS:
            return "revocation", None
        return "end_of_year", f"{datetime.now().year}-12-31T00:00:00.000Z"
    if s == "day":
        from datetime import datetime
        return "day", datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
    return s, None


def create_order(page, ticker: str, side: str, qty: int,
                 order_type: str = "market",
                 limit_price: float = None,
                 stop_price:  float = None,
                 validity:    str   = "max",
                 smart:       dict  = None) -> dict | None:
    """
    Crée un ordre sur BD — étape de validation (non envoyé).

    ticker     : ticker yfinance (ex: "TTE.PA", "ILMN")
    side       : "buy" | "sell"
    order_type : "market" | "limit" | "meta" (expert)
    limit_price: prix d'entrée pour achat limité ou Expert achat
    stop_price : seuil de déclenchement stop
    validity   : "seance" | "max" | "revocation" | "DD/MM/YYYY"
    smart      : dict SL/TP pour ordre Expert (généré par les helpers)
    """
    info = get_ticker_info(ticker)
    if not info:
        print(f"[BD Orders] Ticker inconnu : {ticker} — ajouter dans TICKER_MAP")
        return None

    validity_api, validity_date = parse_validity(validity, info["mic"])

    payload = {
        "login":           BD_LOGIN.upper(),
        "mic":             info["mic"],
        "ticker":          info["bd_ticker"],
        "currency":        info["currency"],
        "quantity":        str(qty),
        "portfolio":       BD_ACCOUNT,
        "type":            order_type,
        "side":            side,
        "validity":        validity_api,
        "validityDate":    validity_date,
        "settlement":      "cash",
        "limit":           limit_price,
        "stop":            stop_price,
        "position_effect": "open" if side == "buy" else "close",
        "globex":          False,
        "comment":         None,
        "brokerage":       None,
    }

    if smart:
        payload["nature"] = "smart"
        payload["smart"]  = smart
        # Payload Expert réel confirmé (capture réseau BD) : un Expert achat à cours
        # limité utilise type="limit" + limit=<prix> + nature="smart" (PAS type="meta",
        # qui déclenche "La limite ne doit pas être renseignée" → HTTP 400).
        # Le `type` et le `limit` sont fournis par l'appelant (helpers buy/sell).
        # position_effect="close" même côté achat (confirmé par le payload réel).
        payload["position_effect"] = "close"

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
                        stop_loss: float, take_profit: float,
                        validity: str = "max") -> dict | None:
    """
    Ordre Expert VENTE : protège une position existante avec SL+TP combinés.
    Appeler execute_strategy(page, order_id) pour confirmer l'envoi.
    """
    smart = {
        "strategy":    "take_profit",
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "variation":   None,
    }
    return create_order(
        page, ticker, side="sell", qty=qty,
        order_type="meta", smart=smart, validity=validity,
    )


def create_expert_buy_order(page, ticker: str, qty: int,
                            entry_price: float,
                            stop_loss: float, take_profit: float,
                            validity: str = "max") -> dict | None:
    """
    Ordre Expert ACHAT : entrée À COURS LIMITÉ (entry_price) + SL/TP activés dès
    l'exécution. Structure confirmée par capture réseau BD : type="limit" +
    limit=entry_price + nature="smart" + smart={strategy, stop_loss, take_profit}.
    Permet d'entrer en position ET de placer la protection en un seul ordre.
    Appeler execute_strategy(page, order_id) pour confirmer l'envoi.
    """
    smart = {
        "strategy":    "take_profit",
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "variation":   None,
    }
    return create_order(
        page, ticker, side="buy", qty=qty,
        order_type="limit", limit_price=entry_price,
        smart=smart, validity=validity,
    )


def debug_order_variants(page, ticker: str, send_fn=print) -> None:
    """
    Diagnostic /testordre : teste plusieurs variantes de payload /order/create
    (étape de VALIDATION uniquement — l'ordre n'est JAMAIS envoyé au marché,
    le brouillon expire de lui-même). Identifie ce que BD accepte sur ce titre.
    Utile pour les marchés où le payload Euronext échoue (ex: US "Missing arguments").
    """
    import prices
    q = prices.get_quote(ticker)
    price = q.get("price")
    if not price:
        send_fn(f"Cours indisponible pour {ticker}")
        return
    entry = round(price * 0.99, 2)
    sl    = round(price * 0.95, 2)
    tp    = round(price * 1.05, 2)
    smart = {"strategy": "take_profit", "stop_loss": sl, "take_profit": tp, "variation": None}
    info  = get_ticker_info(ticker)
    send_fn(f"🔬 Test /order/create {ticker} (mic {info['mic']}, {info['currency']}) "
            f"— qty 1, entrée {entry}, SL {sl}, TP {tp}\n"
            f"Aucun ordre ne sera envoyé au marché.")

    variants = [
        ("A: Expert limit+smart, validité revocation (payload actuel US)",
         dict(order_type="limit", limit_price=entry, smart=smart, validity="revocation")),
        ("B: Expert limit+smart, validité seance",
         dict(order_type="limit", limit_price=entry, smart=smart, validity="seance")),
        ("C: limit simple, validité revocation",
         dict(order_type="limit", limit_price=entry, smart=None, validity="revocation")),
        ("D: limit simple, validité seance",
         dict(order_type="limit", limit_price=entry, smart=None, validity="seance")),
        ("E: market simple, validité seance",
         dict(order_type="market", limit_price=None, smart=None, validity="seance")),
    ]
    results = []
    for label, kw in variants:
        try:
            res = create_order(page, ticker, side="buy", qty=1, **kw)
            if res:
                oid = res.get("id") or res.get("order_id", "?")
                results.append(f"✅ {label}\n   → accepté (brouillon {oid}, non envoyé)")
            else:
                data   = _last_raw.get("data", {}) or {}
                msg    = data.get("message", "?")
                fields = data.get("fields") or ""
                results.append(f"❌ {label}\n   → HTTP {_last_raw.get('status')} : {msg} {fields}")
        except Exception as e:
            results.append(f"⚠️ {label}\n   → exception : {e}")
    send_fn("RÉSULTATS\n\n" + "\n\n".join(results))


def format_order_summary(order_data: dict, ticker: str, side: str,
                         qty: int, order_type: str,
                         limit_price: float = None, stop_price: float = None,
                         validity: str = "max", sl: float = None, tp: float = None) -> str:
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

    # Libellé validité lisible
    validity_labels = {
        "seance":      "Séance",
        "max":         "Durée max",
        "revocation":  "Bonne pour annulation",
        "end_of_year": "Fin d'année",
        "day":         "Séance",
    }
    import re as _re
    validity_fr = validity_labels.get(validity.lower() if validity else "max", validity or "Durée max")
    if validity and _re.match(r"\d{2}/\d{2}/\d{4}", validity):
        validity_fr = f"Jusqu'au {validity}"

    lines = [
        f"{side_fr} {qty}x {ticker}",
        f"Type     : {type_fr}",
        f"Validité : {validity_fr}",
    ]
    if limit_price:
        lines.append(f"Entrée : {limit_price}{sym}")
    if sl:
        lines.append(f"SL     : {sl}{sym}")
    if tp:
        lines.append(f"TP     : {tp}{sym}")
    elif stop_price:
        lines.append(f"Seuil  : {stop_price}{sym}")
    if montant is not None:
        lines.append(f"Montant prévu : {montant}{sym}  (frais BD ajoutés à l'exécution)")
    else:
        lines.append(f"Montant prévu : —")
    lines.append(f"Ref BD : {order_id}")

    return "\n".join(lines)
