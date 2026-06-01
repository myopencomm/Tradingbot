"""
Sync Gmail IMAP → portfolio : détecte les emails "Finalisation de votre stratégie"
de Bourse Direct et clôture automatiquement les positions correspondantes.
Nécessite GMAIL_USER + GMAIL_APP_PASSWORD dans .env.
"""
import imaplib
import email
import re
from email.header import decode_header
from html.parser import HTMLParser

import portfolio
import prices
import stats


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
BD_SENDER = "boursedirect.fr"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    p = _HTMLTextExtractor()
    p.feed(html)
    return p.get_text()


def _decode_header_str(raw) -> str:
    parts = decode_header(raw or "")
    return "".join(
        chunk.decode(enc or "utf-8") if isinstance(chunk, bytes) else (chunk or "")
        for chunk, enc in parts
    )


def _get_text_body(msg) -> str:
    for part in msg.walk():
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            return text
        if ctype == "text/html":
            return _strip_html(text)
    return ""


def _parse_finalisation(body: str) -> dict | None:
    """Extrait strategy_type et company_name depuis le corps de l'email."""
    m = re.search(
        r"strat[eé]gie\s+(take_profit|stop_loss)\s+sur\s+la\s+valeur\s+(.+?)\s+a\s+[eé]t[eé]\s+finalis[eé]e",
        body,
        re.IGNORECASE,
    )
    if m:
        return {"strategy_type": m.group(1), "company_name": m.group(2).strip()}
    return None


def _match_position(company_name: str) -> str | None:
    """Trouve le nom de position le plus proche du nom de société reçu par email."""
    import yfinance as yf
    cn = company_name.lower().replace(" ", "").replace("-", "")
    positions = portfolio.get_positions()

    # 1. Matching direct sur le nom de position
    for name in positions:
        if name.lower() in cn or cn in name.lower():
            return name

    # 2. Matching via yfinance shortName / longName
    for name, cfg in positions.items():
        try:
            info = yf.Ticker(cfg["ticker"]).fast_info
            long_name = (getattr(info, "company_name", "") or "").lower().replace(" ", "").replace("-", "")
            if long_name and (long_name in cn or cn in long_name or cn[:6] in long_name):
                return name
        except Exception:
            pass

    return None


def _exit_price_for(cfg: dict, strategy_type: str) -> float | None:
    """Prix de sortie : TP ou SL posé (plus précis qu'un cours live post-exécution)."""
    if strategy_type == "take_profit":
        return cfg.get("target_high")
    if strategy_type == "stop_loss":
        return cfg.get("target_low")
    quote = prices.get_quote(cfg["ticker"])
    return quote.get("price")


def check_and_sync(gmail_user: str, gmail_password: str) -> list[dict]:
    """
    Vérifie la boîte Gmail et clôture les positions dont la stratégie est finalisée.
    Retourne la liste des opérations effectuées.
    """
    results = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(gmail_user, gmail_password)
        mail.select("INBOX")

        _, msg_ids = mail.search(None, f'(UNSEEN FROM "{BD_SENDER}")')
        ids = msg_ids[0].split() if msg_ids[0] else []

        for msg_id in ids:
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _decode_header_str(msg.get("Subject", ""))
                if "finalisation" not in subject.lower():
                    continue

                body = _get_text_body(msg)
                order = _parse_finalisation(body)
                if not order:
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    continue

                company  = order["company_name"]
                strategy = order["strategy_type"]
                pos_name = _match_position(company)

                if not pos_name:
                    results.append({"status": "no_match", "company": company, "strategy": strategy})
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    continue

                cfg        = portfolio.get_positions()[pos_name]
                exit_price = _exit_price_for(cfg, strategy)

                if not exit_price:
                    results.append({"status": "no_price", "company": company, "position": pos_name})
                    continue

                pnl     = stats.record_close(pos_name, cfg["ticker"], cfg["qty"], cfg["entry_price"], exit_price)
                proceeds = round(exit_price * cfg["qty"], 2)
                portfolio.remove_position(pos_name)
                portfolio.update_cash(round(portfolio.get_cash() + proceeds, 2))
                mail.store(msg_id, "+FLAGS", "\\Seen")

                pct = ((exit_price - cfg["entry_price"]) / cfg["entry_price"]) * 100
                results.append({
                    "status":      "closed",
                    "company":     company,
                    "position":    pos_name,
                    "strategy":    strategy,
                    "exit_price":  exit_price,
                    "entry_price": cfg["entry_price"],
                    "qty":         cfg["qty"],
                    "pnl":         pnl,
                    "pct":         pct,
                })

            except Exception as e:
                results.append({"status": "error", "error": str(e)})

        mail.logout()

    except Exception as e:
        results.append({"status": "connect_error", "error": str(e)})

    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "Aucun ordre Bourse Direct en attente."
    lines = []
    for r in results:
        if r["status"] == "closed":
            tag = "WIN" if r["pnl"] > 0 else "LOSS"
            lines.append(
                f"Trade cloture via Gmail — {r['position']} {tag}\n"
                f"  {r['qty']}t @ {r['exit_price']}€  (PRU {r['entry_price']}€)\n"
                f"  P&L : {r['pnl']:+.0f}€  ({r['pct']:+.1f}%)\n"
                f"  Strategie : {r['strategy']}"
            )
        elif r["status"] == "no_match":
            lines.append(
                f"Email BD recu : {r['company']} ({r['strategy']}) — "
                f"position non trouvee. Utilise /vendu NOM PRIX"
            )
        elif r["status"] == "no_price":
            lines.append(
                f"Prix indisponible pour {r['position']}. "
                f"Utilise /vendu {r['position']} PRIX"
            )
        elif r["status"] in ("error", "connect_error"):
            lines.append(f"Erreur Gmail : {r.get('error', '')}")
    return "\n\n".join(lines)
