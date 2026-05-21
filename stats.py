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
    """Enregistre un trade clôturé et retourne le P&L net."""
    data = load_history()
    pnl = round((exit_price - entry_price) * qty - fees, 2)
    data["closed_trades"].append({
        "name":         name,
        "ticker":       ticker,
        "qty":          qty,
        "entry_price":  entry_price,
        "exit_price":   exit_price,
        "fees":         fees,
        "pnl":          pnl,
        "result":       "win" if pnl > 0 else "loss",
        "date":         datetime.now(PARIS).strftime("%Y-%m-%d"),
    })
    save_history(data)
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

    # P&L latent des positions ouvertes
    unrealized_pnl = 0.0
    positions = portfolio.get_positions()
    for cfg in positions.values():
        q = prices.get_quote(cfg["ticker"])
        price = q.get("price")
        if price:
            unrealized_pnl += (price - cfg["entry_price"]) * cfg["qty"]

    return {
        "nb_closed":      len(closed),
        "nb_wins":        len(wins),
        "nb_losses":      len(losses),
        "win_rate":       round(win_rate, 1),
        "realized_pnl":   round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl":      round(realized_pnl + unrealized_pnl, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  profit_factor,
        "best_trade":     best,
        "worst_trade":    worst,
    }
