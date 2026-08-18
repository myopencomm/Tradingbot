"""
Valorisation d'une position — UN calcul, N rendus.

Le même travail (cours retenu, provenance, variation, P&L, devise, conversion
en euros, détection de perf aberrante, diagnostic d'absence de cours) était
réécrit dans CINQ endroits : `/status`, le STATUS planifié de `monitor`, le
snapshot envoyé à l'IA dans `analysis`, le dashboard, et `stats`.

Ils avaient déjà divergé, et pas seulement sur la forme :
**le snapshot IA ne portait NI `protected` NI `pending_sl`.** Le bloc annoncé à
l'IA comme « SOURCE DE VÉRITÉ » présentait donc les SL/TP comme des faits, sans
jamais dire qu'aucun ordre ne les portait sur BD. Du 31/07 au 05/08, pendant que
BAC était réellement à nu, le briefing matinal a raisonné chaque jour comme s'il
était protégé.

Ce module ne rend RIEN. Chaque appelant garde son formatage (Telegram, prompt,
web, texte) et ne fait plus que lire des champs déjà calculés — c'est le calcul
qui est unique, pas l'affichage.
"""
import market
import portfolio
import prices

# Au-delà de ce écart, une perf sur un titre en devise trahit presque toujours
# un PRU saisi dans la mauvaise devise (et non une vraie performance).
PERF_ABERRANTE_PCT = 80


def view(name: str, cfg: dict, quote: dict | None = None) -> dict:
    """Tout ce qu'il faut savoir pour AFFICHER une position.

    `quote` évite de refaire l'appel quand l'appelant l'a déjà (boucles qui
    utilisent aussi les techniques ou le range intraday).

    Champs retournés
    ----------------
    name, ticker, qty, entry              identité et PRU en devise de cotation
    price, currency, sym                  cours retenu
    source                                provenance ('yf'|'bd'|'yf_stale') —
                                          diagnostic, pas pour l'utilisateur
    stale, note                           le cours est-il périmé, et pourquoi | ''
    chg_pct, pnl                          en devise de COTATION
    entry_eur, pnl_eur, chg_eur           en EUROS (dashboard, totaux)
    sl, tp                                seuils mémorisés
    hold, autonomous                      nature de la position
    protected                             False = AUCUN ordre SL/TP actif sur BD
    trailable                             False = protection non remontable
    pending_sl                            SL calculé mais PAS posé sur BD
    aberrant                              perf incohérente (PRU mal saisi ?)
    estimated                             P&L déduit du PRU, sans cours
    problem                               (code, message) si aucun cours
    """
    q = quote if quote is not None else prices.get_quote(cfg.get("ticker", ""))
    best = portfolio.best_price(cfg, q)

    price    = best["price"]
    currency = best["currency"] or market.currency(cfg.get("ticker", ""))
    entry    = cfg.get("entry_price") or 0
    qty      = cfg.get("qty") or 0
    fx       = prices.fx_to_eur(currency)

    # PRU en euros : la valeur BRUTE de Bourse Direct quand on l'a (c'est SA
    # référence, frais inclus et sans erreur de conversion) ; sinon le PRU en
    # devise de cotation ramené en euros au taux du jour.
    _eur      = entry * fx
    entry_eur = cfg.get("bd_pru_raw") or round(_eur, 2 if _eur >= 1 else 4)

    chg_pct = pnl = pnl_eur = chg_eur = None
    estimated = False

    if price and entry:
        chg_pct = round((price - entry) / entry * 100, 2)
        brut    = (price - entry) * qty          # jamais arrondi avant conversion :
        pnl     = round(brut, 2)                 # un double arrondi décalait le
        pnl_eur = round(brut * fx, 2)            # P&L euro d'un centime
        chg_eur = chg_pct
        # Le relevé BD chiffre directement en euros les titres que yfinance ne
        # cote plus : plus fiable qu'une conversion sur un cours mort.
        if best["source"] == "bd" and cfg.get("bd_pnl_eur") is not None:
            pnl_eur = cfg["bd_pnl_eur"]
            chg_eur = (round(pnl_eur / (entry_eur * qty) * 100, 2)
                       if entry_eur and qty else chg_pct)
    elif cfg.get("worthless") and entry_eur and qty:
        # Titre acté sans valeur (faillite, cotation suspendue définitive) : le
        # PRU suffit à chiffrer la perte, aucun cours n'est nécessaire. Marqué
        # « estimé » — c'est un calcul, pas un relevé (il ignore un éventuel
        # résidu, 0.26 € sur GVN).
        pnl_eur   = -round(entry_eur * qty, 2)
        chg_eur   = chg_pct = -100.0
        estimated = True

    aberrant = bool(currency != "EUR" and chg_pct is not None
                    and abs(chg_pct) > PERF_ABERRANTE_PCT)

    pending = cfg.get("pending_sl")
    if pending and pending <= (cfg.get("target_low") or 0):
        pending = None      # déjà posé, ou dépassé par le stop actif

    return {
        "name":       name,
        "ticker":     cfg.get("ticker", ""),
        "qty":        qty,
        "entry":      entry,
        "price":      price,
        "currency":   currency,
        "sym":        market.symbol(currency),
        # `source` reste pour le diagnostic ; `stale` est ce sur quoi les
        # rendus décident, parce que c'est la question du lecteur : ce chiffre
        # est-il à jour ? La provenance ne l'est pas.
        "source":     best["source"],
        "stale":      best["stale"],
        "note":       best["note"],
        "as_of":      best["as_of"],
        "chg_pct":    chg_pct,
        "pnl":        pnl,
        "entry_eur":  entry_eur,
        "pnl_eur":    pnl_eur,
        "chg_eur":    chg_eur,
        "pru_bd":     bool(cfg.get("bd_pru_raw")),
        "sl":         cfg.get("target_low"),
        "tp":         cfg.get("target_high"),
        "hold":       bool(cfg.get("hold")),
        "autonomous": bool(cfg.get("autonomous")),
        # `is False` et non `not ...` : None = jamais vérifié par un sync, ce
        # qui n'est PAS la même chose que « vérifié, aucune protection ».
        "protected":  cfg.get("protected"),
        "trailable":  cfg.get("trailable"),
        "pending_sl": pending,
        "aberrant":   aberrant,
        "estimated":  estimated,
        "problem":    None if price else portfolio.quote_problem(cfg, q),
    }


def views(positions: dict, quotes: dict | None = None) -> list[dict]:
    """`view()` sur tout un portefeuille, dans l'ordre d'insertion."""
    quotes = quotes or {}
    return [view(n, c, quotes.get(n)) for n, c in positions.items()]


# ─── Fragments d'affichage partagés ─────────────────────────────────────────
# Ce qui doit se DIRE pareil partout : une protection absente et un stop
# calculé mais non posé. Le formatage reste libre (indentation, emoji), le
# fond ne l'est plus.

def alerte_protection(v: dict, indent: str = "  ") -> str:
    """Avertissement « seuils non protecteurs », ou chaîne vide."""
    if v["protected"] is not False:
        return ""
    return (f"\n{indent}🚨 AUCUN ordre SL/TP actif sur BD — ces seuils ne "
            f"protègent RIEN\n{indent}→ /ordre vendre {v['ticker']} {v['qty']} "
            f"expert {v['sl']} {v['tp']}")


def alerte_stop_en_attente(v: dict, indent: str = "  ") -> str:
    """Avertissement « SL calculé mais pas posé sur BD », ou chaîne vide."""
    if not v["pending_sl"]:
        return ""
    return (f"\n{indent}⏳ SL {v['pending_sl']} calculé mais PAS posé sur BD — "
            f"le stop actif reste {v['sl']}")
