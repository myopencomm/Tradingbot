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
    # AMGN XNGS confirmé par capture réseau du site BD (07/2026) : le NASDAQ
    # est identifié par XNGS (Global Select), PAS XNAS — un mic inconnu de BD
    # donne un HTTP 400 "Missing arguments" générique sur /order/create.
    "AMGN":                   {"bd_ticker": "AMGN",   "mic": "XNGS", "currency": "USD"},
    "ILMN":                   {"bd_ticker": "ILMN",   "mic": "XNGS", "currency": "USD"},
    # Ajouter au fur et à mesure lors des premiers passages d'ordres
}

# MICs candidats pour un titre US : résolus dynamiquement via
# /order/context-validate (endpoint confirmé par capture réseau).
US_MIC_CANDIDATES = ("XNGS", "XNYS", "XNAS")


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

    # US sans suffixe — MIC inconnu a priori (XNGS=NASDAQ, XNYS=NYSE) :
    # create_order le résout dynamiquement via /order/context-validate.
    return {"bd_ticker": t, "mic": "XNGS", "currency": "USD", "us_unresolved": True}


def register_ticker(yf_ticker: str, bd_ticker: str, mic: str, currency: str):
    """Enregistre dynamiquement un nouveau ticker dans le mapping."""
    TICKER_MAP[yf_ticker.upper()] = {
        "bd_ticker": bd_ticker,
        "mic":       mic,
        "currency":  currency,
    }


def resolve_us_mic(page, ticker: str, currency: str = "USD") -> str | None:
    """
    Détermine le MIC BD réel d'un titre US via /order/context-validate
    (endpoint + payload confirmés par capture réseau du site BD).
    Un MIC erroné donne HTTP 400 "Missing arguments" sur /order/create.
    Le résultat est mémorisé dans TICKER_MAP pour la session.
    """
    for mic in US_MIC_CANDIDATES:
        r = _api_post(page, "/order/context-validate",
                      {"instrument": {"mic": mic, "ticker": ticker, "currency": currency}})
        if r and r.get("ok"):
            print(f"[BD Orders] MIC résolu : {ticker} → {mic}")
            register_ticker(ticker, ticker, mic, currency)
            return mic
    print(f"[BD Orders] MIC introuvable pour {ticker} (candidats {US_MIC_CANDIDATES})")
    return None


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

    # "revocation" avec date de FIN DE MOIS — payload réel du site BD confirmé
    # par capture réseau (07/2026) : validityDate n'est jamais null.
    import calendar
    now = datetime.now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    end_of_month = f"{now.year}-{now.month:02d}-{last_day:02d}T00:00:00.000Z"

    if s == "seance":
        return "day", now.strftime("%Y-%m-%dT00:00:00.000Z")
    if s == "max":
        if mic in EURONEXT_MICS:
            return "end_of_year", f"{now.year}-12-31T00:00:00.000Z"
        return "revocation", end_of_month
    if s == "revocation":
        return "revocation", end_of_month
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


def _round_to_tick(price: float, tick: float, direction: str) -> float:
    """Arrondit un prix au pas de cotation. direction: 'up' | 'down' | 'nearest'.

    Délègue à `ticks` : la règle vit à UN endroit, partagé avec l'analyse qui
    arrondit désormais à la source. Deux implémentations de l'arrondi, c'est
    la garantie qu'elles divergeront un jour.
    """
    import ticks
    return ticks.round_to_tick(price, tick, direction)


def _extract_tick(raw: dict) -> float | None:
    """Extrait le pas de cotation depuis une erreur BD.
    Ex: fields.smart = ["Le pas de cotation pour cette limite est 0.02."]"""
    import re
    try:
        txt = json.dumps(raw.get("data", {}) or {}, ensure_ascii=False)
        m = re.search(r"pas de cotation[^\d]*(\d+(?:[.,]\d+)?)", txt, re.I)
        if m:
            tick = float(m.group(1).replace(",", "."))
            if tick > 0:
                return tick
    except Exception:
        pass
    return None


def create_order(page, ticker: str, side: str, qty: int,
                 order_type: str = "market",
                 limit_price: float = None,
                 stop_price:  float = None,
                 validity:    str   = "max",
                 smart:       dict  = None,
                 _tick_retry: int   = 0) -> dict | None:
    """
    Crée un ordre sur BD — étape de validation (non envoyé).

    ticker     : ticker yfinance (ex: "TTE.PA", "ILMN")
    side       : "buy" | "sell"
    order_type : "market" | "limit" | "meta" (expert)
    limit_price: prix d'entrée pour achat limité ou Expert achat
    stop_price : seuil de déclenchement stop
    validity   : "seance" | "max" | "revocation" | "DD/MM/YYYY"
    smart      : dict SL/TP pour ordre Expert (généré par les helpers)

    Si BD rejette pour pas de cotation ("Le pas de cotation pour cette limite
    est 0.02"), les prix sont ré-arrondis au pas et l'ordre retenté (max 3×).
    Règle conservatrice : SL arrondi VERS LE HAUT, TP VERS LE BAS, limite
    d'achat vers le bas / de vente vers le haut. Les prix finalement acceptés
    sont retournés dans data["_adjusted"].
    """
    info = get_ticker_info(ticker)
    if not info:
        print(f"[BD Orders] Ticker inconnu : {ticker} — ajouter dans TICKER_MAP")
        return None

    # Titre US jamais tradé : résout le vrai MIC (XNGS/XNYS) avant l'ordre —
    # un MIC erroné donne "Missing arguments".
    if info.get("us_unresolved"):
        resolved = resolve_us_mic(page, info["bd_ticker"], info["currency"])
        if resolved:
            info = TICKER_MAP[ticker.upper()]

    validity_api, validity_date = parse_validity(validity, info["mic"])

    # ── Pas de cotation US, appliqué AVANT l'envoi ──────────────────────────
    # Les actions américaines se traitent au cent (SEC Rule 612 : 0.01 $ au
    # dessus d'un dollar). Le bot envoyait des limites à trois décimales et
    # comptait sur BD pour le corriger — BD renvoie en effet un 400 « Le pas de
    # cotation pour cette limite est 0.01 », et l'ordre est retenté arrondi.
    #
    # Mais cette validation de BD est INCONSTANTE. Le 18/08/2026, RTX à
    # 224.431 $ est passé sans 400 : `create_order` a répondu 200, puis le NYSE
    # a refusé l'ordre. Le carnet légal le dit mot pour mot — « Achat rejeté
    # marché » — et ni l'app ni l'API ne donnent le motif. JNJ, la veille, avait
    # eu le 400 et s'était exécuté sans problème après arrondi.
    #
    # On n'attend donc plus que BD s'en aperçoive : les prix US sont arrondis
    # ici. Le retry sur 400 reste en place pour les autres places, dont le pas
    # dépend du cours et que BD, lui, signale de façon fiable.
    if info["currency"] == "USD":
        import ticks
        tick_us = ticks.tick_for(limit_price, "USD")
        if limit_price is not None:
            arrondi = _round_to_tick(limit_price, tick_us,
                                     "down" if side == "buy" else "up")
            if arrondi != limit_price:
                print(f"[BD Orders] {ticker} : limite {limit_price} → {arrondi} "
                      f"(pas US {tick_us})")
                limit_price = arrondi
        if stop_price is not None:
            stop_price = _round_to_tick(stop_price, tick_us, "up")
        if smart:
            smart = dict(smart)
            if smart.get("stop_loss") is not None:
                smart["stop_loss"] = _round_to_tick(smart["stop_loss"], tick_us, "up")
            if smart.get("take_profit") is not None:
                smart["take_profit"] = _round_to_tick(smart["take_profit"], tick_us, "down")

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
        # ── Retry pas de cotation : BD indique le tick dans son erreur ───────
        tick = _extract_tick(result)
        if tick and _tick_retry < 3:
            new_limit, new_stop, new_smart = limit_price, stop_price, smart
            changed = False
            if limit_price is not None:
                nl = _round_to_tick(limit_price, tick, "down" if side == "buy" else "up")
                changed |= nl != limit_price
                new_limit = nl
            if stop_price is not None:
                ns = _round_to_tick(stop_price, tick, "nearest")
                changed |= ns != stop_price
                new_stop = ns
            if smart:
                new_smart = dict(smart)
                sl_v = smart.get("stop_loss")
                tp_v = smart.get("take_profit")
                if sl_v:
                    new_smart["stop_loss"] = _round_to_tick(sl_v, tick, "up")
                if tp_v:
                    new_smart["take_profit"] = _round_to_tick(tp_v, tick, "down")
                changed |= new_smart != smart
            if changed:
                print(f"[BD Orders] pas de cotation {tick} — retry "
                      f"limit={new_limit} stop={new_stop} smart={new_smart}")
                return create_order(page, ticker, side, qty, order_type,
                                    new_limit, new_stop, validity, new_smart,
                                    _tick_retry=_tick_retry + 1)
        print(f"[BD Orders] create_order HTTP {result.get('status')} : {result.get('data')}")
        return None

    data = result.get("data")
    # Log complet pour découvrir la structure réelle de la réponse BD
    print(f"[BD Orders] create_order OK : {json.dumps(data)[:600]}")
    # Prix finalement acceptés (≠ demandés si retry pas de cotation)
    if isinstance(data, dict) and _tick_retry > 0:
        data["_adjusted"] = {
            "limit":       limit_price,
            "stop":        stop_price,
            "stop_loss":   (smart or {}).get("stop_loss"),
            "take_profit": (smart or {}).get("take_profit"),
        }
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


BD_LEGACY_DETAIL_URL = "https://www.boursedirect.fr/priv/new/detailOrdre.php"


def cancel_legacy_order(page, ref: str, refbo: str) -> bool:
    """
    Annule un ordre du CARNET LEGACY (ordres-en-carnet.php).

    Confirmé par inspection manuelle (28/07/2026) : l'annulation est un simple
    GET, sans formulaire ni POST :
        detailOrdre.php?ref=<ref>&refbo=<refbo>&fn=1&isOpcvm=0
    (`fn=1` déclenche l'annulation ; `num=1` dans le lien du carnet ouvre
    seulement la popup de détail qui porte ce lien.)

    C'est la SEULE voie qui atteigne les protections rattachées à un ordre
    d'achat exécuté — l'API moderne /order/cancel ne connaît pas ces ordres.

    Requête via le contexte du navigateur : mêmes cookies de session, sans
    perturber la page courante.

    ⚠️ Le retour HTTP 200 ne prouve PAS l'annulation (la page legacy répond 200
    même sur erreur). L'appelant DOIT relire le carnet pour vérifier.
    """
    url = f"{BD_LEGACY_DETAIL_URL}?ref={ref}&refbo={refbo}&fn=1&isOpcvm=0"
    try:
        resp = page.request.get(url, timeout=20000)
        print(f"[BD Legacy cancel] ref={ref} → HTTP {resp.status}")
        return bool(resp.ok)
    except Exception as e:
        print(f"[BD Legacy cancel] ref={ref} : {e}")
        return False


def cancel_order(page, order_id: str) -> dict | None:
    """
    Annule un ordre en cours sur BD (Take Profit, Stop Loss, ordre limite...).

    PAYLOAD CONFIRMÉ PAR CAPTURE RÉSEAU (27/07/2026, annulation manuelle sur
    /fr/page/ordres-en-carnet → HTTP 200) : le site envoie UNIQUEMENT
    {"order_id": "..."} — ni `login`, ni `csrf`. Les envoyer quand même
    faisait partie du 403 « une erreur est intervenue » côté bot.

    ⚠️ L'`order_id` doit être celui de l'ordre ENFANT (le Stop Loss ou le Take
    Profit pris séparément, lisibles sur la page carnet d'ordres), PAS celui du
    bloc consolidé de la page portefeuille — ce dernier porte l'id de l'ordre
    d'ACHAT parent déjà exécuté, qui n'est pas annulable (403 légitime).
    """
    payload = {"order_id": order_id}
    result = _api_post(page, "/order/cancel", payload)
    if not result:
        return None
    if not result.get("ok"):
        print(f"[BD Orders] cancel_order HTTP {result.get('status')} : {result.get('data')}")
        return None
    return result.get("data")


def confirm_order_auto(page, order_id: str, is_buy_with_smart: bool) -> dict | None:
    """
    Confirme un ordre créé — ACTION IRRÉVERSIBLE.

    Un Expert ACHAT (type="limit" + smart) est un ordre LIMITE parent dont la
    stratégie SL/TP est portée par des ordres enfants ("children" dans la
    réponse create) : il se confirme via /order/send comme un ordre limite
    classique. /order/execute/strategy renvoie HTTP 500 pour lui (constaté en
    réel sur EDEN.PA) — cet endpoint ne vaut que pour les Expert VENTE (meta).

    En cas d'échec de l'endpoint attendu, tente l'autre en secours (sans risque
    de double exécution : un ordre déjà envoyé n'est plus confirmable).
    """
    primary, secondary = ((send_order, execute_strategy) if is_buy_with_smart
                          else (execute_strategy, send_order))
    res = primary(page, order_id)
    if res:
        return res
    print("[BD Orders] confirmation primaire échouée — tentative endpoint alternatif")
    return secondary(page, order_id)


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
