import json
import csv
import io
from config import POSITIONS_PATH


def load() -> dict:
    try:
        return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"cash_available": 0, "positions": {}, "pending_orders": {}}


def save(data: dict):
    POSITIONS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_positions() -> dict:
    return load().get("positions", {})


def get_cash() -> float:
    return load().get("cash_available", 0)


def add_position(name: str, ticker: str, qty: int, entry_price: float, sl: float, tp: float):
    data = load()
    data.setdefault("positions", {})[name.upper()] = {
        "ticker": ticker,
        "qty": qty,
        "entry_price": round(entry_price, 4),
        "target_high": round(tp, 4),
        "target_low": round(sl, 4),
    }
    save(data)


def remove_position(name: str):
    data = load()
    data.get("positions", {}).pop(name.upper(), None)
    data.get("pending_orders", {}).pop(name.upper(), None)
    save(data)


def add_pending_order(name: str, ticker: str, qty: int, entry_price: float,
                      sl: float, tp: float) -> float:
    """Enregistre un ordre en attente et réserve le cash. Retourne le cash réservé."""
    from datetime import datetime
    data = load()
    reserved = round(entry_price * qty, 2)
    data.setdefault("pending_orders", {})[name.upper()] = {
        "ticker":        ticker,
        "qty":           qty,
        "entry_price":   round(entry_price, 4),
        "target_low":    round(sl, 4),
        "target_high":   round(tp, 4),
        "reserved_cash": reserved,
        "created_at":    datetime.now().strftime("%Y-%m-%d"),
    }
    data["cash_available"] = round(data.get("cash_available", 0) - reserved, 2)
    save(data)
    return reserved


def cancel_pending_order(name: str) -> float:
    """Annule un ordre en attente et libère le cash réservé. Retourne le cash libéré."""
    data = load()
    order = data.get("pending_orders", {}).pop(name.upper(), None)
    if order:
        released = order.get("reserved_cash", 0)
        data["cash_available"] = round(data.get("cash_available", 0) + released, 2)
        save(data)
        return released
    return 0.0


def get_pending_orders() -> dict:
    return load().get("pending_orders", {})


def update_cash(amount: float):
    data = load()
    data["cash_available"] = round(amount, 2)
    save(data)


def update_sl(name: str, price: float):
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["target_low"] = round(price, 4)
        save(data)
        return True
    return False


def mark_gmail_triggered(name: str, strategy: str):
    """Marque qu'une notif Gmail 'Déclenchement' a été envoyée — évite le doublon."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["gmail_triggered"] = strategy
        save(data)


def clear_gmail_triggered(name: str):
    data = load()
    pos = data.get("positions", {}).get(name.upper(), {})
    pos.pop("gmail_triggered", None)
    save(data)


def is_gmail_triggered(name: str) -> bool:
    return bool(load().get("positions", {}).get(name.upper(), {}).get("gmail_triggered"))


def mark_sl_breach(name: str):
    """Marque qu'une alerte SL dépassé a déjà été envoyée — empêche le spam."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["sl_breach_notified"] = True
        save(data)


def mark_tp_breach(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte TP atteint — une seule notif par franchissement."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["tp_breach_notified"] = notified
        save(data)


def mark_sl_proche(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte SL proche — évite le spam à chaque check."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["sl_proche_notified"] = notified
        save(data)


def mark_breakeven(name: str, notified: bool = True):
    """Marque/réinitialise l'alerte trailing stop breakeven — une notif par épisode."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["breakeven_notified"] = notified
        save(data)


def update_sl(name: str, price: float):
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["target_low"] = round(price, 4)
        save(data)
        return True
    return False


def update_tp(name: str, price: float):
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["target_high"] = round(price, 4)
        save(data)
        return True
    return False


def import_from_csv(csv_content: str) -> list:
    """
    Parse a Bourse Direct portfolio CSV export.
    Bourse Direct uses semicolon separator and French headers.
    Returns list of dicts: {name, qty, pru}
    """
    positions = []
    # Try semicolon first, then comma
    for delimiter in (";", ","):
        try:
            reader = csv.DictReader(io.StringIO(csv_content), delimiter=delimiter)
            rows = list(reader)
            if not rows:
                continue
            for row in rows:
                # Normalize header variants from Bourse Direct exports
                name = (
                    row.get("Libellé") or row.get("Valeur") or
                    row.get("Titre") or row.get("Instrument", "")
                ).strip()
                raw_qty = row.get("Quantité") or row.get("Qté") or row.get("Nb titres", "0")
                raw_pru = row.get("PRU") or row.get("Prix de revient unitaire", "0")
                try:
                    qty = int(str(raw_qty).replace(" ", "").replace("\xa0", "").replace(",", ""))
                    pru = float(str(raw_pru).replace(",", ".").replace(" ", "").replace("\xa0", ""))
                except ValueError:
                    continue
                if name and qty > 0 and pru > 0:
                    positions.append({"name": name, "qty": qty, "pru": pru})
            if positions:
                break
        except Exception:
            continue
    return positions
