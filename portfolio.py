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


def mark_sl_breach(name: str):
    """Marque qu'une alerte SL dépassé a déjà été envoyée — empêche le spam."""
    data = load()
    if name.upper() in data.get("positions", {}):
        data["positions"][name.upper()]["sl_breach_notified"] = True
        save(data)


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
