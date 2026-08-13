"""
Suivi des coûts API IA — pour un bilan HONNÊTE du bot.

Chaque appel Anthropic enregistre ses tokens réels (renvoyés par l'API) dans
api_costs.json ; le coût est calculé au tarif du modèle et intégré au P&L
(/stats + dashboard). Les frais de courtage sont déjà déduits par trade —
les coûts IA sont la 2e charge réelle, globale, jusqu'ici invisible.

Amorce : 5.66$ constatés sur la console Anthropic du 01 au 17/07/2026 (CSV
fourni par l'utilisateur). L'usage antérieur (mai-juin) est inconnu et n'est
PAS estimé — on ne comptabilise que ce qui est mesuré.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytz

PARIS = pytz.timezone("Europe/Paris")
COSTS_PATH = Path(__file__).parent / "api_costs.json"
_lock = threading.Lock()

# ── Tarifs $/million de tokens (input, output) ────────────────────────────────
# Vérifiés le 04/08/2026. L'ORDRE COMPTE : le premier motif contenu dans le nom
# du modèle gagne, donc du plus spécifique au plus général (« flash-lite » avant
# « flash », sinon un Flash-Lite serait facturé au tarif Flash).
#
# Anthropic : tarifs officiels de la doc API.
#   ⚠️ Opus était codé à 15/75 — c'est l'ancien tarif Opus 3/4. Les Opus actuels
#   (4.6 → 5) sont à 5/25, soit une SURESTIMATION de 3× si le bot y repasse.
# Gemini : tarifs publics Google. « flash » nu vaut 1.50/7.50 (Flash courant) et
#   non 0.30/2.50 — ce dernier est le tarif Flash-LITE. Le bot tournant à 100%
#   sur `gemini-flash-latest` depuis le 20/07, ce raccourci sous-estimait la
#   facture d'un facteur 5 en entrée.
PRICING = (
    # Anthropic
    ("haiku",       (1.0,  5.0)),
    ("sonnet",      (3.0, 15.0)),
    ("fable",      (10.0, 50.0)),
    ("mythos",     (10.0, 50.0)),
    ("opus",        (5.0, 25.0)),
    # Google — le plus spécifique d'abord
    ("flash-lite",  (0.3,  2.5)),
    ("flash",       (1.5,  7.5)),
    ("gemini",      (1.25, 10.0)),   # Gemini Pro
)
# Modèle inconnu → tarif VOLONTAIREMENT haut. Sous-estimer une facture qu'on ne
# sait pas lire donne un bilan flatteur et faux ; la surestimer se voit.
_DEFAULT_PRICING = (10.0, 50.0)

SEED = {"seed_usd": 5.66,
        "seed_note": "console Anthropic 01-17/07/2026 (CSV) ; usage mai-juin inconnu, non estimé"}


def _load() -> dict:
    try:
        return json.loads(COSTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {**SEED, "daily": {}}


def rates(model: str) -> tuple[float, float]:
    """Tarif ($/M in, $/M out) du modèle, ou le défaut haut s'il est inconnu."""
    m = (model or "").lower()
    for key, r in PRICING:
        if key in m:
            return r
    if m:
        print(f"[api costs] modèle inconnu « {model} » — facturé au tarif "
              f"prudent {_DEFAULT_PRICING[0]}/{_DEFAULT_PRICING[1]} $/M")
    return _DEFAULT_PRICING


# Multiplicateurs des jetons de CACHE, appliqués au tarif d'entrée du modèle.
# Écrire dans le cache coûte plus cher qu'un jeton normal, le relire coûte
# presque rien — c'est tout l'intérêt du cache, et l'ignorer fausse la facture
# dans les deux sens selon le trafic.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT  = 0.10


def _price(model: str, input_tokens: int, output_tokens: int,
           cache_write: int = 0, cache_read: int = 0) -> float:
    pin, pout = rates(model)
    return (input_tokens / 1e6 * pin
            + cache_write / 1e6 * pin * CACHE_WRITE_MULT
            + cache_read / 1e6 * pin * CACHE_READ_MULT
            + output_tokens / 1e6 * pout)


def record(model: str, input_tokens: int, output_tokens: int,
           cache_write: int = 0, cache_read: int = 0) -> None:
    """Enregistre un appel API.

    `input_tokens` = jetons d'entrée au TARIF PLEIN, cache exclu. Les deux
    compteurs de cache sont facturés à part (voir les multiplicateurs) — les
    appelants doivent donc les retrancher de l'entrée quand leur SDK les y
    inclut, ce que fait Gemini et pas Anthropic.

    Les quatre compteurs et la ventilation PAR MODÈLE sont persistés : c'est
    `models` que lit `get_costs()` pour dire QUI a réellement répondu.

    ⚠️ Cette fonction n'a rien enregistré du 04/08 au 13/08/2026. Les deux
    appelants lui passaient déjà cinq arguments alors qu'elle n'en acceptait
    que trois : chaque appel levait un `TypeError` AVANT d'entrer ici, donc son
    propre `except` ne pouvait rien rattraper — et les appelants avalaient
    l'erreur par un `except: pass`. Neuf jours d'appels IA facturés nulle part,
    et `top_model` resté vide alors que c'est précisément l'indicateur qui
    devait révéler que le bot tournait sur Gemini depuis le 20/07.
    """
    try:
        cost = _price(model, input_tokens, output_tokens, cache_write, cache_read)
        day = datetime.now(PARIS).strftime("%Y-%m-%d")
        with _lock:
            data = _load()
            d = data.setdefault("daily", {}).setdefault(
                day, {"cost_usd": 0.0, "input": 0, "output": 0, "calls": 0})
            d["cost_usd"] = round(d["cost_usd"] + cost, 6)
            d["input"]   += int(input_tokens)
            d["output"]  += int(output_tokens)
            d["calls"]   += 1
            d["cache_write"] = d.get("cache_write", 0) + int(cache_write)
            d["cache_read"]  = d.get("cache_read", 0) + int(cache_read)

            m = d.setdefault("models", {}).setdefault(
                model or "?", {"cost_usd": 0.0, "calls": 0})
            m["cost_usd"] = round(m["cost_usd"] + cost, 6)
            m["calls"]   += 1

            tmp = COSTS_PATH.with_suffix(COSTS_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, COSTS_PATH)
    except Exception as e:
        print(f"[api costs] record({model}) : {e}")


def get_costs() -> dict:
    """Totaux en USD et EUR (fx live, fallback 0.92) : total, mois en cours, jour."""
    data = _load()
    daily = data.get("daily", {})
    now = datetime.now(PARIS)
    month_prefix = now.strftime("%Y-%m")
    today = now.strftime("%Y-%m-%d")

    tracked = sum(d.get("cost_usd", 0.0) for d in daily.values())
    total_usd = data.get("seed_usd", 0.0) + tracked
    month_usd = sum(d.get("cost_usd", 0.0) for day, d in daily.items()
                    if day.startswith(month_prefix))
    # l'amorce (01-17/07/2026) appartient à juillet 2026
    if month_prefix == "2026-07":
        month_usd += data.get("seed_usd", 0.0)
    today_usd = daily.get(today, {}).get("cost_usd", 0.0)

    try:
        import prices
        fx = prices.fx_to_eur("USD") or 0.92
    except Exception:
        fx = 0.92
    # Répartition par modèle sur les 30 derniers jours : sert à dire QUI a
    # réellement répondu. Le bot a basculé sur le fallback Gemini le 20/07 et
    # n'a plus jamais servi un appel Anthropic — invisible jusqu'ici.
    from datetime import timedelta
    since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    by_model: dict[str, dict] = {}
    for day, d in daily.items():
        if day < since:
            continue
        for name, m in (d.get("models") or {}).items():
            agg = by_model.setdefault(name, {"cost_usd": 0.0, "calls": 0})
            agg["cost_usd"] = round(agg["cost_usd"] + m.get("cost_usd", 0.0), 6)
            agg["calls"] += m.get("calls", 0)
    top = max(by_model.items(), key=lambda kv: kv[1]["calls"], default=(None, None))

    return {
        "by_model":  by_model,
        "top_model": top[0],
        "total_usd": round(total_usd, 2),
        "total_eur": round(total_usd * fx, 2),
        "month_usd": round(month_usd, 2),
        "month_eur": round(month_usd * fx, 2),
        "today_usd": round(today_usd, 4),
        "daily":     {d: v.get("cost_usd", 0.0) for d, v in daily.items()},
        "calls":     sum(d.get("calls", 0) for d in daily.values()),
        "seed_note": data.get("seed_note", ""),
    }
