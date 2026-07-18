import json
from datetime import datetime
import pytz
import portfolio
import prices
from config import HISTORY_PATH

PARIS = pytz.timezone("Europe/Paris")


def load_history() -> dict:
    try:
        if HISTORY_PATH.exists():
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"closed_trades": []}


def save_history(data: dict):
    HISTORY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_close(name: str, ticker: str, qty: int, entry_price: float,
                 exit_price: float, fees: float = 0.0) -> float:
    """Enregistre un trade clôturé (+ contexte d'entrée et post-mortem) et
    retourne le P&L net."""
    import portfolio
    data = load_history()
    pnl = round((exit_price - entry_price) * qty - fees, 2)
    result = "win" if pnl > 0 else "loss"

    # Brique 2 : récupère le POURQUOI de l'entrée et en tire des leçons.
    ctx = portfolio.get_entry_context(ticker) or portfolio.get_entry_context(name)
    try:
        import lessons
        tags = lessons.post_mortem(ctx, entry_price, exit_price, result)
    except Exception:
        tags = []

    record = {
        "name":         name,
        "ticker":       ticker,
        "qty":          qty,
        "entry_price":  entry_price,
        "exit_price":   exit_price,
        "fees":         fees,
        "pnl":          pnl,
        "result":       result,
        "date":         datetime.now(PARIS).strftime("%Y-%m-%d"),
        "source":       ctx.get("source", "inconnu"),
        "entry_context": ctx,
        "lessons":      tags,
    }
    data["closed_trades"].append(record)
    save_history(data)
    portfolio.clear_entry_context(ticker)
    portfolio.clear_entry_context(name)
    return pnl


def get_stats() -> dict:
    history = load_history()
    closed = history["closed_trades"]

    wins   = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]

    realized_pnl  = sum(t["pnl"] for t in closed)
    win_rate      = (len(wins) / len(closed) * 100) if closed else 0
    avg_win       = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss      = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    gross_wins    = sum(t["pnl"] for t in wins)
    gross_losses  = abs(sum(t["pnl"] for t in losses))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None
    best  = max(closed, key=lambda t: t["pnl"]) if closed else None
    worst = min(closed, key=lambda t: t["pnl"]) if closed else None

    # P&L latent des positions ouvertes GÉRÉES par le bot. Les positions HOLD
    # long terme (hold: true, ex ILMN) sont hors périmètre trading : leur
    # latent n'entre pas dans le bilan du bot.
    unrealized_pnl = 0.0
    positions = portfolio.get_managed_positions()
    for cfg in positions.values():
        q = prices.get_quote(cfg["ticker"])
        price = q.get("price")
        if price:
            unrealized_pnl += (price - cfg["entry_price"]) * cfg["qty"]

    # Coûts API IA — 2e charge réelle après les frais de courtage (déjà déduits
    # par trade). Sans eux, le bilan surestime l'efficacité du bot.
    try:
        import api_costs
        costs = api_costs.get_costs()
        api_cost_eur = costs["total_eur"]
        api_month_eur = costs["month_eur"]
    except Exception:
        api_cost_eur, api_month_eur = 0.0, 0.0

    total_pnl = round(realized_pnl + unrealized_pnl, 2)
    return {
        "nb_closed":      len(closed),
        "nb_wins":        len(wins),
        "nb_losses":      len(losses),
        "win_rate":       round(win_rate, 1),
        "realized_pnl":   round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl":      total_pnl,
        "api_cost_eur":   api_cost_eur,
        "api_month_eur":  api_month_eur,
        "net_pnl":        round(total_pnl - api_cost_eur, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  profit_factor,
        "best_trade":     best,
        "worst_trade":    worst,
    }
