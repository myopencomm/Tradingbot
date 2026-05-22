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
    """Retourne les 2 ordres de protection à saisir après un achat (SL + TP)."""
    sl_pct = sl_pct or DEFAULT_SL_PCT
    tp_pct = tp_pct or DEFAULT_TP_PCT
    sl_price = round(pru * (1 - sl_pct / 100), 2)
    tp_price = round(pru * (1 + tp_pct / 100), 2)
    sep = "━" * 34
    sl_block = (
        f"— ORDRE 1 : STOP-LOSS —\n"
        f"🔴 Vente à seuil de déclenchement\n"
        f"Valeur : {ticker}  |  Quantité : {qty} titres\n"
        f"Seuil  : {sl_price}€  |  Validité : Durée max\n"
        f"App  → Bourse → Passer un ordre → Seuil\n"
        f"Site → Passer un ordre → À seuil de déclenchement"
    )
    tp_block = (
        f"— ORDRE 2 : TAKE-PROFIT —\n"
        f"🔴 Vente à cours limité\n"
        f"Valeur : {ticker}  |  Quantité : {qty} titres\n"
        f"Limite : {tp_price}€  |  Validité : Durée max\n"
        f"App  → Bourse → Passer un ordre → Limité\n"
        f"Site → Passer un ordre → À cours limité"
    )
    return (
        f"📋 PROTECTION — {ticker}\n"
        f"PRU {pru}€  |  SL {sl_price}€ (-{sl_pct}%)  |  TP {tp_price}€ (+{tp_pct}%)\n"
        f"{sep}\n"
        f"Saisis ces 2 ordres maintenant → ils s'exécutent\n"
        f"automatiquement sans surveillance.\n\n"
        f"{sl_block}\n\n"
        f"{tp_block}\n"
        f"{sep}\n"
        f"💡 Quand l'un s'exécute → annuler l'autre sur Bourse Direct\n"
        f"   puis /close {ticker} PRIX_VENTE pour mettre à jour le bot."
    )


def expert_take_profit_buy(ticker: str, qty: int, buy_price: float, sl_pct: float = None, tp_pct: float = None, account: str = "CTO") -> str:
    """Ordre Expert Take Profit : achat + SL + TP en une seule opération (OCO natif Bourse Direct)."""
    sl_pct = sl_pct or DEFAULT_SL_PCT
    tp_pct = tp_pct or DEFAULT_TP_PCT
    sl_price = round(buy_price * (1 - sl_pct / 100), 2)
    tp_price = round(buy_price * (1 + tp_pct / 100), 2)
    sep = "━" * 34
    return (
        f"🟢 ORDRE EXPERT — ACHAT + PROTECTION AUTOMATIQUE\n"
        f"{ticker}  |  {qty} titres  |  PRU {buy_price}€\n"
        f"{sep}\n"
        f"App  → Bourse → Passer un ordre → EXPERTS → Take Profit\n"
        f"Site → Ordres → Passer un ordre → Ordres Experts → Take Profit\n"
        f"{sep}\n"
        f"📌 Valeur      : {ticker}\n"
        f"💼 Compte      : {account}\n"
        f"📊 Sens        : Achat\n"
        f"🔢 Quantité    : {qty} titres\n"
        f"💰 Achat       : {buy_price}€  (cours limité)\n"
        f"🛡 Protection  : {sl_price}€  (seuil SL -{sl_pct}%)\n"
        f"🎯 Objectif    : {tp_price}€  (limite TP +{tp_pct}%)\n"
        f"📅 Validité    : Durée max\n"
        f"{sep}\n"
        f"⚡ Si l'achat s'exécute → SL et TP actifs automatiquement.\n"
        f"   Bourse Direct annule l'un quand l'autre s'exécute.\n\n"
        f"⚠️ Ordres Experts = SBF 120 et certains ETFs uniquement.\n"
        f"   Si non disponible pour ce titre → /setup {ticker} {qty} {buy_price}"
    )
