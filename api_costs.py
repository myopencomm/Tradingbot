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
import threading
from datetime import datetime
from pathlib import Path

import pytz

PARIS = pytz.timezone("Europe/Paris")
COSTS_PATH = Path(__file__).parent / "api_costs.json"
_lock = threading.Lock()

# Tarifs $/million de tokens (input, output) — les images vision comptent
# dans l'input. Correspondance par sous-chaîne du nom de modèle.
PRICING = {
    "haiku":  (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus":   (15.0, 75.0),
}
_DEFAULT_PRICING = (3.0, 15.0)  # inconnu → tarif Sonnet (prudent)

SEED = {"seed_usd": 5.66,
        "seed_note": "console Anthropic 01-17/07/2026 (CSV) ; usage mai-juin inconnu, non estimé"}


def _load() -> dict:
    try:
        return json.loads(COSTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {**SEED, "daily": {}}


def _price(model: str, input_tokens: int, output_tokens: int) -> float:
    m = (model or "").lower()
    pin, pout = _DEFAULT_PRICING
    for key, (i, o) in PRICING.items():
        if key in m:
            pin, pout = i, o
            break
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    """Enregistre un appel API (best-effort : ne lève jamais)."""
    try:
        cost = _price(model, input_tokens, output_tokens)
        day = datetime.now(PARIS).strftime("%Y-%m-%d")
        with _lock:
            data = _load()
            d = data.setdefault("daily", {}).setdefault(
                day, {"cost_usd": 0.0, "input": 0, "output": 0, "calls": 0})
            d["cost_usd"] = round(d["cost_usd"] + cost, 6)
            d["input"]   += int(input_tokens)
            d["output"]  += int(output_tokens)
            d["calls"]   += 1
            COSTS_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[api costs] record: {e}")


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
    return {
        "total_usd": round(total_usd, 2),
        "total_eur": round(total_usd * fx, 2),
        "month_usd": round(month_usd, 2),
        "month_eur": round(month_usd * fx, 2),
        "today_usd": round(today_usd, 4),
        "calls":     sum(d.get("calls", 0) for d in daily.values()),
        "seed_note": data.get("seed_note", ""),
    }
