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
BD_ACCOUNT  = "<BD_ACCOUNT>"  # CTO Compte Titre Ordinaire

# Token CSRF statique extrait du bundle ordertrade.bundle.js
_CSRF = "OWY4NmQwODE4ODRjN2Q2NTlhMmZlYWEwYzU1YWQwMTVhM2JmNGYxYjJiMGI4MjJjZDE1ZDMGYwMGEwOA=="

# ── Mapping yfinance ticker → données BD ─────────────────────────────────────
# mic     : Market Identifier Code (XPAR=Euronext Paris, XNAS=NASDAQ, XNYS=NYSE...)
# bd_ticker : symbole interne BD (préfixe E: pour Euronext)
# Découvert depuis portefeuille-TR.php et fiche-valeur.php?val=...&pl=...
TICKER_MAP: dict[str, dict] = {
    "EXENS.PA":               {"bd_ticker": "E:EXENS",  "mic": "XPAR", "currency": "EUR"},
    "GNFT.PA":                {"bd_ticker": "E:GNFT",   "mic": "XPAR", "currency": "EUR"},
    "LBIRD.PA":               {"bd_ticker": "E:LBIRD",  "mic": "XPAR", "currency": "EUR"},
    "MCPHY.PA":               {"bd_ticker": "E:MCPHY",  "mic": "XPAR", "currency": "EUR"},
    "FR0011799907.PA":        {"bd_ticker": "E:GVN",    "mic": "XPAR", "currency": "EUR"},
    "ILMN":                   {"bd_ticker": "ILMN",     "mic": "XNAS", "currency": "USD"},
    # Ajouter au fur et à mesure lors des premiers passages d'ordres
}


def get_ticker_info(yf_ticker: str) -> dict | None:
    """
    Retourne les données BD pour un ticker yfinance.
    Cherche d'abord dans TICKER_MAP (override manuel),
    puis applique les règles de conversion automatique.

    Règles de conversion yfinance → BD :
      .PA  → E:<base>  MIC=XPAR  EUR   (Euronext Paris)
      .BR  → E:<base>  MIC=XBRU  EUR   (Euronext Bruxelles)
      .AS  → E:<base>  MIC=XAMS  EUR   (Euronext Amsterdam)
      .L   → <base>    MIC=XLON  GBP   (London Stock Exchange)
      .DE  → <base>    MIC=XETR  EUR   (Xetra Frankfurt)
      (rien) → <base>  MIC=XNAS  USD   (NASDAQ — défaut US)
    """
    t = yf_ticker.upper()
    if t in TICKER_MAP:
        return TICKER_MAP[t]

    # Conversion automatique par suffixe
    for suffix, bd_prefix, mic, currency in [
        (".PA", "E:", "XPAR", "EUR"),
        (".BR", "E:", "XBRU", "EUR"),
        (".AS", "E:", "XAMS", "EUR"),
        (".L",  "",   "XLON", "GBP"),
        (".DE", "",   "XETR", "EUR"),
    ]:
        if t.endswith(suffix):
            base = t[: -len(suffix)]
            return {"bd_ticker": f"{bd_prefix}{base}", "mic": mic, "currency": currency}

    # US stock sans suffixe — NASDAQ par défaut
    # Si c'est un NYSE, ajouter manuellement dans TICKER_MAP avec "mic": "XNYS"
    return {"bd_ticker": t, "mic": "XNAS", "currency": "USD"}


def register_ticker(yf_ticker: str, bd_ticker: str, mic: str, currency: str):
    """Enregistre dynamiquement un nouveau ticker dans le mapping."""
    TICKER_MAP[yf_ticker.upper()] = {
        "bd_ticker": bd_ticker,
        "mic":       mic,
        "currency":  currency,
    }


# ── Appel API interne (via fetch Playwright avec cookies de session) ──────────

def _api_post(page, endpoint: str, payload: dict) -> dict | None:
    """
    POST sur hub/trading via fetch() dans le contexte Playwright.
    Les cookies de session BD sont transmis automatiquement.
    """
    url         = BD_API_BASE + endpoint
    payload_str = json.dumps(payload)
    result = page.evaluate(f"""async () => {{
        try {{
            const resp = await fetch({json.dumps(url)}, {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({payload_str}),
                credentials: 'include'
            }});
            const text = await resp.text();
            let data;
            try {{ data = JSON.parse(text); }} catch(e) {{ data = {{raw: text}}; }}
            return {{ok: resp.ok, status: resp.status, data}};
        }} catch(e) {{
            return {{ok: false, error: e.toString()}};
        }}
    }}""")
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

    payload = {
        "login":          BD_LOGIN,
        "mic":            info["mic"],
        "ticker":         info["bd_ticker"],
        "currency":       info["currency"],
        "quantity":       qty,
        "portfolio":      BD_ACCOUNT,
        "type":           order_type,
        "side":           side,
        "validity":       validity,
        "validityDate":   None,
        "settlement":     "cash",
        "limit":          limit_price,
        "stop":           stop_price,
        "position_effect": None,
        "globex":         None,
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

    return result.get("data")


# ── Étape 2 : confirmer l'envoi (irréversible) ───────────────────────────────

def send_order(page, order_id: str) -> dict | None:
    """
    Envoie l'ordre au marché — ACTION IRRÉVERSIBLE.
    Appeler UNIQUEMENT après double confirmation de l'utilisateur.

    order_id : identifiant retourné par create_order()
    """
    payload = {
        "order_id": order_id,
        "login":    BD_LOGIN,
        "csrf":     _CSRF,
    }
    result = _api_post(page, "/order/send", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] send_order HTTP {result.get('status')} : {result.get('data')}")
        return None
    return result.get("data")


def execute_strategy(page, order_id: str) -> dict | None:
    """
    Exécute un ordre Expert (SL+TP combiné) — ACTION IRRÉVERSIBLE.
    Utiliser pour les ordres de type 'smart'/'meta' après create_order().
    """
    payload = {
        "order_id": order_id,
        "login":    BD_LOGIN,
        "csrf":     _CSRF,
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
    fees = order_data.get("fees", {}).get("total", {}).get("amount", "?")
    order_id = order_data.get("id") or order_data.get("order_id", "?")

    side_fr  = "ACHAT" if side == "buy" else "VENTE"
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
        lines.append(f"Limite : {limit_price}€")
    if stop_price:
        lines.append(f"Seuil  : {stop_price}€")
    lines.append(f"Frais  : {fees}€")
    lines.append(f"Ref BD : {order_id}")

    return "\n".join(lines)
