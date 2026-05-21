"""
Generateur d'instructions d'ordres Bourse Direct.
Produit des messages step-by-step optimisés mobile/iPhone.
"""
from dataclasses import dataclass, field
from enum import Enum
from config import DEFAULT_SL_PCT, DEFAULT_TP_PCT


class Side(Enum):
    BUY = "Achat"
    SELL = "Vente"


class OType(Enum):
    MARKET = "Au marché"
    LIMIT = "À cours limité"
    STOP_MARKET = "À seuil de déclenchement"   # stop → market (exécution garantie)
    STOP_LIMIT = "À plage de déclenchement"    # stop → limit (prix garanti, fill non garanti)


@dataclass
class Order:
    ticker: str
    side: Side
    qty: int
    otype: OType
    price: float | None = None        # prix limite
    trigger: float | None = None      # seuil de déclenchement
    limit_floor: float | None = None  # limite après déclenchement (stop-limit only)
    isin: str | None = None
    account: str = "CTO"
    validity: str = "Durée max"


def _format(order: Order) -> str:
    icon = "🟢" if order.side == Side.BUY else "🔴"
    lines = [
        f"{icon} ORDRE {order.side.value.upper()} — Bourse Direct",
        "━" * 34,
        f"📌 Valeur    : {order.ticker}" + (f"  ({order.isin})" if order.isin else ""),
        f"💼 Compte    : {order.account}",
        f"📊 Sens      : {order.side.value}",
        f"🔢 Quantité  : {order.qty} titres",
        f"⚙️  Type      : {order.otype.value}",
    ]
    if order.otype == OType.LIMIT:
        lines.append(f"💰 Limite    : {order.price} €")
    elif order.otype == OType.STOP_MARKET:
        lines.append(f"🎯 Seuil     : {order.trigger} €")
    elif order.otype == OType.STOP_LIMIT:
        lines.append(f"🎯 Seuil     : {order.trigger} €")
        lines.append(f"💰 Limite    : {order.limit_floor} €")
    lines += [
        f"📅 Validité  : {order.validity}",
        "━" * 34,
        "👆 Bourse Direct → Passer un ordre",
    ]
    return "\n".join(lines)


# ─── Helpers publics ────────────────────────────────────────────────────────

def stop_loss(ticker: str, qty: int, sl_price: float, isin: str = None, account: str = "CTO") -> str:
    """Ordre stop-loss : seuil de déclenchement → vente au marché."""
    return _format(Order(
        ticker=ticker, side=Side.SELL, qty=qty,
        otype=OType.STOP_MARKET, trigger=sl_price,
        isin=isin, account=account,
    ))


def take_profit(ticker: str, qty: int, tp_price: float, isin: str = None, account: str = "CTO") -> str:
    """Ordre take-profit : vente à cours limité."""
    return _format(Order(
        ticker=ticker, side=Side.SELL, qty=qty,
        otype=OType.LIMIT, price=tp_price,
        isin=isin, account=account,
    ))


def buy_limit(ticker: str, qty: int, price: float, isin: str = None, account: str = "CTO") -> str:
    """Ordre d'achat à cours limité."""
    return _format(Order(
        ticker=ticker, side=Side.BUY, qty=qty,
        otype=OType.LIMIT, price=price,
        isin=isin, account=account,
    ))


def sl_from_pru(ticker: str, qty: int, pru: float, pct: float = None) -> str:
    """Calcule et formate un SL à partir du PRU."""
    pct = pct or DEFAULT_SL_PCT
    sl_price = round(pru * (1 - pct / 100), 2)
    return stop_loss(ticker, qty, sl_price)


def tp_from_pru(ticker: str, qty: int, pru: float, pct: float = None) -> str:
    """Calcule et formate un TP à partir du PRU."""
    pct = pct or DEFAULT_TP_PCT
    tp_price = round(pru * (1 + pct / 100), 2)
    return take_profit(ticker, qty, tp_price)


def full_setup(ticker: str, qty: int, pru: float, sl_pct: float = None, tp_pct: float = None) -> str:
    """Retourne les 2 ordres à passer après un achat (SL + TP)."""
    sl_pct = sl_pct or DEFAULT_SL_PCT
    tp_pct = tp_pct or DEFAULT_TP_PCT
    sl_price = round(pru * (1 - sl_pct / 100), 2)
    tp_price = round(pru * (1 + tp_pct / 100), 2)
    return (
        f"📋 ORDRES À PASSER APRÈS ACHAT {ticker}\n"
        f"PRU: {pru}€ | SL: {sl_price}€ (-{sl_pct}%) | TP: {tp_price}€ (+{tp_pct}%)\n\n"
        + stop_loss(ticker, qty, sl_price)
        + "\n\n"
        + take_profit(ticker, qty, tp_price)
    )
